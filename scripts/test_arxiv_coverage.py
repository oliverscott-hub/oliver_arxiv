import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import collect_arxiv_category as collector


class CoverageExportTests(unittest.TestCase):
    def test_only_completed_days_including_zero_are_exported(self):
        with tempfile.TemporaryDirectory() as directory:
            source = sqlite3.connect(":memory:")
            source.executescript("""
                CREATE TABLE day_coverage (
                    day_utc TEXT, papers_captured INTEGER,
                    completed_at_utc TEXT, status TEXT);
                CREATE TABLE papers (id TEXT);
                INSERT INTO day_coverage VALUES
                    ('2026-11-06', 3, '2026-11-07T03:00:00Z', 'ok'),
                    ('2026-11-07', 0, '2026-11-08T03:00:00Z', 'ok'),
                    ('2026-11-08', NULL, '2026-11-09T03:00:00Z', 'error');
            """)
            destination = Path(directory) / "sample.coverage.sqlite"
            with patch.object(collector, "COVERAGE_SQLITE_PATH", destination):
                summary = collector.write_coverage_sqlite(source)
            source.close()
            exported = sqlite3.connect(destination)
            try:
                rows = exported.execute(
                    "SELECT day,articles,last_scan FROM daily_coverage ORDER BY day"
                ).fetchall()
            finally:
                exported.close()
            self.assertEqual(rows, [
                ("2026-11-06", 3, "2026-11-07"),
                ("2026-11-07", 0, "2026-11-08"),
            ])
            self.assertEqual(summary["covered_day_count"], 2)
