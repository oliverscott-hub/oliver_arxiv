import sqlite3
import tempfile
import unittest
from pathlib import Path
from rubric_v3_storage import open_db, parse, metrics, save_response

ABSTRACT = "We derive a specific result that reduces an important uncertainty."
RAW = '\n'.join((
    'T=t', 'Q1=C', 'E1="important uncertainty"', 'J1="It addresses an important research question."',
    'Q2=C', 'E2="reduces an important uncertainty"', 'J2="It reduces the stated uncertainty."',
    'Q3=U', 'E3="-"', 'J3="-"',
    'Q4=U', 'E4="-"', 'J4="-"'))

class StorageTests(unittest.TestCase):
    def test_valid_and_uncertainty(self):
        selected = parse(RAW, ABSTRACT)
        self.assertEqual(selected['T'], 'T')
        self.assertEqual(metrics(selected), {'score_cents': None, 'coverage_percent': 60,
                         'minimum_cents': 400, 'maximum_cents': 800, 'undetermined_count': 2})

    def test_evidence_and_justification_rules(self):
        for raw in (RAW.replace('important uncertainty', 'invented finding', 1),
                    RAW.replace('J3="-"', 'J3="extra"'),
                    RAW.replace('J2="It reduces the stated uncertainty."', 'J2="-"'),
                    RAW.replace('E3="-"', 'E3="important uncertainty"'),
                    RAW.replace('Q4=U\nE4="-"\nJ4="-"', '')):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                parse(raw, ABSTRACT)

    def test_score_and_storage(self):
        raw = RAW.replace('Q3=U\nE3="-"\nJ3="-"', 'Q3=A\nE3="specific result"\nJ3="-"').replace(
            'Q4=U\nE4="-"\nJ4="-"', 'Q4=A\nE4="derive a specific result"\nJ4="-"')
        with tempfile.TemporaryDirectory() as directory:
            db = open_db(Path(directory)/'v3.sqlite')
            record = save_response(db, arxiv_id='2611.12345v1', model='test', abstract=ABSTRACT, raw=raw)
            row = db.execute('select puntuacion_centesimas,T,Q1,E1,J1,Q4,version_rubrica,validation_error from evaluations where id=?', (record,)).fetchone()
            self.assertEqual(row, (400, 'T', 'C', 'important uncertainty',
                                   'It addresses an important research question.', 'A', 3, None))
            with self.assertRaises(sqlite3.IntegrityError):
                save_response(db, arxiv_id='2611.12345v1', model='test', abstract=ABSTRACT, raw=raw)
            db.close()

if __name__ == '__main__': unittest.main()
