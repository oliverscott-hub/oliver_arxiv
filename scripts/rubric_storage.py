"""Strict storage for seven letter selections under scientific rubric version 1."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RUBRIC_VERSION = 1
PROMPT_VERSION = 2
WEIGHTS_VERSION = 1
WEIGHTS = (15, 15, 25, 10, 15, 10, 10)
OPTIONS = (
    ("NARROW", "SPECIALIZED", "MAJOR", "FUNDAMENTAL"),
    ("REPLICATIVE", "INCREMENTAL", "DISTINCT", "RADICALLY_NEW"),
    ("MINIMAL", "LIMITED", "SUBSTANTIAL", "DECISIVE"),
    ("LOCALIZED", "SPECIALIZED", "BROAD", "CROSS_FIELD"),
    ("UNCHANGED", "REFINED", "EXTENDED", "FOUNDATIONAL"),
    ("NONE", "LIMITED", "SUBSTANTIAL", "DECISIVE"),
    ("ROUTINE", "IMPROVING", "ENABLING", "TRANSFORMATIVE"),
)
LINE = re.compile(r"^I([1-7])\s*=\s*([ABCDU])$", re.IGNORECASE)


def parse_choices(raw: str) -> tuple[list[str], list[str]]:
    """Require seven ordered lines; accept upper/lowercase letters and whitespace."""
    if not isinstance(raw, str):
        raise ValueError("Response is not text")
    lines = raw.strip().splitlines()
    if len(lines) != 7:
        raise ValueError(f"Expected seven lines, received {len(lines)}")
    choices: list[str] = []
    categories: list[str] = []
    for expected, line in enumerate(lines, 1):
        match = LINE.fullmatch(line.strip())
        if match is None or int(match.group(1)) != expected:
            raise ValueError(f"Invalid choice line I{expected}: {line!r}")
        letter = match.group(2).upper()
        choices.append(letter)
        categories.append("UNDETERMINED" if letter == "U" else OPTIONS[expected - 1][ord(letter) - ord("A")])
    return choices, categories


def calculate_score(choices: list[str]) -> dict[str, int]:
    if len(choices) != 7 or any(choice not in "ABCDU" for choice in choices):
        raise ValueError("Seven normalized choices are required")
    known = sum(weight * (ord(choice) - ord("A"))
                for weight, choice in zip(WEIGHTS, choices) if choice != "U")
    missing = sum(weight for weight, choice in zip(WEIGHTS, choices) if choice == "U")
    # Exact integer arithmetic; midpoint 1.5 for U; half-up to three decimals.
    def milli(twice_numerator: int) -> int:
        return (twice_numerator * 1000 + 30) // 60
    return {
        "nota_milesimas": milli(2 * known + 3 * missing),
        "coverage_percent": 100 - missing,
        "minimum_milli": milli(2 * known),
        "maximum_milli": milli(2 * known + 6 * missing),
        "undetermined_count": choices.count("U"),
    }


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("PRAGMA journal_mode=DELETE")
    db.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY,
        arxiv_id TEXT NOT NULL,
        modelo TEXT NOT NULL,
        version_rubrica INTEGER NOT NULL CHECK(version_rubrica=1),
        nota_milesimas INTEGER CHECK(nota_milesimas BETWEEN 0 AND 10000),
        I1_choice TEXT, I2_choice TEXT, I3_choice TEXT, I4_choice TEXT,
        I5_choice TEXT, I6_choice TEXT, I7_choice TEXT,
        I1 TEXT, I2 TEXT, I3 TEXT, I4 TEXT, I5 TEXT, I6 TEXT, I7 TEXT,
        coverage_percent INTEGER, minimum_milli INTEGER, maximum_milli INTEGER,
        undetermined_count INTEGER,
        version_pesos INTEGER NOT NULL,
        pesos_json TEXT NOT NULL,
        version_prompt INTEGER NOT NULL,
        abstract_sha256 TEXT NOT NULL,
        response_raw TEXT NOT NULL,
        validation_error TEXT,
        created_at_utc TEXT NOT NULL,
        latency_ms INTEGER,
        input_tokens INTEGER,
        output_tokens INTEGER
    )""")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_valid_score ON evaluations(arxiv_id,version_rubrica) WHERE validation_error IS NULL")
    db.execute("CREATE INDEX IF NOT EXISTS evaluation_lookup ON evaluations(arxiv_id,version_rubrica)")
    db.commit()
    return db


def save_response(db: sqlite3.Connection, *, arxiv_id: str, model: str, abstract: str,
                  raw: str, latency_ms: int | None = None,
                  input_tokens: int | None = None, output_tokens: int | None = None) -> int:
    if not arxiv_id or not model or not abstract.strip():
        raise ValueError("Missing evaluation metadata")
    values = {
        "arxiv_id": arxiv_id, "modelo": model, "version_rubrica": RUBRIC_VERSION,
        "version_pesos": WEIGHTS_VERSION, "pesos_json": "[15,15,25,10,15,10,10]",
        "version_prompt": PROMPT_VERSION,
        "abstract_sha256": hashlib.sha256(abstract.encode("utf-8")).hexdigest(),
        "response_raw": raw, "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "latency_ms": latency_ms, "input_tokens": input_tokens, "output_tokens": output_tokens,
    }
    try:
        choices, categories = parse_choices(raw)
        values.update(calculate_score(choices))
        for i, (choice, category) in enumerate(zip(choices, categories), 1):
            values[f"I{i}_choice"] = choice
            values[f"I{i}"] = category
    except ValueError as error:
        values["validation_error"] = str(error)
    columns = list(values)
    cursor = db.execute(
        f"INSERT INTO evaluations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        list(values.values()),
    )
    db.commit()
    return cursor.lastrowid
