import sqlite3
import tempfile
import unittest
from pathlib import Path

from rubric_storage import calculate_score, open_db, parse_choices, save_response


class RubricStorageTests(unittest.TestCase):
    def test_upper_lower_case_and_mapping(self):
        choices, categories = parse_choices("i1=d\nI2=c\ni3=C\nI4=b\nI5=u\nI6=a\nI7 = D\n")
        self.assertEqual(choices, ["D", "C", "C", "B", "U", "A", "D"])
        self.assertEqual(categories, ["FUNDAMENTAL", "DISTINCT", "SUBSTANTIAL", "SPECIALIZED", "UNDETERMINED", "NONE", "TRANSFORMATIVE"])

    def test_reject_missing_extra_duplicate_and_invalid(self):
        for raw in ("I1=A", "\n".join(["I1=A"] * 7),
                    "\n".join(f"I{i}=X" for i in range(1,8)),
                    "\n".join(f"I{i}=A" for i in range(1,8)) + "\nExplanation: good"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse_choices(raw)

    def test_score_and_uncertainty(self):
        self.assertEqual(calculate_score(["A"]*7)["nota_milesimas"],0)
        self.assertEqual(calculate_score(["D"]*7)["nota_milesimas"],10000)
        self.assertEqual(calculate_score(["U"]*7),{
            "nota_milesimas":5000,"coverage_percent":0,"minimum_milli":0,
            "maximum_milli":10000,"undetermined_count":7})
        self.assertEqual(calculate_score(["D","C","C","C","C","C","C"])["nota_milesimas"],7167)

    def test_append_invalid_and_one_valid_per_paper(self):
        with tempfile.TemporaryDirectory() as directory:
            db=open_db(Path(directory)/"test.sqlite")
            bad=save_response(db,arxiv_id="2611.12345v1",model="Qwen3-8B",abstract="Abstract",raw="I1=A")
            raw="\n".join(f"I{i}=c" for i in range(1,8))
            good=save_response(db,arxiv_id="2611.12345v1",model="Qwen3-8B",abstract="Abstract",raw=raw)
            self.assertIsNone(db.execute("select nota_milesimas from evaluations where id=?",(bad,)).fetchone()[0])
            row=db.execute("select arxiv_id,modelo,version_rubrica,nota_milesimas,I1_choice,I1,response_raw,version_prompt from evaluations where id=?",(good,)).fetchone()
            self.assertEqual(row,("2611.12345v1","Qwen3-8B",1,6667,"C","MAJOR",raw,2))
            with self.assertRaises(sqlite3.IntegrityError):
                save_response(db,arxiv_id="2611.12345v1",model="Qwen3-8B",abstract="Abstract",raw=raw)
            db.close()


if __name__=="__main__":
    unittest.main()
