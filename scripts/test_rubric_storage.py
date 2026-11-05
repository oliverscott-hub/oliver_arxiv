import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from rubric_storage import calculate_score, open_db, save_response, validate_response


def example(variant="B"):
    data = {
        "importance": dict(zip((f"I{i}" for i in range(1, 8)),
                               ("FUNDAMENTAL", "DISTINCT", "SUBSTANTIAL", "BROAD",
                                "EXTENDED", "SUBSTANTIAL", "ENABLING"))),
        "description": {"contribution_type": "THEORY", "methods": ["THEORETICAL"],
                        "result_status": "DERIVED"},
        "evidence_control": {"support": "ARGUED", "limitations": "QUALIFIED",
                             "claim_status": "ESTABLISHED_WITHIN_MODEL"},
        "summary": "Derives a mechanism.",
    }
    if variant == "B":
        data["evidence"] = {f"I{i}": {"type": "DIRECT", "text": "Reported in the abstract."}
                            for i in range(1, 8)}
    else:
        data["justification"] = "The result extends the theory."
    return data


class RubricStorageTests(unittest.TestCase):
    def test_score_and_uncertainty(self):
        self.assertEqual(calculate_score([0] * 7)["score_milli"], 0)
        self.assertEqual(calculate_score([3] * 7)["score_milli"], 10000)
        self.assertEqual(calculate_score([None] * 7), {
            "score_milli": 5000, "coverage_percent": 0,
            "minimum_milli": 0, "maximum_milli": 10000, "undetermined_count": 7})
        self.assertEqual(calculate_score([3, 2, 2, 2, 2, 2, 2])["score_milli"], 7167)

    def test_validity_and_missing(self):
        data = example()
        self.assertEqual(validate_response(data, "B")["score_milli"], 7167)
        data["importance"]["I5"] = "UNDETERMINED"
        with self.assertRaises(ValueError):
            validate_response(data, "B")
        data["evidence"]["I5"] = {"type": "MISSING", "text": "Consequences are not specified."}
        self.assertEqual(validate_response(data, "B")["coverage_percent"], 85)
        data["description"]["methods"] = ["THEORETICAL", "COMPUTATIONAL"]
        validate_response(data, "B")
        del data["importance"]["I7"]
        with self.assertRaises(ValueError):
            validate_response(data, "B")

    def test_append_and_invalid_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            db = open_db(Path(directory) / "rubric.sqlite")
            raw = json.dumps(example())
            first = save_response(db, arxiv_id="2610.12345v1", model="Qwen3-8B",
                                  abstract="Abstract", variant="B", raw=raw)
            second = save_response(db, arxiv_id="2610.12345v1", model="Qwen3-8B",
                                   abstract="Abstract", variant="B", raw=raw)
            self.assertNotEqual(first, second)
            invalid = save_response(db, arxiv_id="bad", model="Qwen3-8B",
                                    abstract="Abstract", variant="A", raw="{")
            row = db.execute("SELECT arxiv_id, modelo, version_rubrica, nota_milesimas, I1, I7, methods_json, response_raw FROM evaluations WHERE id=?", (first,)).fetchone()
            self.assertEqual(row[:6], ("2610.12345v1", "Qwen3-8B", 1, 7167, "FUNDAMENTAL", "ENABLING"))
            self.assertEqual(json.loads(row[6]), ["THEORETICAL"])
            self.assertEqual(row[7], raw)
            bad = db.execute("SELECT nota_milesimas, validation_error FROM evaluations WHERE id=?", (invalid,)).fetchone()
            self.assertIsNone(bad[0])
            self.assertTrue(bad[1])
            db.close()


if __name__ == "__main__":
    unittest.main()
