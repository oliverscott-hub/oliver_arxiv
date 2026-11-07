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
EVALUATION_DATABASES = [
    "rubric_v3_qwen3_8b.sqlite",
    "rubric_v3_qwen3_8b_tuesday.sqlite",
    "rubric_v3_qwen3_8b_wednesday.sqlite",
    "rubric_v3_qwen3_8b_thursday.sqlite",
    "rubric_v3_qwen3_8b_friday.sqlite",
    "rubric_v3_qwen3_8b_weekend.sqlite",
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


def evaluated_ids() -> set[str]:
    result = set()
    for filename in EVALUATION_DATABASES:
        path = ROOT / "abstract_evaluation" / filename
        if not path.exists():
            continue
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            result.update(row[0] for row in db.execute("SELECT id FROM evaluations"))
        finally:
            db.close()
    return result


def counts(records: dict[str, dt.date], evaluated: set[str], start: dt.date | None, end: dt.date | None):
    result = [{"evaluated": 0, "pending": 0} for _ in range(7)]
    for arxiv_id, value in records.items():
        if start is not None and not start <= value <= end:
            continue
        slot = result[value.weekday()]
        slot["evaluated" if arxiv_id in evaluated else "pending"] += 1
    return result


def main() -> None:
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    monday = today - dt.timedelta(days=today.weekday())
    previous_monday = monday - dt.timedelta(days=7)
    previous_sunday = monday - dt.timedelta(days=1)
    month_start = today.replace(day=1)
    quarter_month = ((today.month - 1) // 3) * 3 + 1
    quarter_start = today.replace(month=quarter_month, day=1)
    half_start = today.replace(month=1 if today.month <= 6 else 7, day=1)
    year_start = today.replace(month=1, day=1)
    evaluated = evaluated_ids()

    categories = {}
    all_records: dict[str, dt.date] = {}
    for name, slug in CATEGORIES:
        papers = read_category(slug)
        categories[name] = {
            "total": len(papers),
            "current_week": counts(papers, evaluated, monday, today),
            "previous_week": counts(papers, evaluated, previous_monday, previous_sunday),
            "current_month": counts(papers, evaluated, month_start, today),
            "current_quarter": counts(papers, evaluated, quarter_start, today),
            "current_half_year": counts(papers, evaluated, half_start, today),
            "current_year": counts(papers, evaluated, year_start, today),
            "all_time": counts(papers, evaluated, None, None),
        }
        # A paper present in multiple collector categories is counted once in the combined view.
        for arxiv_id, date in papers.items():
            if arxiv_id not in all_records or date > all_records[arxiv_id]:
                all_records[arxiv_id] = date

    categories["Total"] = {
        "total": len(all_records),
        "current_week": counts(all_records, evaluated, monday, today),
        "previous_week": counts(all_records, evaluated, previous_monday, previous_sunday),
        "current_month": counts(all_records, evaluated, month_start, today),
        "current_quarter": counts(all_records, evaluated, quarter_start, today),
        "current_half_year": counts(all_records, evaluated, half_start, today),
        "current_year": counts(all_records, evaluated, year_start, today),
        "all_time": counts(all_records, evaluated, None, None),
    }
    payload = {
        "generated_at_utc": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "today_utc": today.isoformat(),
        "week_start_utc": monday.isoformat(),
        "previous_week_start_utc": previous_monday.isoformat(),
        "previous_week_end_utc": previous_sunday.isoformat(),
        "month_start_utc": month_start.isoformat(),
        "quarter_start_utc": quarter_start.isoformat(),
        "half_year_start_utc": half_start.isoformat(),
        "year_start_utc": year_start.isoformat(),
        "weekdays": ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
        "counting_rule": "updated, falling back to published; unique arxiv_id in combined Total",
        "evaluation_databases": {
            "Qwen3-8B lunes": "abstract_evaluation/rubric_v3_qwen3_8b.sqlite",
            "Qwen3-8B martes": "abstract_evaluation/rubric_v3_qwen3_8b_tuesday.sqlite",
            "Qwen3-8B miércoles": "abstract_evaluation/rubric_v3_qwen3_8b_wednesday.sqlite",
            "Qwen3-8B jueves": "abstract_evaluation/rubric_v3_qwen3_8b_thursday.sqlite",
            "Qwen3-8B viernes": "abstract_evaluation/rubric_v3_qwen3_8b_friday.sqlite",
            "Qwen3-8B fin de semana": "abstract_evaluation/rubric_v3_qwen3_8b_weekend.sqlite",
        },
        "categories": categories,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT.relative_to(ROOT)}; combined unique papers: {len(all_records):,}")


if __name__ == "__main__":
    main()
