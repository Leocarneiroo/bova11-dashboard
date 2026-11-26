import os
import sys
import tempfile
import unittest


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import bova11_options_tape_flow as mod


class OptionsTapeFlowHistoryTests(unittest.TestCase):
    def test_merge_date_groups_keeps_history_and_overrides_same_date(self):
        history_groups = [
            {"date": "24/04/2026", "iso": "2026-04-24", "expiries": [{"label": "Old"}]},
            {"date": "25/04/2026", "iso": "2026-04-25", "expiries": [{"label": "Keep"}]},
        ]
        current_groups = [
            {"date": "24/04/2026", "iso": "2026-04-24", "expiries": [{"label": "New"}]},
            {"date": "27/04/2026", "iso": "2026-04-27", "expiries": [{"label": "Latest"}]},
        ]

        merged = mod.merge_date_groups(history_groups, current_groups)

        self.assertEqual(
            [group["date"] for group in merged],
            ["24/04/2026", "25/04/2026", "27/04/2026"],
        )
        self.assertEqual(merged[0]["expiries"][0]["label"], "New")
        self.assertEqual(merged[1]["expiries"][0]["label"], "Keep")

    def test_history_roundtrip_preserves_dashboard_groups(self):
        groups = [
            {
                "date": "24/04/2026",
                "iso": "2026-04-24",
                "expiries": [{"label": "15 Mai Mensal", "summary": {"total_volume": 123}}],
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            history_path = os.path.join(tmpdir, "options_history.json")
            mod.save_options_history(history_path, groups)
            loaded = mod.load_options_history(history_path)

        self.assertEqual(loaded, groups)


class OptionsTapeFlowContractRankingTests(unittest.TestCase):
    def test_calc_trade_financial(self):
        trade = {"price": 2.5, "qty": 1000}
        self.assertEqual(mod.calc_trade_financial(trade), 2500.0)

    def test_aggregate_contract_premium_groups_duplicate_contract(self):
        rows = [
            {
                "ticker": "BOVAQ196",
                "side": "call",
                "strike": 196.0,
                "expiry": "15 Mai Mensal",
                "option_delta": 0.21,
                "volume": 1000.0,
                "financial": 2500.0,
                "vwap": 2.5,
                "close": 2.6,
            },
            {
                "ticker": "BOVAQ196",
                "side": "call",
                "strike": 196.0,
                "expiry": "15 Mai Mensal",
                "option_delta": 0.21,
                "volume": 500.0,
                "financial": 1000.0,
                "vwap": 2.0,
                "close": 1.9,
            },
        ]
        grouped = mod.aggregate_contract_premium(rows)
        self.assertEqual(len(grouped), 1)
        item = grouped[0]
        self.assertEqual(item["contract"], "BOVAQ196")
        self.assertEqual(item["type"], "CALL")
        self.assertAlmostEqual(item["total_qty"], 1500.0)
        self.assertAlmostEqual(item["total_financial"], 3500.0)
        self.assertAlmostEqual(item["vwap"], (2.5 * 1000 + 2.0 * 500) / 1500.0)
        self.assertAlmostEqual(item["last_price"], 1.9)

    def test_classify_contract_bucket_edges_and_fallback(self):
        stats = {"p25_financial": 1500.0, "p75_financial": 5000.0}

        po_row = {"type": "CALL", "delta": 0.10, "total_financial": 1200.0}
        self.assertEqual(mod.classify_contract_bucket(po_row, stats), "Po")

        gamma_row = {"type": "PUT", "delta": -0.25, "total_financial": 3000.0}
        self.assertEqual(mod.classify_contract_bucket(gamma_row, stats), "Gamma util")

        itm_call = {"type": "CALL", "delta": 0.75, "total_financial": 5000.0}
        self.assertEqual(mod.classify_contract_bucket(itm_call, stats), "Muito ITM / caro")

        itm_put = {"type": "PUT", "delta": -0.75, "total_financial": 6000.0}
        self.assertEqual(mod.classify_contract_bucket(itm_put, stats), "Muito ITM / caro")

        missing_delta = {"type": "CALL", "delta": None, "total_financial": 999999.0}
        self.assertEqual(mod.classify_contract_bucket(missing_delta, stats), "Intermediario")


if __name__ == "__main__":
    unittest.main()
