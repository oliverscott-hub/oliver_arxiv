import datetime as dt
import unittest
from pathlib import Path

from pdf_weekday import DAY_GROUPS, group_for_timestamp, qwen_pdf_db


class PdfWeekdayTests(unittest.TestCase):
    def test_every_utc_weekday_has_one_group(self):
        monday = dt.datetime(2026, 11, 9, tzinfo=dt.timezone.utc)
        expected = ("monday", "tuesday", "wednesday", "thursday", "friday", "weekend", "weekend")
        self.assertEqual(tuple(group_for_timestamp((monday + dt.timedelta(days=i)).isoformat())
                               for i in range(7)), expected)
        self.assertEqual(set(DAY_GROUPS), set(expected))

    def test_offset_is_converted_to_utc_before_partitioning(self):
        self.assertEqual(group_for_timestamp("2026-11-09T00:30:00+01:00"), "weekend")
        self.assertEqual(group_for_timestamp("2026-11-09T00:30:00Z"), "monday")

    def test_monday_keeps_existing_database_name(self):
        root = Path("repo")
        self.assertEqual(qwen_pdf_db(root, "monday").name, "pdf_answers_qwen3_rag_8b.sqlite")
        self.assertEqual(qwen_pdf_db(root, "weekend").name, "pdf_answers_qwen3_rag_8b_weekend.sqlite")
        with self.assertRaises(ValueError):
            qwen_pdf_db(root, "sunday")


if __name__ == "__main__":
    unittest.main()
