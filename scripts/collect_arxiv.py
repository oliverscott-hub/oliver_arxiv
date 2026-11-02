import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "config.json"
DATA_PATH = ROOT / "data" / "arxiv_records.json"
BASE_URL = "https://export.arxiv.org/api/query"
ATOM = "http://www.w3.org/2005/Atom"
OPEN_SEARCH = "http://a9.com/-/spec/opensearch/1.1/"
ARXIV = "http://arxiv.org/schemas/atom"
MAX_RESULTS_PER_REQUEST = 2000
MAX_RESULTS_PER_QUERY = 30000
MAX_RETRIES = 3


def utc_now():
    return datetime.now(timezone.utc)


def iso(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def build_query(categories, start, end):
    category_terms = " OR ".join(f"cat:{category}" for category in categories)
    date_term = f"submittedDate:[{start:%Y%m%d%H%M} TO {end:%Y%m%d%H%M}]"
    return f"({category_terms}) AND {date_term}"


class PoliteClient:
    def __init__(self, minimum_interval):
        self.minimum_interval = max(3.0, float(minimum_interval))
        self.last_request_at = None
        self.user_agent = os.environ.get(
            "ARXIV_USER_AGENT",
            "MalditoSigmaArxivCollector/1.0 (metadata-only; configure contact email)",
        )

    def fetch(self, params):
        url = BASE_URL + "?" + urllib.parse.urlencode(params)
        for attempt in range(MAX_RETRIES + 1):
            if self.last_request_at is not None:
                remaining = self.minimum_interval - (time.monotonic() - self.last_request_at)
                if remaining > 0:
                    time.sleep(remaining)
            request = urllib.request.Request(
                url,
                headers={"User-Agent": self.user_agent, "Accept": "application/atom+xml"},
            )
            self.last_request_at = time.monotonic()
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    payload = response.read()
                return ET.fromstring(payload)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After")
                    try:
                        delay = max(self.minimum_interval, float(retry_after)) if retry_after else self.minimum_interval * (2 ** attempt)
                    except ValueError:
                        parsed = email.utils.parsedate_to_datetime(retry_after)
                        delay = max(self.minimum_interval, (parsed - utc_now()).total_seconds())
                elif 500 <= exc.code < 600:
                    delay = self.minimum_interval * (2 ** attempt)
                else:
                    detail = exc.read().decode("utf-8", errors="replace")[:1000]
                    raise RuntimeError(f"arXiv HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
                if attempt >= MAX_RETRIES:
                    raise RuntimeError(f"No se pudo recuperar una respuesta válida: {exc}") from exc
                delay = self.minimum_interval * (2 ** attempt)

            if attempt >= MAX_RETRIES:
                raise RuntimeError("Reintentos agotados; se detiene la ejecución para evitar peticiones rápidas.")
            time.sleep(max(self.minimum_interval, delay))


def text_of(parent, tag):
    node = parent.find(f"{{{ATOM}}}{tag}")
    return " ".join((node.text or "").split()) if node is not None else ""


def api_error_message(root):
    for entry in root.findall(f"{{{ATOM}}}entry"):
        for link in entry.findall(f"{{{ATOM}}}link"):
            if "/api/errors#" in link.get("href", ""):
                return text_of(entry, "summary") or text_of(entry, "title")
    return None


def parse_entry(entry, seen_at):
    id_url = text_of(entry, "id")
    match = re.search(r"/abs/([^/?#]+)", id_url)
    if not match:
        return None
    raw_id = match.group(1)
    version_match = re.search(r"v(\d+)$", raw_id)
    version = int(version_match.group(1)) if version_match else None
    arxiv_id = re.sub(r"v\d+$", "", raw_id)
    version_url = None
    for link in entry.findall(f"{{{ATOM}}}link"):
        href = link.get("href", "")
        if link.get("rel") == "alternate" and "/abs/" in href:
            version_url = href
            version_match = re.search(r"v(\d+)$", href)
            if version_match:
                version = int(version_match.group(1))
            break
    primary = entry.find(f"{{{ARXIV}}}primary_category")
    authors = [text_of(author, "name") for author in entry.findall(f"{{{ATOM}}}author")]
    return {
        "arxiv_id": arxiv_id,
        "version": version,
        "version_url": version_url or f"https://arxiv.org/abs/{arxiv_id}",
        "title": text_of(entry, "title"),
        "authors": authors,
        "summary": text_of(entry, "summary"),
        "published_v1": text_of(entry, "published"),
        "updated_version": text_of(entry, "updated"),
        "primary_category": primary.get("term") if primary is not None else None,
        "categories": sorted({node.get("term") for node in entry.findall(f"{{{ATOM}}}category") if node.get("term")}),
        "journal_reference": " ".join((node.text or "").split()) if (node := entry.find(f"{{{ARXIV}}}journal_ref")) is not None else "",
        "doi": " ".join((node.text or "").split()) if (node := entry.find(f"{{{ARXIV}}}doi")) is not None else "",
        "first_seen_utc": seen_at,
        "last_seen_utc": seen_at,
    }


def read_existing():
    if not DATA_PATH.exists():
        return {"schema_version": 1, "records": {}, "runs": []}
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se sobrescribe el archivo de datos ilegible: {exc}") from exc
    if not isinstance(data.get("records"), dict) or not isinstance(data.get("runs"), list):
        raise RuntimeError("Formato inesperado en el archivo de datos; se detiene sin sobrescribirlo.")
    return data


def merge_record(records, incoming):
    key = incoming["arxiv_id"]
    existing = records.get(key)
    if existing is None:
        incoming["versions_seen"] = [incoming["version"]] if incoming["version"] else []
        records[key] = incoming
        return True

    changed = False
    for field in ("version", "version_url", "title", "authors", "summary", "published_v1", "updated_version", "primary_category", "categories", "journal_reference", "doi"):
        value = incoming.get(field)
        if value and existing.get(field) != value:
            existing[field] = value
            changed = True
    existing["last_seen_utc"] = incoming["last_seen_utc"]
    versions = existing.setdefault("versions_seen", [])
    version = incoming.get("version")
    if version and version not in versions:
        versions.append(version)
        versions.sort()
        changed = True
    return changed


def main():
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))
    categories = config["categories"]
    page_size = int(config.get("page_size", MAX_RESULTS_PER_REQUEST))
    if not 1 <= page_size <= MAX_RESULTS_PER_REQUEST:
        raise ValueError(f"page_size debe estar entre 1 y {MAX_RESULTS_PER_REQUEST}")
    lookback = int(config.get("lookback_hours", 36))
    if lookback < 24:
        raise ValueError("lookback_hours debe ser al menos 24 para cubrir un ciclo fallido de 12 h")

    finished_at = utc_now()
    started_at = finished_at - timedelta(hours=lookback)
    start = started_at.replace(second=0, microsecond=0)
    end = finished_at.replace(second=0, microsecond=0)
    query = build_query(categories, start, end)
    data = read_existing()
    client = PoliteClient(config.get("min_request_interval_seconds", 3.2))
    total_reported = None
    pages = 0
    returned = 0
    records_changed = 0
    start_index = 0
    seen_at = iso(finished_at)

    while True:
        params = {
            "search_query": query,
            "start": start_index,
            "max_results": page_size,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        root = client.fetch(params)
        error = api_error_message(root)
        if error:
            raise RuntimeError(f"Error devuelto en Atom por arXiv: {error}")
        total_text = root.findtext(f"{{{OPEN_SEARCH}}}totalResults", default="0")
        total_reported = int(total_text)
        if total_reported > MAX_RESULTS_PER_QUERY:
            raise RuntimeError(
                f"Consulta demasiado amplia ({total_reported} resultados; máximo documentado 30000). "
                "No se guardó el catálogo. Refina o divide la consulta."
            )

        page_entries = root.findall(f"{{{ATOM}}}entry")
        pages += 1
        returned += len(page_entries)
        for entry in page_entries:
            record = parse_entry(entry, seen_at)
            if record and merge_record(data["records"], record):
                records_changed += 1
        if not page_entries or start_index + len(page_entries) >= total_reported:
            break
        start_index += len(page_entries)

    data["schema_version"] = 1
    data["last_run"] = {
        "checked_at_utc": seen_at,
        "window_start_utc": iso(start),
        "window_end_utc": iso(end),
        "lookback_hours": lookback,
        "categories": categories,
        "query": query,
        "total_reported": total_reported,
        "pages_fetched": pages,
        "records_returned": returned,
        "records_changed": records_changed,
    }
    data.setdefault("runs", []).append(data["last_run"])
    data["runs"] = data["runs"][-180:]
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = DATA_PATH.with_suffix(".json.tmp")
    temporary_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary_path.replace(DATA_PATH)
    print(json.dumps(data["last_run"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
