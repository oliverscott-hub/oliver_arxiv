"""Version 3 rubric storage and strict validation."""
from __future__ import annotations
import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

WEIGHTS = (20, 40, 20, 20)
LINES = ("T", "Q1", "E1", "J1", "Q2", "E2", "J2", "Q3", "E3", "J3", "Q4", "E4", "J4")
CHOICE = re.compile(r"^[ABCDU]$", re.IGNORECASE)
TYPE = re.compile(r"^[TEMS PU]$".replace(" ", ""), re.IGNORECASE)

def parse(raw: str, abstract: str) -> dict:
    lines = raw.strip().splitlines()
    if len(lines) != 13:
        raise ValueError(f"Expected 13 lines, got {len(lines)}")
    values = {}
    for key, line in zip(LINES, lines):
        prefix = key + "="
        if not line.startswith(prefix):
            raise ValueError(f"Expected {key} line")
        value = line[len(prefix):]
        if key.startswith(("E", "J")) and key != "T":
            if len(value) < 2 or value[0] != '"' or value[-1] != '"':
                raise ValueError(f"{key} must be quoted")
            value = value[1:-1]
        else:
            value = value.upper()
        values[key] = value
    if not TYPE.fullmatch(values["T"]):
        raise ValueError("Invalid T")
    for i in range(1, 5):
        q, e, j = (values[f"{key}{i}"] for key in ("Q", "E", "J"))
        if not CHOICE.fullmatch(q):
            raise ValueError(f"Invalid Q{i}")
        if q == "U":
            if e != "-": raise ValueError(f"E{i} must be - for U")
        elif not e or e == "-" or e not in abstract:
            raise ValueError(f"E{i} is not an exact abstract excerpt")
        if (q in "CD" and (not j or j == "-")) or (q in "ABU" and j != "-"):
            raise ValueError(f"Invalid J{i} for {q}")
    return values

def metrics(values: dict) -> dict:
    missing = sum(w for i, w in enumerate(WEIGHTS, 1) if values[f"Q{i}"] == "U")
    known = sum(w * (ord(values[f"Q{i}"]) - 65) for i, w in enumerate(WEIGHTS, 1) if values[f"Q{i}"] != "U")
    # score=(10/3)*sum(weight*level)/100; integer cents, half-up.
    cents = lambda numerator: (numerator * 10 + 1) // 3
    return {"score_cents": None if missing else cents(known),
            "coverage_percent": 100-missing,
            "minimum_cents": cents(known), "maximum_cents": cents(known+3*missing),
            "undetermined_count": sum(values[f"Q{i}"] == "U" for i in range(1,5))}

def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY,
        arxiv_id TEXT NOT NULL,
        modelo TEXT NOT NULL,
        puntuacion_centesimas INTEGER CHECK(puntuacion_centesimas BETWEEN 0 AND 1000),
        T TEXT, Q1 TEXT, E1 TEXT, J1 TEXT, Q2 TEXT, E2 TEXT, J2 TEXT,
        Q3 TEXT, E3 TEXT, J3 TEXT, Q4 TEXT, E4 TEXT, J4 TEXT,
        version_rubrica INTEGER NOT NULL CHECK(version_rubrica=3),
        pesos_json TEXT NOT NULL,
        coverage_percent INTEGER, minimum_cents INTEGER, maximum_cents INTEGER,
        undetermined_count INTEGER,
        abstract_sha256 TEXT NOT NULL,
        response_raw TEXT NOT NULL, validation_error TEXT,
        created_at_utc TEXT NOT NULL, latency_ms INTEGER,
        input_tokens INTEGER, output_tokens INTEGER
    )""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS valid_paper ON evaluations(arxiv_id) WHERE validation_error IS NULL")
    db.commit()
    return db

def save_response(db, *, arxiv_id, model, abstract, raw, latency_ms=None, input_tokens=None, output_tokens=None):
    values = {"arxiv_id": arxiv_id, "modelo": model, "version_rubrica": 3,
              "pesos_json": "[20,40,20,20]", "abstract_sha256": hashlib.sha256(abstract.encode()).hexdigest(),
              "response_raw": raw, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "latency_ms": latency_ms, "input_tokens": input_tokens, "output_tokens": output_tokens}
    try:
        selected = parse(raw, abstract)
        values.update(selected)
        calculated = metrics(selected)
        values["puntuacion_centesimas"] = calculated.pop("score_cents")
        values.update(calculated)
    except ValueError as error:
        values["validation_error"] = str(error)
    columns = list(values)
    cur = db.execute(f"INSERT INTO evaluations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(values.values()))
    db.commit()
    return cur.lastrowid
