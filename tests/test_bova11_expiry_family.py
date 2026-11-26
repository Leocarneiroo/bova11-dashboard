import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import pandas as pd

import bova11_auto
from bova11_expiry_family import filter_expiry_family


class ExpiryFamilyTests(unittest.TestCase):
    def test_weekly_family_filters_both_option_legs(self):
        rows = pd.DataFrame([
            {"strike": 190, "call_ticker": "BOVAJ190W2", "put_ticker": "BOVAV190W2"},
            {"strike": 190, "call_ticker": "BOVAJ190W2", "put_ticker": "BOVAV190W4"},
            {"strike": 190, "call_ticker": "BOVAJ190W4", "put_ticker": "BOVAV190W4"},
            {"strike": 190, "call_ticker": "BOVAJ190W5", "put_ticker": "BOVAV190W5"},
        ])

        filtered = filter_expiry_family(rows, "2026-10-09", "Semanal")

        self.assertEqual(filtered[["call_ticker", "put_ticker"]].values.tolist(), [
            ["BOVAJ190W2", "BOVAV190W2"],
        ])

    def test_dashboard_parser_filters_before_deduplicating_strikes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "venc 9 out W2 fechamento (6out).csv")
            header = [f"col{i}" for i in range(23)]
            rows = []
            for suffix, call_oi in (("W4", "400"), ("W2", "100"), ("W5", "500")):
                row = ["0"] * 23
                row[0] = f"BOVAJ190{suffix}"
                row[2] = call_oi
                row[11] = "190,00"
                row[22] = f"BOVAV190{suffix}"
                rows.append(";".join(row))
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(";".join(header) + "\n" + "\n".join(rows))

            parsed = bova11_auto.parse_fech_csv(path)

        self.assertEqual([row["c_oi"] for row in parsed], [100])


if __name__ == "__main__":
    unittest.main()
