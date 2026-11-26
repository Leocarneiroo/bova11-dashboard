import os
import sys
import tempfile
import unittest
from datetime import date, datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bova11_runner as runner


class RunnerDateDiscoveryTests(unittest.TestCase):
    def _write_expiries(self, folder, tag, timestamp):
        paths = []
        for expiry in ("9 out W2", "16 out Mensal"):
            path = os.path.join(folder, f"venc {expiry} fechamento ({tag}).csv")
            with open(path, "w", encoding="utf-8"):
                pass
            os.utime(path, (timestamp, timestamp))
            paths.append(path)
        return paths

    def test_discovery_orders_dates_across_year_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            dec = datetime(2026, 12, 31, 18).timestamp()
            jan = datetime(2027, 1, 2, 18).timestamp()
            self._write_expiries(folder, "31dez", dec)
            self._write_expiries(folder, "2jan", jan)
            old_dir = runner.DATA_DIR
            runner.DATA_DIR = folder
            try:
                self.assertEqual(runner.discover_dates(), ("2jan", "31dez"))
            finally:
                runner.DATA_DIR = old_dir

    def test_discovery_deduplicates_pre_and_post_market_tags(self):
        with tempfile.TemporaryDirectory() as folder:
            normal_time = datetime(2026, 10, 5, 18).timestamp()
            post_time = datetime(2026, 10, 5, 22).timestamp()
            self._write_expiries(folder, "5out", normal_time)
            self._write_expiries(folder, "5posout", post_time)
            old_dir = runner.DATA_DIR
            runner.DATA_DIR = folder
            try:
                self.assertEqual(runner.discover_dates(), ("5posout", None))
            finally:
                runner.DATA_DIR = old_dir

    def test_tag_date_uses_nearest_year_and_rejects_invalid_date(self):
        self.assertEqual(
            runner.tag_to_iso_date("31dez", reference_date=date(2027, 1, 2)),
            "2026-12-31",
        )
        self.assertIsNone(
            runner.tag_to_iso_date("32xyz", reference_date=date(2027, 1, 2))
        )


if __name__ == "__main__":
    unittest.main()
