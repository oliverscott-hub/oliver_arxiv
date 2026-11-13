"""Build the arXiv pipeline dashboard from collector and evaluation databases."""
import datetime as dt
import json
import sqlite3

from build_weekday_dashboard import ROOT, LIBRARY, CATEGORIES, EVALUATION_DATABASES, read_category
from library_storage import restore

EVALUATIONS = ROOT / "abstract_evaluation"
OUTPUT = ROOT / "dashboard/data/pipeline.json"
STAGES = ("collected", "abstract_pending", "abstract_discarded", "abstract_stopped",
          "paper_pending", "paper_discarded", "paper_stopped", "selected")


def rows(path, table, columns):
    if not path.exists():
        return []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute(f"SELECT {columns} FROM {table}").fetchall()


def records():
    phase1, phase2, phase3 = {}, {}, {}
    for filename in EVALUATION_DATABASES:
        for aid, attempts, score, q2, q3 in rows(EVALUATIONS / filename, "evaluations", "id,intentos,puntuacion,Q2,Q3"):
            phase1[aid] = (attempts, score, q2, q3)
    for path in (EVALUATIONS / "open_answers_v1").glob("pdf_answers_qwen3_rag_8b*.sqlite"):
        for aid, attempts in rows(path, "answers", "id,intentos"):
            phase2[aid] = attempts
    for aid, attempts, score in rows(EVALUATIONS / "phase3/pdf_score_qwen3_8b.sqlite", "evaluations", "id,intentos,puntuacion"):
        phase3[aid] = (attempts, score)
    phase4 = {row[0] for row in rows(EVALUATIONS / "phase4/selected_papers.sqlite", "papers", "id")}
    return phase1, phase2, phase3, phase4


def stage(aid, phase1, phase2, phase3, phase4):
    if aid in phase4:
        return "selected"
    if aid in phase3:
        attempts, score = phase3[aid]
        if attempts == -1:
            return "paper_discarded"
        if attempts == 0 and score is not None:
            return "paper_pending" if score >= 900 else "paper_stopped"
        return "paper_pending"
    if aid in phase2:
        attempts = phase2[aid]
        return "paper_discarded" if attempts == -1 else "paper_pending"
    if aid in phase1:
        attempts, score, q2, q3 = phase1[aid]
        if attempts == -1:
            return "abstract_discarded"
        if attempts != 0 or score is None:
            return "abstract_pending"
        qualifies = score > 800 or (700 < score <= 800 and q2 == "D" and q3 in ("C", "D"))
        return "paper_pending" if qualifies else "abstract_stopped"
    return "collected"


def empty():
    return {name: 0 for name in STAGES}


def category_coverage(slug, yesterday):
    paper_db = restore(LIBRARY / f"{slug}.sqlite")
    with sqlite3.connect(paper_db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT state_value FROM collector_state WHERE state_key='archive_start_day'").fetchone()
        if not row:
            row = db.execute("SELECT MIN(SUBSTR(published,1,10)) FROM papers").fetchone()
    if not row or not row[0]:
        return None
    start = dt.date.fromisoformat(row[0][:10])
    if start > yesterday:
        return None
    path = restore(LIBRARY / f"{slug}.coverage.sqlite")
    days = []
    if path.exists():
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            days = sorted(dt.date.fromisoformat(day) for (day,) in db.execute(
                "SELECT day FROM daily_coverage WHERE day BETWEEN ? AND ?",
                (start.isoformat(), yesterday.isoformat())))
    runs = []
    for day in days:
        if runs and day <= runs[-1][1] + dt.timedelta(days=1):
            runs[-1][1] = day
        else:
            runs.append([day, day])
    total = (yesterday - start).days + 1
    return {"start": start.isoformat(), "end": yesterday.isoformat(),
            "covered": len(days), "total": total,
            "runs": [[a.isoformat(), b.isoformat()] for a, b in runs]}


def aggregate(items, state, today):
    phase1, phase2, phase3, phase4 = state
    week_start = today - dt.timedelta(days=today.weekday())
    previous_start = week_start - dt.timedelta(days=7)
    previous_end = week_start - dt.timedelta(days=1)
    buckets = {
        "current_week": [empty() for _ in range(7)],
        "previous_week": [empty() for _ in range(7)],
        "weeks": [empty() for _ in range(12)],
        "months": [empty() for _ in range(12)],
        "quarters": [empty() for _ in range(8)],
        "semesters": [empty() for _ in range(max(1, (today.year - min((day.year for day in items.values()), default=today.year)) * 2 + (today.month > 6) + 1))],
        "years": [empty() for _ in range(max(1, today.year - min((day.year for day in items.values()), default=today.year) + 1))],
    }
    totals = empty()
    started = [0, 0, 0]
    for aid, day in items.items():
        key = stage(aid, *state)
        totals[key] += 1
        started[0] += aid in phase1 or aid in phase2 or aid in phase3 or aid in phase4
        started[1] += aid in phase2 or aid in phase3 or aid in phase4
        started[2] += aid in phase4
        if week_start <= day <= today:
            buckets["current_week"][day.weekday()][key] += 1
        if previous_start <= day <= previous_end:
            buckets["previous_week"][day.weekday()][key] += 1
        age_weeks = (week_start - (day - dt.timedelta(days=day.weekday()))).days // 7
        if 0 <= age_weeks < 12:
            buckets["weeks"][11-age_weeks][key] += 1
        age_months = (today.year-day.year)*12 + today.month-day.month
        if 0 <= age_months < 12:
            buckets["months"][11-age_months][key] += 1
        age_quarters = (today.year-day.year)*4 + (today.month-1)//3 - (day.month-1)//3
        if 0 <= age_quarters < 8:
            buckets["quarters"][7-age_quarters][key] += 1
        age_semesters = (today.year-day.year)*2 + (today.month > 6) - (day.month > 6)
        if 0 <= age_semesters < len(buckets["semesters"]):
            buckets["semesters"][len(buckets["semesters"])-1-age_semesters][key] += 1
        age_years = today.year-day.year
        if 0 <= age_years < len(buckets["years"]):
            buckets["years"][len(buckets["years"])-1-age_years][key] += 1
    def iso_week(start):
        year, week, _ = start.isocalendar()
        return f"{year}-S{week:02d}"
    labels = {
        "current_week": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        "previous_week": ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"],
        "weeks": [iso_week(week_start-dt.timedelta(weeks=i)) for i in range(11, -1, -1)],
        "months": [],
        "quarters": [], "semesters": [],
        "years": [str(today.year-i) for i in range(len(buckets["years"])-1, -1, -1)],
    }
    labels["months"] = [f"{(today.year*12+today.month-1-i)//12}-{(today.year*12+today.month-1-i)%12+1:02d}" for i in range(11, -1, -1)]
    for age in range(7, -1, -1):
        q = today.year*4+(today.month-1)//3-age
        labels["quarters"].append(f"T{q%4+1} {q//4}")
    for age in range(len(buckets["semesters"])-1, -1, -1):
        half = today.year*2+(today.month > 6)-age
        labels["semesters"].append(f"S{half%2+1} {half//2}")
    return {"total": len(items), "started": started, "totals": totals,
            "charts": {key: {"labels": labels[key], "values": values} for key, values in buckets.items()}}


def main():
    now = dt.datetime.now(dt.timezone.utc)
    state = records()
    categories = {}
    coverages = {}
    combined = {}
    for name, slug in CATEGORIES:
        restore(LIBRARY / f"{slug}.sqlite")
        works = read_category(slug)
        categories[name] = aggregate(works, state, now.date())
        coverages[name] = category_coverage(slug, now.date() - dt.timedelta(days=1))
        for aid, day in works.items():
            if aid not in combined or day > combined[aid]:
                combined[aid] = day
    payload = {"generated_at_utc": now.isoformat(), "stages": STAGES,
               "journals": categories, "coverage": coverages,
               "Total": aggregate(combined, state, now.date())}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Dashboard: {len(combined)} papers únicos")


if __name__ == "__main__":
    main()
