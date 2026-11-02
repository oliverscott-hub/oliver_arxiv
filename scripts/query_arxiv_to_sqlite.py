import argparse
import email.utils
import json
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, time as datetime_time, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "arxiv_queries.sqlite"
API_URL = "https://export.arxiv.org/api/query"
ATOM = "http://www.w3.org/2005/Atom"
OPEN_SEARCH = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV = "http://arxiv.org/schemas/atom"
CATEGORY = "astro-ph.CO"
MAX_RESULTS_PER_PAGE = 2000
MAX_RESULTS_PER_QUERY = 30000
MIN_REQUEST_INTERVAL_SECONDS = 3.2
MAX_RETRIES = 3
USER_AGENT = (
    "oliver_arxiv-date-query/1.0 "
    "(metadata-only; https://github.com/oliverscott-hub/oliver_arxiv)"
)


class ApiRequestError(RuntimeError):
    def __init__(self, message, http_status=None, requested_at_utc=None):
        super().__init__(message)
        self.http_status = http_status
        self.requested_at_utc = requested_at_utc


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_iso_utc(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return iso_utc(parsed)


def normalize_text(value):
    return " ".join((value or "").split())


def text_of(parent, tag, namespace=ATOM):
    node = parent.find(f"{{{namespace}}}{tag}")
    if node is None:
        return None
    return normalize_text("".join(node.itertext()))


def build_query(from_day, to_day):
    start = datetime.combine(from_day, datetime_time.min, tzinfo=timezone.utc)
    end = datetime.combine(to_day, datetime_time(23, 59), tzinfo=timezone.utc)
    query = (
        f"cat:{CATEGORY} AND submittedDate:"
        f"[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]"
    )
    return query, start, end


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def init_db(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS query_runs (
            query_run_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            segment_id TEXT NOT NULL,
            from_day TEXT NOT NULL,
            to_day TEXT NOT NULL,
            query_string TEXT NOT NULL,
            api_url TEXT NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            total_results INTEGER,
            items_per_page INTEGER NOT NULL,
            start_index INTEGER NOT NULL,
            sort_by TEXT NOT NULL,
            sort_order TEXT NOT NULL,
            query_status TEXT,
            query_http_status INTEGER,
            response_status TEXT NOT NULL,
            http_status INTEGER,
            response_count INTEGER NOT NULL DEFAULT 0,
            pages_fetched INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS query_pages (
            query_run_id TEXT NOT NULL,
            page_number INTEGER NOT NULL,
            start_index INTEGER NOT NULL,
            fetched_at_utc TEXT NOT NULL,
            max_results_requested INTEGER NOT NULL,
            total_results INTEGER,
            items_per_page INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            http_status INTEGER,
            count INTEGER NOT NULL DEFAULT 0,
            retry_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            PRIMARY KEY (query_run_id, page_number),
            FOREIGN KEY (query_run_id) REFERENCES query_runs(query_run_id)
        );

        CREATE TABLE IF NOT EXISTS entries (
            query_run_id TEXT NOT NULL,
            id TEXT NOT NULL,
            version INTEGER,
            updated TEXT,
            published TEXT,
            title TEXT NOT NULL,
            summary TEXT,
            abstract_length INTEGER,
            abstract_status TEXT,
            author_json TEXT NOT NULL,
            link_json TEXT NOT NULL,
            category_json TEXT NOT NULL,
            arxiv_primary_category_json TEXT,
            arxiv_comment TEXT,
            arxiv_status_comment TEXT,
            arxiv_journal_ref TEXT,
            arxiv_doi TEXT,
            source_json TEXT NOT NULL,
            PRIMARY KEY (query_run_id, id),
            FOREIGN KEY (query_run_id) REFERENCES query_runs(query_run_id)
        );

        CREATE INDEX IF NOT EXISTS idx_entries_id ON entries(id);
        CREATE INDEX IF NOT EXISTS idx_entries_published ON entries(published);
        """
    )
    connection.commit()


def retry_delay(retry_after, attempt):
    if retry_after:
        try:
            return max(MIN_REQUEST_INTERVAL_SECONDS, float(retry_after))
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(MIN_REQUEST_INTERVAL_SECONDS, (retry_at - utc_now()).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return MIN_REQUEST_INTERVAL_SECONDS * (2**attempt)


class PoliteClient:
    def __init__(self):
        self.last_request_finished = None

    def fetch(self, params):
        url = API_URL + "?" + urllib.parse.urlencode(params)
        for attempt in range(MAX_RETRIES + 1):
            if self.last_request_finished is not None:
                wait = MIN_REQUEST_INTERVAL_SECONDS - (
                    time.monotonic() - self.last_request_finished
                )
                if wait > 0:
                    time.sleep(wait)

            requested_at_utc = iso_utc(utc_now())
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = response.read()
                    http_status = response.getcode()
                self.last_request_finished = time.monotonic()
                root = ET.fromstring(payload)
                return root, http_status, requested_at_utc, attempt
            except urllib.error.HTTPError as exc:
                self.last_request_finished = time.monotonic()
                retryable = exc.code == 429 or 500 <= exc.code < 600
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                if not retryable or attempt >= MAX_RETRIES:
                    raise ApiRequestError(
                        f"arXiv HTTP {exc.code}: {detail}", exc.code, requested_at_utc
                    ) from exc
                time.sleep(retry_delay(exc.headers.get("Retry-After"), attempt))
            except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
                self.last_request_finished = time.monotonic()
                if attempt >= MAX_RETRIES:
                    raise ApiRequestError(
                        f"No se pudo recuperar una respuesta válida: {exc}",
                        None,
                        requested_at_utc,
                    ) from exc
                time.sleep(MIN_REQUEST_INTERVAL_SECONDS * (2**attempt))


def api_error_message(root):
    for entry in root.findall(f"{{{ATOM}}}entry"):
        entry_id = text_of(entry, "id") or ""
        if "/api/errors#" in entry_id:
            return text_of(entry, "summary") or text_of(entry, "title") or "Error Atom de arXiv"
    return None


def atom_links(parent):
    result = []
    for link in parent.findall(f"{{{ATOM}}}link"):
        href = link.get("href")
        if href:
            result.append(
                {
                    "href": href,
                    "rel": link.get("rel"),
                    "type": link.get("type"),
                    "title": link.get("title"),
                }
            )
    return result


def atom_categories(parent):
    return [
        {
            "term": category.get("term"),
            "scheme": category.get("scheme"),
            "label": category.get("label"),
        }
        for category in parent.findall(f"{{{ATOM}}}category")
        if category.get("term")
    ]


def parse_entry(entry):
    id_url = text_of(entry, "id") or ""
    match = re.search(r"/abs/([^?#]+)", id_url)
    if not match:
        return None
    raw_id = match.group(1)
    version_match = re.search(r"v(\d+)$", raw_id)
    version = int(version_match.group(1)) if version_match else None
    arxiv_id = re.sub(r"v\d+$", "", raw_id)

    links = atom_links(entry)
    if version is None:
        for link in links:
            if link.get("rel") == "alternate":
                alt_match = re.search(r"/abs/[^?#]+v(\d+)(?:[?#]|$)", link["href"])
                if alt_match:
                    version = int(alt_match.group(1))
                    break

    authors = []
    for author in entry.findall(f"{{{ATOM}}}author"):
        name = text_of(author, "name")
        if not name:
            continue
        affiliation = text_of(author, "affiliation", ARXIV)
        author_record = {"name": name}
        if affiliation:
            author_record["affiliation"] = affiliation
        authors.append(author_record)

    primary_node = entry.find(f"{{{ARXIV}}}primary_category")
    primary_category = None
    if primary_node is not None:
        primary_category = {
            "term": primary_node.get("term"),
            "scheme": primary_node.get("scheme"),
            "label": primary_node.get("label"),
        }

    summary = text_of(entry, "summary")
    abstract = {
        "length": len(summary) if summary is not None else None,
        "status": "ok" if summary else "unusable",
    }
    source = [{"href": id_url, "rel": None, "type": None, "title": None}]

    return {
        "id": arxiv_id,
        "version": version,
        "updated": parse_iso_utc(text_of(entry, "updated")),
        "published": parse_iso_utc(text_of(entry, "published")),
        "title": text_of(entry, "title") or "",
        "summary": summary,
        "abstract": abstract,
        "author": authors,
        "link": links,
        "category": atom_categories(entry),
        "arxiv_primary_category": primary_category,
        "arxiv_comment": text_of(entry, "comment", ARXIV),
        # arXiv comment is free text, not a structured publication-status field.
        "arxiv_status_comment": None,
        "arxiv_journal_ref": text_of(entry, "journal_ref", ARXIV),
        "arxiv_doi": text_of(entry, "doi", ARXIV),
        "source": source,
    }


def save_query_run(connection, run_id, from_day, to_day, query_string, fetched_at_utc):
    connection.execute(
        """
        INSERT INTO query_runs (
            query_run_id, source, segment_id, from_day, to_day, query_string,
            api_url, fetched_at_utc, completed_at_utc, total_results,
            items_per_page, start_index, sort_by, sort_order, query_status,
            query_http_status, response_status, http_status
        ) VALUES (?, 'arxiv', ?, ?, ?, ?, ?, ?, NULL, NULL, ?, 0,
                  'submittedDate', 'descending', NULL, NULL, 'error', NULL)
        """,
        (
            run_id,
            CATEGORY,
            from_day.isoformat(),
            to_day.isoformat(),
            query_string,
            API_URL,
            fetched_at_utc,
            MAX_RESULTS_PER_PAGE,
        ),
    )
    connection.commit()


def save_entry(connection, run_id, entry):
    connection.execute(
        """
        INSERT OR REPLACE INTO entries (
            query_run_id, id, version, updated, published, title, summary,
            abstract_length, abstract_status, author_json, link_json,
            category_json, arxiv_primary_category_json, arxiv_comment,
            arxiv_status_comment, arxiv_journal_ref, arxiv_doi, source_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            entry["id"],
            entry["version"],
            entry["updated"],
            entry["published"],
            entry["title"],
            entry["summary"],
            entry["abstract"]["length"],
            entry["abstract"]["status"],
            json_text(entry["author"]),
            json_text(entry["link"]),
            json_text(entry["category"]),
            json_text(entry["arxiv_primary_category"])
            if entry["arxiv_primary_category"] is not None
            else None,
            entry["arxiv_comment"],
            entry["arxiv_status_comment"],
            entry["arxiv_journal_ref"],
            entry["arxiv_doi"],
            json_text(entry["source"]),
        ),
    )


def update_run(connection, run_id, **fields):
    assignments = ", ".join(f"{name} = ?" for name in fields)
    values = list(fields.values()) + [run_id]
    connection.execute(
        f"UPDATE query_runs SET {assignments} WHERE query_run_id = ?", values
    )
    connection.commit()


def run_query(from_day, to_day):
    query_string, _, _ = build_query(from_day, to_day)
    run_id = str(uuid.uuid4())
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    init_db(connection)
    fetched_at_utc = iso_utc(utc_now())
    save_query_run(connection, run_id, from_day, to_day, query_string, fetched_at_utc)

    client = PoliteClient()
    start_index = 0
    page_number = 0
    total_results = None
    total_captured = 0
    last_http_status = None
    try:
        while True:
            params = {
                "search_query": query_string,
                "start": start_index,
                "max_results": MAX_RESULTS_PER_PAGE,
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            try:
                root, http_status, requested_at_utc, retry_count = client.fetch(params)
                last_http_status = http_status
            except ApiRequestError as exc:
                page_number += 1
                requested_at = exc.requested_at_utc or iso_utc(utc_now())
                status = "rate_limited" if exc.http_status == 429 else "error"
                connection.execute(
                    """
                    INSERT INTO query_pages (
                        query_run_id, page_number, start_index, fetched_at_utc,
                        max_results_requested, status, http_status, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        page_number,
                        start_index,
                        requested_at,
                        MAX_RESULTS_PER_PAGE,
                        status,
                        exc.http_status,
                        str(exc),
                    ),
                )
                connection.commit()
                update_run(
                    connection,
                    run_id,
                    completed_at_utc=iso_utc(utc_now()),
                    total_results=total_results,
                    query_status=status,
                    query_http_status=exc.http_status,
                    response_status=status,
                    http_status=exc.http_status,
                    response_count=total_captured,
                    pages_fetched=page_number,
                    error_message=str(exc),
                )
                raise

            page_number += 1
            atom_error = api_error_message(root)
            if atom_error:
                connection.execute(
                    """
                    INSERT INTO query_pages (
                        query_run_id, page_number, start_index, fetched_at_utc,
                        max_results_requested, total_results, status, http_status,
                        retry_count, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, 'error', ?, ?, ?)
                    """,
                    (
                        run_id,
                        page_number,
                        start_index,
                        requested_at_utc,
                        MAX_RESULTS_PER_PAGE,
                        total_results,
                        http_status,
                        retry_count,
                        atom_error,
                    ),
                )
                connection.commit()
                update_run(
                    connection,
                    run_id,
                    completed_at_utc=iso_utc(utc_now()),
                    total_results=total_results,
                    query_status="error",
                    query_http_status=http_status,
                    response_status="error",
                    http_status=http_status,
                    response_count=total_captured,
                    pages_fetched=page_number,
                    error_message=atom_error,
                )
                raise ApiRequestError(atom_error, http_status, requested_at_utc)

            total_text = root.findtext(f"{{{OPEN_SEARCH}}}totalResults", default="0")
            total_results = int(total_text)
            start_text = root.findtext(f"{{{OPEN_SEARCH}}}startIndex", default=str(start_index))
            returned_start = int(start_text)
            actual_start = returned_start if returned_start >= 0 else start_index
            page_entries = root.findall(f"{{{ATOM}}}entry")
            items_text = root.findtext(
                f"{{{OPEN_SEARCH}}}itemsPerPage", default=str(len(page_entries))
            )
            items_per_page_returned = int(items_text)
            if page_number == 1:
                update_run(connection, run_id, fetched_at_utc=requested_at_utc)
            if total_results > MAX_RESULTS_PER_QUERY:
                message = (
                    f"La consulta devolvió {total_results} resultados; el límite de una consulta "
                    f"lógica es {MAX_RESULTS_PER_QUERY}. Refina el intervalo."
                )
                connection.execute(
                    """
                    INSERT INTO query_pages (
                        query_run_id, page_number, start_index, fetched_at_utc,
                        max_results_requested, total_results, items_per_page,
                        status, http_status, retry_count, error_message
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'error', ?, ?, ?)
                    """,
                    (
                        run_id,
                        page_number,
                        actual_start,
                        requested_at_utc,
                        MAX_RESULTS_PER_PAGE,
                        total_results,
                        items_per_page_returned,
                        http_status,
                        retry_count,
                        message,
                    ),
                )
                connection.commit()
                update_run(
                    connection,
                    run_id,
                    completed_at_utc=iso_utc(utc_now()),
                    total_results=total_results,
                    query_status="error",
                    query_http_status=http_status,
                    response_status="error",
                    http_status=http_status,
                    response_count=total_captured,
                    pages_fetched=page_number,
                    error_message=message,
                )
                raise ApiRequestError(message, http_status, requested_at_utc)

            parsed_entries = [
                parsed
                for raw_entry in page_entries
                if (parsed := parse_entry(raw_entry)) is not None
            ]
            connection.execute(
                """
                INSERT INTO query_pages (
                    query_run_id, page_number, start_index, fetched_at_utc,
                    max_results_requested, total_results, items_per_page,
                    status, http_status, count, retry_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ok', ?, ?, ?)
                """,
                (
                    run_id,
                    page_number,
                    actual_start,
                    requested_at_utc,
                    MAX_RESULTS_PER_PAGE,
                    total_results,
                    items_per_page_returned,
                    http_status,
                    len(parsed_entries),
                    retry_count,
                ),
            )
            for entry in parsed_entries:
                save_entry(connection, run_id, entry)
            connection.commit()

            total_captured += len(parsed_entries)
            update_run(
                connection,
                run_id,
                total_results=total_results,
                query_http_status=http_status,
                http_status=http_status,
                response_count=total_captured,
                pages_fetched=page_number,
            )

            if not page_entries or actual_start + len(page_entries) >= total_results:
                break
            start_index = actual_start + len(page_entries)

        update_run(
            connection,
            run_id,
            completed_at_utc=iso_utc(utc_now()),
            query_status="ok",
            response_status="ok",
            http_status=last_http_status,
            response_count=total_captured,
            pages_fetched=page_number,
            error_message=None,
        )
        return {
            "query_run_id": run_id,
            "status": "ok",
            "category": CATEGORY,
            "from_day": from_day.isoformat(),
            "to_day": to_day.isoformat(),
            "query_string": query_string,
            "total_results": total_results,
            "response_count": total_captured,
            "pages_fetched": page_number,
            "database": str(DB_PATH.relative_to(ROOT)).replace("\\", "/"),
        }
    except Exception:
        connection.close()
        raise
    finally:
        try:
            connection.close()
        except sqlite3.Error:
            pass


def parse_day(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("La fecha debe tener formato YYYY-MM-DD") from exc


def main():
    parser = argparse.ArgumentParser(
        description="Consulta arXiv astro-ph.CO por fecha y guarda el resultado en SQLite."
    )
    parser.add_argument("--from-day", required=True, type=parse_day)
    parser.add_argument("--to-day", required=True, type=parse_day)
    args = parser.parse_args()
    if args.to_day < args.from_day:
        parser.error("--to-day debe ser igual o posterior a --from-day")

    try:
        result = run_query(args.from_day, args.to_day)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
