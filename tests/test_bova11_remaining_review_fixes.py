import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
for path in (ROOT, SCRIPTS):
    if path not in sys.path:
        sys.path.insert(0, path)

import bova11_runner
import bova11_skew_history as skew_history
import bova11_tex_dex_vex as tex_dex_vex
import bova11_trade_score_james as trade_score
from bova11_shared import expiry_label_to_iso_nearest, tag_to_iso_nearest


class RemainingReviewFixesTests(unittest.TestCase):
    @staticmethod
    def _write_expiries(folder, tag, timestamp):
        for expiry in ("9 out W2", "16 out Mensal"):
            path = os.path.join(folder, f"venc {expiry} fechamento ({tag}).csv")
            with open(path, "w", encoding="utf-8"):
                pass
            os.utime(path, (timestamp, timestamp))

    def test_active_date_discovery_handles_year_boundary_and_market_suffixes(self):
        cases = (
            ("31dez", datetime(2026, 12, 31, 18).timestamp()),
            ("2jan", datetime(2027, 1, 2, 18).timestamp()),
        )
        for module in (bova11_runner, trade_score, tex_dex_vex):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as folder:
                for tag, timestamp in cases:
                    self._write_expiries(folder, tag, timestamp)
                with patch.object(module, "DATA_DIR", folder):
                    self.assertEqual(module.discover_dates(), ("2jan", "31dez"))

        for module in (bova11_runner, trade_score, tex_dex_vex):
            with self.subTest(module=module.__name__, case="pre/post"), tempfile.TemporaryDirectory() as folder:
                self._write_expiries(folder, "5out", datetime(2026, 10, 5, 18).timestamp())
                self._write_expiries(folder, "5posout", datetime(2026, 10, 5, 22).timestamp())
                with patch.object(module, "DATA_DIR", folder):
                    self.assertEqual(module.discover_dates(), ("5posout", None))

    def test_previous_session_date_uses_prior_year_at_new_year(self):
        self.assertEqual(tag_to_iso_nearest("2jan", datetime(2027, 1, 2).date()), "2027-01-02")
        self.assertEqual(
            tag_to_iso_nearest("31dez", datetime(2027, 1, 2).date()),
            "2026-12-31",
        )
        self.assertEqual(
            expiry_label_to_iso_nearest("9 jan W2", "2026-12-31"),
            "2027-01-09",
        )

    def test_skew_history_extracts_pre_and_post_market_tags(self):
        self.assertEqual(
            skew_history.extract_date_from_filename("venc 9 out W2 fechamento (6posout).csv"),
            ("06", "out", "06/out"),
        )
        self.assertEqual(
            skew_history.extract_date_from_filename("venc 9 out W2 fechamento (5preout).csv"),
            ("05", "out", "05/out"),
        )

    def test_skew_history_keeps_dates_missing_from_current_csv_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            data_dir = os.path.join(folder, "data")
            output_dir = os.path.join(folder, "output")
            os.makedirs(data_dir)
            os.makedirs(output_dir)
            history_path = os.path.join(output_dir, "history.json")
            with open(history_path, "w", encoding="utf-8") as fh:
                json.dump({"31/mai": {"old expiry": {"skew": 1.2}}}, fh)

            headers = ["Ativo", "Último", "C. Abertos", "Delta", "Gamma", "Theta", "Vega",
                       "Vol Impl", "Negócios", "Bid", "Ask", "Strike", "Bid", "Ask",
                       "Negócios", "Vol Impl", "Vega", "Theta", "Gamma", "Delta",
                       "C. Abertos", "Último", "Ativo.1"]
            row = ["0"] * 23
            row[0], row[7], row[11], row[15], row[22] = (
                "BOVAJ190W2", "20,00", "190,00", "25,00", "BOVAV190W2"
            )
            csv_path = os.path.join(data_dir, "venc 9 out W2 fechamento (6out).csv")
            with open(csv_path, "w", encoding="latin-1") as fh:
                fh.write(";".join(headers) + "\n" + ";".join(row) + "\n")

            with patch.object(skew_history, "CSV_DIR", data_dir), \
                 patch.object(skew_history, "OUTPUT_DIR", output_dir), \
                 patch.object(skew_history, "HISTORY_FILE", "history.json"), \
                 patch.object(skew_history, "ask_strike_range", return_value=(180, 220)), \
                 patch.object(skew_history, "generate_html", return_value="<html></html>"), \
                 contextlib.redirect_stdout(io.StringIO()):
                skew_history.main()

            with open(history_path, encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertIn("31/mai", saved)
            self.assertIn("06/out", saved)


if __name__ == "__main__":
    unittest.main()
