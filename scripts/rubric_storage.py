"""Versioned, append-only storage for abstract-only rubric experiments."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

RUBRIC_VERSION = 1
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
CONTRIBUTIONS = {"DISCOVERY", "MEASUREMENT", "CONSTRAINT", "THEORY", "METHOD", "REANALYSIS", "REPLICATION", "SYNTHESIS", "OTHER", "UNDETERMINED"}
METHODS = {"THEORETICAL", "OBSERVATIONAL", "EXPERIMENTAL", "COMPUTATIONAL", "STATISTICAL"}
RESULTS = {"PROPOSED", "DERIVED", "SIMULATED", "OBSERVED", "MEASURED", "TESTED", "MIXED", "UNDETERMINED"}
SUPPORT = {"UNSPECIFIED", "ASSERTED", "ARGUED", "SUBSTANTIATED"}
LIMITATIONS = {"NONE_REPORTED", "QUALIFIED", "EXPLICITLY_LIMITED"}
CLAIMS = {"ESTABLISHED_WITHIN_MODEL", "EMPIRICALLY_SUPPORTED", "TENTATIVE", "SPECULATIVE", "UNCLEAR"}


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError(f"Expected exactly these keys: {sorted(expected)}")


def _choice(value, allowed):
    if value not in allowed:
        raise ValueError(f"Invalid category: {value!r}")


def _words(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.split()) > maximum:
        raise ValueError(f"Expected nonempty text of at most {maximum} words")


def validate_response(response: dict, variant: str) -> dict:
    """Validate A/B verbatim; return score components without trusting model arithmetic."""
    if variant not in {"A", "B"}:
        raise ValueError("Rubric response variant must be A or B")
    expected = {"importance", "description", "evidence_control", "summary"}
    expected.add("justification" if variant == "A" else "evidence")
    _keys(response, expected)
    importance = response["importance"]
    _keys(importance, [f"I{i}" for i in range(1, 8)])
    levels = []
    for i, choices in enumerate(OPTIONS, 1):
        category = importance[f"I{i}"]
        _choice(category, set(choices) | {"UNDETERMINED"})
        levels.append(None if category == "UNDETERMINED" else choices.index(category))
    description = response["description"]
    _keys(description, {"contribution_type", "methods", "result_status"})
    _choice(description["contribution_type"], CONTRIBUTIONS)
    methods = description["methods"]
    if not isinstance(methods, list) or len(methods) != len(set(methods)) or not set(methods) <= METHODS:
        raise ValueError("Invalid methods")
    _choice(description["result_status"], RESULTS)
    control = response["evidence_control"]
    _keys(control, {"support", "limitations", "claim_status"})
    _choice(control["support"], SUPPORT)
    _choice(control["limitations"], LIMITATIONS)
    _choice(control["claim_status"], CLAIMS)
    _words(response["summary"], 30)
    if variant == "A":
        _words(response["justification"], 80)
    else:
        evidence = response["evidence"]
        _keys(evidence, [f"I{i}" for i in range(1, 8)])
        for i in range(1, 8):
            item = evidence[f"I{i}"]
            _keys(item, {"type", "text"})
            _choice(item["type"], {"DIRECT", "INFERRED", "MISSING"})
            if (item["type"] == "MISSING") != (levels[i - 1] is None):
                raise ValueError(f"I{i}: MISSING iff UNDETERMINED")
            _words(item["text"], 20)
    return calculate_score(levels)


def calculate_score(levels: list[int | None]) -> dict:
    if len(levels) != 7 or any(level not in (None, 0, 1, 2, 3) for level in levels):
        raise ValueError("Seven rubric levels required")
    known = sum(weight * level for weight, level in zip(WEIGHTS, levels) if level is not None)
    missing = sum(weight for weight, level in zip(WEIGHTS, levels) if level is None)
    # Half-up rounding to three decimal places, entirely with integer arithmetic.
    def milli(twice_numerator):
        return (twice_numerator * 1000 + 30) // 60
    return {
        "score_milli": milli(2 * known + 3 * missing),
        "coverage_percent": 100 - missing,
        "minimum_milli": milli(2 * known),
        "maximum_milli": milli(2 * known + 6 * missing),
        "undetermined_count": sum(level is None for level in levels),
    }


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("""CREATE TABLE IF NOT EXISTS evaluations (
        id INTEGER PRIMARY KEY,
        arxiv_id TEXT NOT NULL,
        modelo TEXT NOT NULL,
        version_rubrica INTEGER NOT NULL,
        nota_milesimas INTEGER CHECK(nota_milesimas BETWEEN 0 AND 10000),
        variante TEXT NOT NULL CHECK(variante IN ('A','B','C')),
        version_pesos INTEGER,
        pesos_json TEXT,
        abstract_sha256 TEXT NOT NULL,
        I1 TEXT, I2 TEXT, I3 TEXT, I4 TEXT, I5 TEXT, I6 TEXT, I7 TEXT,
        I1_evidence_type TEXT, I1_evidence_text TEXT,
        I2_evidence_type TEXT, I2_evidence_text TEXT,
        I3_evidence_type TEXT, I3_evidence_text TEXT,
        I4_evidence_type TEXT, I4_evidence_text TEXT,
        I5_evidence_type TEXT, I5_evidence_text TEXT,
        I6_evidence_type TEXT, I6_evidence_text TEXT,
        I7_evidence_type TEXT, I7_evidence_text TEXT,
        contribution_type TEXT, methods_json TEXT, result_status TEXT,
        support TEXT, limitations TEXT, claim_status TEXT,
        summary TEXT, justification TEXT,
        coverage_percent INTEGER, minimum_milli INTEGER, maximum_milli INTEGER,
        undetermined_count INTEGER,
        response_raw TEXT NOT NULL,
        validation_error TEXT,
        created_at_utc TEXT NOT NULL,
        latency_ms INTEGER,
        input_tokens INTEGER, output_tokens INTEGER
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS evaluations_lookup ON evaluations(arxiv_id, modelo, version_rubrica, variante)")
    db.commit()
    return db


def save_response(db: sqlite3.Connection, *, arxiv_id: str, model: str, abstract: str,
                  variant: str, raw: str, latency_ms: int | None = None,
                  input_tokens: int | None = None, output_tokens: int | None = None) -> int:
    """Append one attempt, including malformed responses for audit. C remains a baseline."""
    if variant not in {"A", "B", "C"} or not arxiv_id or not model or not abstract.strip():
        raise ValueError("Missing or invalid evaluation metadata")
    values = {"arxiv_id": arxiv_id, "modelo": model, "version_rubrica": RUBRIC_VERSION,
              "variante": variant, "abstract_sha256": hashlib.sha256(abstract.encode()).hexdigest(),
              "response_raw": raw, "created_at_utc": datetime.now(timezone.utc).isoformat(),
              "latency_ms": latency_ms, "input_tokens": input_tokens, "output_tokens": output_tokens}
    try:
        parsed = json.loads(raw)
        if variant in {"A", "B"}:
            score = validate_response(parsed, variant)
            values.update(score)
            values.update({"version_pesos": WEIGHTS_VERSION, "pesos_json": json.dumps(WEIGHTS)})
            values.update(parsed["importance"])
            values.update(parsed["description"] | parsed["evidence_control"])
            values["methods_json"] = json.dumps(values.pop("methods"))
            values["summary"] = parsed["summary"]
            if variant == "A":
                values["justification"] = parsed["justification"]
            else:
                for key, evidence in parsed["evidence"].items():
                    values[f"{key}_evidence_type"] = evidence["type"]
                    values[f"{key}_evidence_text"] = evidence["text"]
            values["nota_milesimas"] = values.pop("score_milli")
        else:
            _keys(parsed, {"importance", "justification"})
            if type(parsed["importance"]) is not int or not 0 <= parsed["importance"] <= 10:
                raise ValueError("Invalid baseline score")
            _words(parsed["justification"], 500)
            values["nota_milesimas"] = parsed["importance"] * 1000
            values["justification"] = parsed["justification"]
    except (ValueError, TypeError, KeyError) as exc:
        values["validation_error"] = str(exc)
    columns = list(values)
    cursor = db.execute(
        f"INSERT INTO evaluations ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
        list(values.values()),
    )
    db.commit()
    return cursor.lastrowid
