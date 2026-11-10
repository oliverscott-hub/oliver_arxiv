"""Select phase-3 papers scoring at least 9.00/10 for phase 4."""
import datetime as dt
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORES = ROOT / "abstract_evaluation/phase3/pdf_score_qwen3_8b.sqlite"
OUTPUT = ROOT / "abstract_evaluation/phase4/selected_papers.sqlite"


def sunday(stamp):
    day = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(dt.timezone.utc).date()
    return day.isoformat(), (day + dt.timedelta(days=6 - day.weekday())).isoformat()


def main():
    scores = sqlite3.connect(SCORES)
    wanted = {row[0] for row in scores.execute("SELECT id FROM evaluations WHERE puntuacion>=900")}
    scores.close()
    found = {}
    for path in sorted((ROOT / "library").glob("*.sqlite")):
        if ".coverage." in path.name:
            continue
        source = sqlite3.connect(path)
        try:
            for aid, updated, published, links_json in source.execute(
                "SELECT arxiv_id,updated,published,links_json FROM papers"
            ):
                if aid not in wanted or not (updated or published):
                    continue
                effective, week_sunday = sunday(updated or published)
                links = json.loads(links_json or "[]")
                landing = next((x.get("href") for x in links if x.get("rel") == "alternate" and x.get("href")), None)
                pdf = next((x.get("href") for x in links if x.get("type") == "application/pdf" and x.get("href")), None)
                row = (aid, effective, week_sunday, landing or f"https://arxiv.org/abs/{aid}",
                       pdf or f"https://arxiv.org/pdf/{aid}")
                if aid not in found or effective > found[aid][1]:
                    found[aid] = row
        finally:
            source.close()
    missing = wanted - found.keys()
    if missing:
        raise RuntimeError(f"Selected arXiv IDs absent from collectors: {len(missing)}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".sqlite.tmp")
    temp.unlink(missing_ok=True)
    target = sqlite3.connect(temp)
    try:
        target.execute("""CREATE TABLE papers (
            id TEXT PRIMARY KEY, effective_date TEXT NOT NULL, week_sunday TEXT NOT NULL,
            arxiv_landing_page TEXT NOT NULL, arxiv_pdf_url TEXT NOT NULL)""")
        target.executemany("INSERT INTO papers VALUES(?,?,?,?,?)", sorted(found.values()))
        target.execute("CREATE INDEX papers_week_sunday ON papers(week_sunday)")
        target.commit()
    finally:
        target.close()
    temp.replace(OUTPUT)
    print(f"Phase 4 arXiv: {len(found)} papers with score >=9.00")


if __name__ == "__main__":
    main()
