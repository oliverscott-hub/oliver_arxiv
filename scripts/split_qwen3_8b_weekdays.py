#!/usr/bin/env python3
"""Move existing Qwen3-8B v3 evaluations to their effective UTC weekday DB."""

import datetime as dt
import json
import sqlite3
from pathlib import Path

from rubric_v3_storage import COLUMNS, open_db

ROOT = Path(__file__).resolve().parents[1]
GROUPS = ("monday", "tuesday", "wednesday", "thursday", "friday", "weekend", "weekend")
SOURCE = ROOT / "abstract_evaluation" / "rubric_v3_qwen3_8b.sqlite"


def main():
    original = open_db(SOURCE)
    rows = original.execute(f"SELECT {','.join(COLUMNS)} FROM evaluations").fetchall()
    ids = {row[0] for row in rows}
    effective = {}
    categories = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))["categories"]
    for category in categories:
        path = ROOT / "library" / f"{category.replace('.', '_')}.sqlite"
        library = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            for paper_id, timestamp in library.execute(
                "SELECT arxiv_id, COALESCE(NULLIF(updated,''),published) FROM papers"
            ):
                if paper_id in ids:
                    day = dt.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                    effective[paper_id] = day.astimezone(dt.timezone.utc).weekday()
        finally:
            library.close()
    missing = ids - effective.keys()
    if missing:
        raise RuntimeError(f"Cannot migrate {len(missing)} IDs missing from collectors: {sorted(missing)[:5]}")

    targets = {name: open_db(ROOT / "abstract_evaluation" / f"rubric_v3_qwen3_8b_{name}.sqlite")
               for name in GROUPS if name != "monday"}
    placeholders = ",".join("?" for _ in COLUMNS)
    sql = f"INSERT OR IGNORE INTO evaluations ({','.join(COLUMNS)}) VALUES ({placeholders})"
    try:
        for row in rows:
            group = GROUPS[effective[row[0]]]
            if group != "monday":
                targets[group].execute(sql, row)
        for group, conn in targets.items():
            expected = {row[0] for row in rows if GROUPS[effective[row[0]]] == group}
            present = {r[0] for r in conn.execute("SELECT id FROM evaluations")}
            if not expected <= present:
                raise RuntimeError(f"Migration incomplete for {group}")
        original.executemany("DELETE FROM evaluations WHERE id=?", [
            (row[0],) for row in rows if GROUPS[effective[row[0]]] != "monday"
        ])
        for conn in targets.values():
            conn.commit()
        original.commit()
        print("Original rows:", len(rows))
        print("Groups:", {group: sum(GROUPS[effective[row[0]]] == group for row in rows)
                          for group in dict.fromkeys(GROUPS)})
    finally:
        original.close()
        for conn in targets.values():
            conn.close()


if __name__ == "__main__":
    main()
