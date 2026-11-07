#!/usr/bin/env python3
"""Build static weekday totals from the arXiv collector SQLite files."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "library"
OUTPUT = ROOT / "dashboard" / "data" / "weekday_totals.json"
CATEGORIES = [
    ("astro-ph.CO", "astro-ph_CO"),
    ("astro-ph.HE", "astro-ph_HE"),
    ("astro-ph.GA", "astro-ph_GA"),
    ("astro-ph.SR", "astro-ph_SR"),
    ("astro-ph.IM", "astro-ph_IM"),
    ("gr-qc", "gr-qc"),
    ("hep-th", "hep-th"),
    ("hep-ph", "hep-ph"),
    ("hep-ex", "hep-ex"),
    ("math-ph", "math-ph"),
]


def read_category(slug: str) -> dict[str, dt.date]:
    path = LIBRARY / f"{slug}.sqlite"
    if not path.exists():
        raise FileNotFoundError(f"Missing collector database: {path}")
    db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        result = {}
        for arxiv_id, updated, published in db.execute(
            "SELECT arxiv_id, updated, published FROM papers"
        ):
            value = updated or published
            if not value:
                continue
            date = dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc).date()
            result[arxiv_id] = date
        return result
    finally:
        db.close()


def counts(records: dict[str, dt.date], start: dt.date | None, end: dt.date | None):
    values = [d for d in records.values() if start is None or start <= d <= end]
    result = [0] * 7
    for value in values:
        result[value.weekday()] += 1
    return result


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    monday = today - dt.timedelta(days=today.weekday())
    previous_monday = monday - dt.timedelta(days=7)
    previous_sunday = monday - dt.timedelta(days=1)

    categories = {}
    all_records: dict[str, dt.date] = {}
    for name, slug in CATEGORIES:
        papers = read_category(slug)
        categories[name] = {
            "total": len(papers),
            "current_week": counts(papers, monday, today),
            "previous_week": counts(papers, previous_monday, previous_sunday),
            "all_time": counts(papers, None, None),
        }
        # A paper present in multiple collector categories is counted once in the combined view.
        for arxiv_id, date in papers.items():
            if arxiv_id not in all_records or date > all_records[arxiv_id]:
                all_records[arxiv_id] = date

    categories["Total"] = {
        "total": len(all_records),
        "current_week": counts(all_records, monday, today),
        "previous_week": counts(all_records, previous_monday, previous_sunday),
        "all_time": counts(all_records, None, None),
    }
    payload = {
        "generated_at_utc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "week_start_utc": monday.isoformat(),
        "previous_week_start_utc": previous_monday.isoformat(),
        "previous_week_end_utc": previous_sunday.isoformat(),
        "weekdays": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
        "counting_rule": "updated, falling back to published; unique arxiv_id in combined Total",
        "categories": categories,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}; combined unique papers: {len(all_records):,}")


if __name__ == "__main__":
    main()
