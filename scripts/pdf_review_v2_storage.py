"""Version 2 PDF-review storage and strict 18-line response parsing."""
import re
import sqlite3
from decimal import Decimal, ROUND_HALF_UP

WEIGHTS = (15, 30, 15, 10, 20, 10)
LINES = tuple(key for i in range(1, 7) for key in (f"Q{i}", f"E{i}", f"J{i}"))
COLUMNS = ("id", "modelo", "version_rubrica", "puntuacion", "pdf_url", *LINES)


def open_db(path, rubric_version=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    fields = ", ".join(f"{key} TEXT NOT NULL" for key in LINES)
    db.execute(f"""CREATE TABLE IF NOT EXISTS evaluations (
        id TEXT PRIMARY KEY, modelo TEXT NOT NULL,
        version_rubrica INTEGER NOT NULL CHECK(version_rubrica={rubric_version}),
        puntuacion INTEGER CHECK(puntuacion BETWEEN 0 AND 1000),
        pdf_url TEXT NOT NULL, {fields})""")
    db.commit()
    return db


def normalize(raw, paper, *, lenient_trace=False):
    if lenient_trace:
        raw = re.sub(r"</?b>", "", raw, flags=re.IGNORECASE)
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if len(lines) != 18:
        raise ValueError(f"Expected 18 lines, got {len(lines)}")
    values = {}
    for key, line in zip(LINES, lines):
        if not line.startswith(key + "="):
            raise ValueError(f"Expected {key} line")
        value = line[len(key) + 1:].strip()
        if key.startswith("Q"):
            value = value.upper()
            if value not in "ABCDU" or len(value) != 1:
                raise ValueError(f"Invalid {key}")
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            if key.startswith("J"):
                value = value.replace('"', "'")
        values[key] = value
    # Extracted PDF text can insert line breaks within a quotation.
    if not lenient_trace:
        searchable = " ".join(paper.split())
        for i in range(1, 7):
            q, e, j = (values[f"{part}{i}"] for part in "QEJ")
            if q == "U":
                if e != "-" or j != "-":
                    raise ValueError(f"Q{i}=U requires E{i}=J{i}=-")
            elif not e or e == "-" or " ".join(e.split()) not in searchable:
                raise ValueError(f"E{i} is not in extracted PDF text")
            if q in "AB" and j != "-":
                raise ValueError(f"J{i} must be - for {q}")
            if q in "CD" and (not j or j == "-"):
                raise ValueError(f"J{i} required for {q}")
    return values


def score_cents(values):
    if any(values[f"Q{i}"] == "U" for i in range(1, 7)):
        return None
    weighted = sum(w * "ABCD".index(values[f"Q{i}"]) for i, w in enumerate(WEIGHTS, 1))
    return int((Decimal(weighted) * Decimal(10) / Decimal(3)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def save(db, aid, model, url, values, rubric_version=2):
    score = score_cents(values)
    row = (aid, model, rubric_version, score, url, *(values[key] for key in LINES))
    db.execute(
        f"INSERT OR IGNORE INTO evaluations ({','.join(COLUMNS)}) VALUES ({','.join('?' for _ in COLUMNS)})",
        row,
    )
    db.commit()
    return score
