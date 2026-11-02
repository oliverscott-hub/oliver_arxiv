#!/usr/bin/env python3
"""Incrementally collect one arXiv category into a coverage-aware SQLite library."""

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
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_URL = "https://export.arxiv.org/api/query"
ATOM = "http://www.w3.org/2005/Atom"
OPEN_SEARCH = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV = "http://arxiv.org/schemas/atom"
CATEGORY = "astro-ph.CO"
DB_PATH = ROOT / "library" / "astro-ph_CO.sqlite"
COVERAGE_JSON_PATH = ROOT / "library" / "astro-ph_CO.coverage.json"
MIN_INTERVAL = 3.2
MAX_PER_PAGE = 2000
MAX_PER_QUERY = 30000
MAX_RETRIES = 3
MAX_DAYS_PER_RUN = 30
USER_AGENT = (
    "oliver-arxiv-category-collector/1.0 "
    "(metadata-only; https://github.com/oliverscott-hub/oliver_arxiv)"
)


class CollectionError(RuntimeError):
    pass


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_text(value):
    return " ".join((value or "").split())


def atom_text(parent, tag, namespace=ATOM):
    node = parent.find(f"{{{namespace}}}{tag}")
    return normalize_text("".join(node.itertext())) if node is not None else None


def build_query(day):
    start = datetime.combine(day, datetime_time.min, tzinfo=timezone.utc)
    end = datetime.combine(day, datetime_time(23, 59), tzinfo=timezone.utc)
    return (
        f"cat:{CATEGORY} AND submittedDate:"
        f"[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]"
    )


def retry_delay(retry_after, attempt):
    if retry_after:
        try:
            return max(MIN_INTERVAL, float(retry_after))
        except ValueError:
            try:
                retry_at = email.utils.parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(MIN_INTERVAL, (retry_at - utc_now()).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return MIN_INTERVAL * (2**attempt)


class PoliteClient:
    def __init__(self):
        self.last_finished = None

    def fetch(self, params):
        url = API_URL + "?" + urllib.parse.urlencode(params)
        for attempt in range(MAX_RETRIES + 1):
            if self.last_finished is not None:
                delay = MIN_INTERVAL - (time.monotonic() - self.last_finished)
                if delay > 0:
                    time.sleep(delay)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml"},
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    payload = response.read()
                    status = response.getcode()
                self.last_finished = time.monotonic()
                return ET.fromstring(payload), status, attempt
            except urllib.error.HTTPError as exc:
                self.last_finished = time.monotonic()
                detail = exc.read().decode("utf-8", errors="replace")[:1000]
                retryable = exc.code == 429 or 500 <= exc.code < 600
                if not retryable or attempt >= MAX_RETRIES:
                    raise CollectionError(f"arXiv HTTP {exc.code}: {detail}") from exc
                time.sleep(retry_delay(exc.headers.get("Retry-After"), attempt))
            except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
                self.last_finished = time.monotonic()
                if attempt >= MAX_RETRIES:
                    raise CollectionError(f"No se obtuvo una respuesta válida: {exc}") from exc
                time.sleep(MIN_INTERVAL * (2**attempt))


def api_error(root):
    for entry in root.findall(f"{{{ATOM}}}entry"):
        entry_id = atom_text(entry, "id") or ""
        if "/api/errors#" in entry_id:
            return atom_text(entry, "summary") or atom_text(entry, "title")
    return None


def parse_entry(entry):
    entry_id = atom_text(entry, "id") or ""
    match = re.search(r"/abs/([^?#]+)", entry_id)
    if not match:
        return None
    raw_id = match.group(1)
    version_match = re.search(r"v(\d+)$", raw_id)
    versioned_id = raw_id if version_match else None
    version = int(version_match.group(1)) if version_match else None
    if versioned_id is None:
        for link in entry.findall(f"{{{ATOM}}}link"):
            if link.get("rel") == "alternate":
                alt_match = re.search(r"/abs/([^/?#]+v(\d+))(?:[?#]|$)", link.get("href", ""))
                if alt_match:
                    versioned_id = alt_match.group(1)
                    version = int(alt_match.group(2))
                    break
    if versioned_id is None:
        raise CollectionError(f"No se pudo determinar la versión del ID {raw_id}")
    work_id = re.sub(r"v\d+$", "", versioned_id)
    summary = atom_text(entry, "summary") or ""
    authors = []
    for author in entry.findall(f"{{{ATOM}}}author"):
        name = atom_text(author, "name")
        if name:
            record = {"name": name}
            affiliation = atom_text(author, "affiliation", ARXIV)
            if affiliation:
                record["affiliation"] = affiliation
            authors.append(record)
    links = [
        {"href": link.get("href"), "rel": link.get("rel"), "type": link.get("type"), "title": link.get("title")}
        for link in entry.findall(f"{{{ATOM}}}link") if link.get("href")
    ]
    categories = [
        {"term": node.get("term"), "scheme": node.get("scheme"), "label": node.get("label")}
        for node in entry.findall(f"{{{ATOM}}}category") if node.get("term")
    ]
    primary = entry.find(f"{{{ARXIV}}}primary_category")
    primary_category = None if primary is None else {
        "term": primary.get("term"), "scheme": primary.get("scheme"), "label": primary.get("label")
    }
    return {
        "arxiv_id": versioned_id,
        "work_id": work_id,
        "version": version,
        "updated": atom_text(entry, "updated"),
        "published": atom_text(entry, "published"),
        "title": atom_text(entry, "title") or "",
        "summary": summary,
        "authors": authors,
        "links": links,
        "categories": categories,
        "primary_category": primary_category,
        "comment": atom_text(entry, "comment", ARXIV),
        "journal_ref": atom_text(entry, "journal_ref", ARXIV),
        "doi": atom_text(entry, "doi", ARXIV),
        "source_url": entry_id,
    }


def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def init_db(connection):
    connection.execute("PRAGMA foreign_keys=ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            work_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            updated TEXT,
            published TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            authors_json TEXT NOT NULL,
            links_json TEXT NOT NULL,
            categories_json TEXT NOT NULL,
            primary_category_json TEXT,
            comment TEXT,
            journal_ref TEXT,
            doi TEXT,
            source_url TEXT NOT NULL,
            first_seen_utc TEXT NOT NULL,
            last_seen_utc TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_papers_work_id ON papers(work_id);
        CREATE INDEX IF NOT EXISTS idx_papers_published ON papers(published);

        CREATE TABLE IF NOT EXISTS day_coverage (
            day_utc TEXT PRIMARY KEY,
            query_string TEXT,
            status TEXT NOT NULL CHECK(status IN ('ok','error')),
            api_total_results INTEGER,
            papers_captured INTEGER,
            successful_queries INTEGER NOT NULL DEFAULT 0,
            page_requests INTEGER NOT NULL DEFAULT 0,
            completed_at_utc TEXT,
            error_message TEXT
        );

        CREATE TABLE IF NOT EXISTS query_runs (
            run_id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            from_day_utc TEXT NOT NULL,
            to_day_utc TEXT NOT NULL,
            query_string TEXT NOT NULL,
            started_at_utc TEXT NOT NULL,
            completed_at_utc TEXT,
            status TEXT NOT NULL,
            api_total_results INTEGER,
            papers_captured INTEGER NOT NULL DEFAULT 0,
            page_requests INTEGER NOT NULL DEFAULT 0,
            error_message TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_query_runs_started ON query_runs(started_at_utc);

        CREATE TABLE IF NOT EXISTS collector_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
    """)
    connection.commit()


def upsert_paper(connection, paper, seen_at):
    connection.execute("""
        INSERT INTO papers (
            arxiv_id, work_id, version, updated, published, title, summary,
            authors_json, links_json, categories_json, primary_category_json,
            comment, journal_ref, doi, source_url, first_seen_utc, last_seen_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(arxiv_id) DO UPDATE SET
            work_id=excluded.work_id, version=excluded.version, updated=excluded.updated,
            published=excluded.published, title=excluded.title, summary=excluded.summary,
            authors_json=excluded.authors_json, links_json=excluded.links_json,
            categories_json=excluded.categories_json,
            primary_category_json=excluded.primary_category_json,
            comment=excluded.comment, journal_ref=excluded.journal_ref, doi=excluded.doi,
            source_url=excluded.source_url, last_seen_utc=excluded.last_seen_utc
    """, (
        paper["arxiv_id"], paper["work_id"], paper["version"], paper["updated"],
        paper["published"], paper["title"], paper["summary"], json_text(paper["authors"]),
        json_text(paper["links"]), json_text(paper["categories"]),
        json_text(paper["primary_category"]) if paper["primary_category"] else None,
        paper["comment"], paper["journal_ref"], paper["doi"], paper["source_url"], seen_at, seen_at,
    ))


def fetch_day(connection, client, day):
    query = build_query(day)
    start_index = 0
    total_results = None
    captured = 0
    requests = 0
    seen_at = iso_utc(utc_now())
    while True:
        root, http_status, retries = client.fetch({
            "search_query": query,
            "start": start_index,
            "max_results": MAX_PER_PAGE,
            "sortBy": "submittedDate",
            "sortOrder": "ascending",
        })
        requests += 1
        error = api_error(root)
        if error:
            raise CollectionError(f"Error Atom arXiv HTTP {http_status}: {error}")
        total_results = int(root.findtext(f"{{{OPEN_SEARCH}}}totalResults", default="0"))
        if total_results > MAX_PER_QUERY:
            raise CollectionError(
                f"El día UTC {day} devolvió {total_results}, por encima del máximo lógico "
                f"de {MAX_PER_QUERY}. No se divide el día ni se marca como cubierto."
            )
        entries = root.findall(f"{{{ATOM}}}entry")
        for raw in entries:
            paper = parse_entry(raw)
            if paper is None:
                raise CollectionError("Entrada Atom sin identificador arXiv válido")
            upsert_paper(connection, paper, seen_at)
            captured += 1
        connection.commit()
        if not entries or start_index + len(entries) >= total_results:
            break
        start_index += len(entries)
    if captured != total_results:
        raise CollectionError(
            f"Paginación incompleta para {day}: arXiv informó {total_results}, "
            f"pero se capturaron {captured}. El día no se marcará cubierto."
        )
    return query, total_results or 0, captured, requests


def collect_day(connection, client, day):
    query, total, captured, requests = fetch_day(connection, client, day)
    completed = iso_utc(utc_now())
    connection.execute("""
        INSERT INTO day_coverage (
            day_utc, query_string, status, api_total_results, papers_captured,
            successful_queries, page_requests, completed_at_utc, error_message
        ) VALUES (?, ?, 'ok', ?, ?, 1, ?, ?, NULL)
        ON CONFLICT(day_utc) DO UPDATE SET
            query_string=excluded.query_string,
            status='ok', api_total_results=excluded.api_total_results,
            papers_captured=excluded.papers_captured,
            successful_queries=excluded.successful_queries,
            page_requests=excluded.page_requests,
            completed_at_utc=excluded.completed_at_utc, error_message=NULL
    """, (day.isoformat(), query, total, captured, requests, completed))
    connection.commit()
    return {"date_utc": day.isoformat(), "status": "ok", "api_total_results": total,
            "papers_captured": captured, "successful_queries": 1, "page_requests": requests,
            "query_string": query}


def archive_start_day(connection, client):
    row = connection.execute(
        "SELECT state_value FROM collector_state WHERE state_key='archive_start_day'"
    ).fetchone()
    if row:
        return date.fromisoformat(row[0])

    # Discover the oldest record for this category once, rather than walking
    # empty calendar years back to arXiv's 1991 launch date.
    root, _, _ = client.fetch({
        "search_query": f"cat:{CATEGORY}",
        "start": 0,
        "max_results": 1,
        "sortBy": "submittedDate",
        "sortOrder": "ascending",
    })
    error = api_error(root)
    if error:
        raise CollectionError(f"Error al descubrir el registro más antiguo: {error}")
    entries = root.findall(f"{{{ATOM}}}entry")
    if not entries:
        raise CollectionError(f"arXiv no devolvió registros para {CATEGORY}")
    published = atom_text(entries[0], "published")
    if not published:
        raise CollectionError("El registro más antiguo no contiene fecha published")
    first_day = datetime.fromisoformat(published.replace("Z", "+00:00")).date()
    # Include one extra day before the oldest record as a conservative boundary.
    start_day = first_day - timedelta(days=1)
    connection.execute("""
        INSERT INTO collector_state(state_key, state_value, updated_at_utc)
        VALUES ('archive_start_day', ?, ?)
    """, (start_day.isoformat(), iso_utc(utc_now())))
    connection.commit()
    return start_day


def daily_counts(connection):
    return [dict(zip(("date_utc", "query_string", "status", "api_total_results", "papers_captured",
                      "successful_queries", "page_requests", "completed_at_utc", "error_message"), row))
            for row in connection.execute("""
                SELECT day_utc, query_string, status, api_total_results, papers_captured,
                       successful_queries, page_requests, completed_at_utc, error_message
                FROM day_coverage ORDER BY day_utc
            """)]


def write_coverage_json(connection):
    coverage = daily_counts(connection)
    payload = {
        "schema_version": 1,
        "source": "arxiv",
        "category": CATEGORY,
        "database": DB_PATH.relative_to(ROOT).as_posix(),
        "generated_at_utc": iso_utc(utc_now()),
        "covered_day_count": sum(1 for row in coverage if row["status"] == "ok"),
        "paper_version_count": connection.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
        "days": coverage,
    }
    COVERAGE_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = COVERAGE_JSON_PATH.with_suffix(".json.tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(COVERAGE_JSON_PATH)
    return payload


def append_log(event):
    path = ROOT / "logs" / "arxiv_collector.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")


def configure_category(category):
    global CATEGORY, DB_PATH, COVERAGE_JSON_PATH
    CATEGORY = category
    slug = category.replace(".", "_")
    DB_PATH = ROOT / "library" / f"{slug}.sqlite"
    COVERAGE_JSON_PATH = ROOT / "library" / f"{slug}.coverage.json"


def run(max_days, category):
    configure_category(category)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    init_db(connection)
    now = utc_now()
    # Exclude the current UTC day: only dates through yesterday are eligible.
    target = now.date() - timedelta(days=1)
    run_id = str(uuid.uuid4())
    started = iso_utc(now)
    connection.execute("""
        INSERT INTO query_runs (run_id, category, from_day_utc, to_day_utc,
            query_string, started_at_utc, status)
        VALUES (?, ?, ?, ?, '', ?, 'running')
    """, (run_id, CATEGORY, target.isoformat(), target.isoformat(), started))
    connection.commit()
    client = PoliteClient()
    processed = []
    error = None
    archive_start = None
    day = target
    try:
        archive_start = archive_start_day(connection, client)
        # Fill the closest uncovered date first, then continue backwards. This
        # naturally retries failed days and fills holes between prior ranges.
        rows = connection.execute(
            "SELECT day_utc FROM day_coverage WHERE status='ok' AND day_utc<=?",
            (target.isoformat(),),
        ).fetchall()
        covered = {date.fromisoformat(row["day_utc"]) for row in rows}
        day = target
        while len(processed) < max_days:
            if day < archive_start:
                break
            if day not in covered:
                try:
                    result = collect_day(connection, client, day)
                except Exception as exc:
                    message = str(exc)
                    completed = iso_utc(utc_now())
                    connection.execute("""
                        INSERT INTO day_coverage (day_utc, query_string, status, error_message, completed_at_utc)
                        VALUES (?, ?, 'error', ?, ?)
                        ON CONFLICT(day_utc) DO UPDATE SET status='error',
                            query_string=excluded.query_string,
                            error_message=excluded.error_message,
                            completed_at_utc=excluded.completed_at_utc
                    """, (day.isoformat(), build_query(day), message, completed))
                    connection.commit()
                    processed.append({"date_utc": day.isoformat(), "status": "error", "error": message})
                    error = message
                    break
                processed.append(result)
            day -= timedelta(days=1)
        status = "error" if error else "ok"
    except Exception as exc:
        error = str(exc)
        status = "error"
    completed = iso_utc(utc_now())
    processed_days = [date.fromisoformat(row["date_utc"]) for row in processed]
    run_from = min(processed_days).isoformat() if processed_days else target.isoformat()
    run_to = max(processed_days).isoformat() if processed_days else target.isoformat()
    run_queries = [
        {
            "date_utc": row["date_utc"],
            "query": row.get("query_string") or build_query(date.fromisoformat(row["date_utc"])),
            "status": row["status"],
        }
        for row in processed
    ]
    connection.execute("""
        UPDATE query_runs SET from_day_utc=?, to_day_utc=?, query_string=?,
            completed_at_utc=?, status=?, papers_captured=?, error_message=?
        WHERE run_id=?
    """, (run_from, run_to, json_text(run_queries), completed, status,
          sum(row.get("papers_captured", 0) for row in processed), error, run_id))
    connection.commit()
    coverage = write_coverage_json(connection)
    result = {
        "event": "collector_run",
        "run_id": run_id,
        "category": CATEGORY,
        "status": status,
        "started_at_utc": started,
        "completed_at_utc": completed,
        "eligible_through_utc_day": target.isoformat(),
        "backfill_complete": archive_start is not None and day < archive_start,
        "days_processed": len(processed),
        "days": processed,
        "covered_day_count": coverage["covered_day_count"],
        "paper_version_count": coverage["paper_version_count"],
        "database": DB_PATH.relative_to(ROOT).as_posix(),
        "coverage_json": COVERAGE_JSON_PATH.relative_to(ROOT).as_posix(),
        "error": error,
    }
    append_log(result)
    connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if error:
        raise CollectionError(error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8-sig"))
    parser.add_argument("--category", choices=config["categories"], required=True)
    parser.add_argument("--max-days", type=int, default=MAX_DAYS_PER_RUN)
    args = parser.parse_args()
    if args.max_days < 1:
        parser.error("--max-days debe ser al menos 1")
    run(args.max_days, args.category)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
