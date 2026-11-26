import copy
import json
import io
from contextlib import redirect_stdout
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bova11_demand_flow as demand
from bova11_expiry_family import collapse_option_rows_by_strike


def contracts(spot=100.0, date="2026-10-08"):
    rows = []
    for i, strike in enumerate((95, 100, 105)):
        rows.append(dict(strike=strike, call_ticker=f"BOVAJ{strike}", put_ticker=f"BOVAV{strike}",
                         call_oi=1000.0, put_oi=1000.0, call_delta=.5, put_delta=-.5,
                         call_gamma=.01, put_gamma=.01, call_iv=.2, put_iv=.2,
                         call_theta=-.1, put_theta=-.1, call_vega=.2, put_vega=.2,
                         call_mid=3.0, put_mid=3.0,
                         call_premium_notional=300.0, put_premium_notional=300.0,
                         call_net_gex=1000*spot**2*.01*.01,
                         put_net_gex=-1000*spot**2*.01*.01,
                         call_gex_abs=1000*spot**2*.01*.01,
                         put_gex_abs=1000*spot**2*.01*.01,
                         net_gex=0.0, spot_proxy=spot, moneyness=strike/spot-1,
                         call_charm_abs=1.0, put_charm_abs=1.0,
                         call_vanna_proxy=1.0, put_vanna_proxy=1.0,
                         date=date))
    return pd.DataFrame(rows)


class CollapseContractTests(unittest.TestCase):
    def test_oi_weighted_greek_preserves_exposure(self):
        rows = pd.DataFrame([
            dict(strike=100, call_ticker="BOVAJ100", put_ticker="BOVAV100", call_oi=1000, call_gamma=.01),
            dict(strike=100, call_ticker="BOVAJ101", put_ticker="BOVAV101", call_oi=1, call_gamma=.1),
        ])
        row = collapse_option_rows_by_strike(rows).iloc[0]
        self.assertAlmostEqual(row.call_gamma * row.call_oi, 10.1)

    def test_duplicate_contract_export_does_not_double_oi(self):
        row = dict(strike=100, call_ticker="BOVAJ100", put_ticker="BOVAV100", call_oi=10, put_oi=20, call_gamma=.1)
        result = collapse_option_rows_by_strike(pd.DataFrame([row, row]))
        self.assertEqual(result.iloc[0].call_oi, 10)

    def test_blank_identity_keeps_distinct_rows_but_deduplicates_exact_exports(self):
        rows = [dict(strike=100, call_ticker="", put_ticker="", call_oi=10, call_gamma=.1),
                dict(strike=100, call_ticker="", put_ticker="", call_oi=20, call_gamma=.2)]
        row = collapse_option_rows_by_strike(pd.DataFrame(rows + [rows[0]])).iloc[0]
        self.assertEqual(row.call_oi, 30)
        self.assertAlmostEqual(row.call_gamma * row.call_oi, 5)

    def test_unknown_positive_oi_greek_propagates_missing(self):
        rows = pd.DataFrame([dict(strike=100, call_oi=1000, call_gamma=.01),
                             dict(strike=100, call_oi=1, call_gamma=np.nan)])
        row = collapse_option_rows_by_strike(rows).iloc[0]
        self.assertTrue(math.isnan(row.call_gamma))

    def test_zero_oi_missing_greek_does_not_destroy_known_exposure(self):
        rows = pd.DataFrame([dict(strike=100, call_oi=1000, call_gamma=.01),
                             dict(strike=100, call_oi=0, call_gamma=np.nan)])
        row = collapse_option_rows_by_strike(rows).iloc[0]
        self.assertAlmostEqual(row.call_gamma * row.call_oi, 10)

    def test_iv_missing_for_positive_oi_invalidates_weighted_total(self):
        rows = pd.DataFrame([dict(strike=100, call_oi=1000, call_iv=.2),
                             dict(strike=100, call_oi=1, call_iv=.8),
                             dict(strike=100, call_oi=1000, call_iv=np.nan)])
        row = collapse_option_rows_by_strike(rows).iloc[0]
        self.assertTrue(np.isnan(row.call_iv))
        complete = collapse_option_rows_by_strike(rows.iloc[:2]).iloc[0]
        self.assertAlmostEqual(complete.call_iv, 200.8/1001)


class DemandModelTests(unittest.TestCase):
    def test_robust_z_caps_tiny_mad_and_preserves_missing(self):
        values = demand.robust_z(pd.Series([0, 1e-12, 2e-12, 100, np.nan]))
        self.assertLessEqual(values.dropna().abs().max(), 3)
        self.assertTrue(math.isnan(values.iloc[-1]))

    def test_without_prior_contracts_score_is_na(self):
        result = demand.score_day(contracts())
        self.assertIsNone(result["score"])
        self.assertEqual(result["signal"], "N/A")
        self.assertEqual(result["coverage"], 0)

    def test_skew_uses_iv_change_not_put_call_intrinsic_prices(self):
        previous = contracts(date="2026-10-07")
        current = previous.copy()
        current["date"] = "2026-10-08"
        current["put_mid"] = [20, 3, 1]
        current["call_mid"] = [1, 3, 20]
        result = demand.score_day(current, previous)
        self.assertEqual(result["components"]["skew"], 0)
        self.assertEqual(result["signal"], "Base")
        self.assertNotIn("charm", result["component_weights"])
        self.assertNotIn("vanna", result["component_weights"])
        self.assertEqual(result["ref_date_d1"], "2026-10-07")

    def test_low_coverage_does_not_turn_missing_into_neutral(self):
        current, previous = contracts(), contracts(date="2026-10-07")
        for col in ("call_iv", "put_iv", "call_net_gex", "put_net_gex", "net_gex",
                    "call_premium_notional", "put_premium_notional"):
            previous[col] = np.nan
        result = demand.score_day(current, previous)
        self.assertLess(result["coverage"], .5)
        self.assertIsNone(result["score"])
        self.assertEqual(result["signal"], "N/A")
        self.assertIsNone(result["components"]["skew"])
        json.loads(demand._safe_json(result))
        self.assertNotIn("NaN", demand._safe_json(result))

    def test_comparison_uses_tickers_not_just_strike(self):
        previous, current = contracts(date="2026-10-07"), contracts()
        current["call_ticker"] = ["BOVAJ199", "BOVAJ200", "BOVAJ201"]
        result = demand.score_day(current, previous)
        self.assertIsNone(result["score"])
        self.assertEqual(result["matched_contracts"], 0)

    def test_blank_tickers_do_not_claim_comparable_contracts(self):
        previous, current = contracts(date="2026-10-07"), contracts()
        current["call_ticker"] = ""
        previous["call_ticker"] = ""
        result = demand.score_day(current, previous)
        self.assertIsNone(result["score"])

    def test_both_days_spot_used_for_comparison_weight(self):
        previous, current = contracts(spot=90, date="2026-10-07"), contracts()
        result = demand.score_day(current, previous)
        expected = np.exp(-10 * ((current.moneyness.abs() + previous.moneyness.abs()) / 2)) * 2000
        expected /= expected.sum()
        self.assertAlmostEqual(result["rows"][0]["atm_weight"], expected.iloc[0])

    def test_build_day_preserves_contracts_and_canonical_gex(self):
        frame = contracts().drop(columns=["call_mid", "put_mid", "call_charm_abs", "put_charm_abs", "call_vanna_proxy", "put_vanna_proxy"])
        frame["call_bid"] = 2; frame["call_ask"] = 4
        frame["put_bid"] = 2; frame["put_ask"] = 4
        extra = frame.iloc[1].copy()
        extra["call_ticker"] = "BOVAJ101"; extra["put_ticker"] = "BOVAV101"
        frame = pd.concat([frame, extra.to_frame().T], ignore_index=True)
        with patch.object(demand, "read_close", return_value=frame):
            day = demand.build_day(Path("unused.csv"), None, "16 Out — Mensal", "2026-10-08", "2026-10-16", "Mensal", 100)
        self.assertEqual(len(day), 4)
        self.assertAlmostEqual(day.iloc[0]["call_net_gex"], 1000)
        self.assertTrue(day["call_premium_notional"].isna().all())
        self.assertNotIn("call_charm_abs", day.columns)

    def test_aggregate_weights_expiries_by_usable_oi(self):
        result = demand.score_day(contracts(), contracts(date="2026-10-07"))
        large, small = copy.deepcopy(result), copy.deepcopy(result)
        large.update(score=1.0, aggregation_weight=1000)
        small.update(score=-1.0, aggregation_weight=1)
        payload = demand.build_payload([dict(expiry="large", result=large), dict(expiry="small", result=small)], {})
        self.assertAlmostEqual(payload["demand"]["AGG"]["score"], 999/1001, places=5)

    def test_aggregate_coverage_includes_expiry_without_d1(self):
        valid = demand.score_day(contracts(), contracts(date="2026-10-07"))
        missing = demand.score_day(contracts())
        result = demand.build_payload([dict(expiry="valid", result=valid), dict(expiry="missing", result=missing)], {})["demand"]["AGG"]
        self.assertAlmostEqual(result["coverage"], .5)
        self.assertTrue(all(abs(value - .5) < 1e-9 for value in result["component_coverage"].values()))
        self.assertEqual(result["total_oi"], 12000)
        self.assertAlmostEqual(result["available_oi_fraction"], .5)
        self.assertEqual(result["available_expiries"], 1)
        self.assertEqual(result["total_expiries"], 2)
        self.assertIsNotNone(result["score"])

    def test_aggregate_global_low_coverage_gates_score_and_history(self):
        valid = demand.score_day(contracts(), contracts(date="2026-10-07"))
        chain = contracts()
        chain["call_oi"] *= 2
        chain["put_oi"] *= 2
        missing = demand.score_day(chain)
        results = [dict(expiry="valid", result=valid), dict(expiry="missing", result=missing)]
        history = {}
        for item in results:
            demand.upsert_history(history, "2026-10-08", item["expiry"], item["result"])
        payload = demand.build_payload(results, history)
        for agg in (payload["demand"]["AGG"], payload["history"]["2026-10-08"]["AGG"]):
            self.assertAlmostEqual(agg["coverage"], 1/3)
            self.assertIsNone(agg["score"])
            self.assertEqual(agg["signal"], "N/A")
        html = demand.build_html(results, history, "2026-10-08", "8out", 100, 100)
        self.assertIn("OI utilizável", html)
        self.assertIn("Vencimentos utilizáveis", html)

    def test_history_without_universe_denominator_has_unknown_aggregate_coverage(self):
        valid = demand.score_day(contracts(), contracts(date="2026-10-07"))
        valid.pop("total_oi", None)
        history = {"2026-10-08@" + demand.SCHEMA_VERSION: {"valid": valid}}
        agg = demand.build_payload([], history)["history"]["2026-10-08"]["AGG"]
        self.assertIsNone(agg["coverage"])
        self.assertIsNone(agg["score"])

    def test_aggregate_component_contributions_reconstruct_score(self):
        template = demand.score_day(contracts(), contracts(date="2026-10-07"))
        large, small = copy.deepcopy(template), copy.deepcopy(template)
        large.update(score=2.0, aggregation_weight=2, components={key: (2.0 if key == "skew" else 0.0) for key in demand.WEIGHTS}, component_weights={key: (1.0 if key == "skew" else 0.0) for key in demand.WEIGHTS})
        small.update(score=-1.0, aggregation_weight=1, components={key: (-1.0 if key == "oi" else 0.0) for key in demand.WEIGHTS}, component_weights={key: (1.0 if key == "oi" else 0.0) for key in demand.WEIGHTS})
        agg = demand.build_payload([dict(expiry="large", result=large), dict(expiry="small", result=small)], {})["demand"]["AGG"]
        reconstructed = sum((value or 0) * agg["component_weights"][key] for key, value in agg["components"].items())
        self.assertAlmostEqual(reconstructed, agg["score"])

    def test_duplicate_blank_contracts_remain_na_without_merge_crash(self):
        current = contracts()
        current["call_ticker"] = ""
        current["put_ticker"] = ""
        current.loc[1, "strike"] = current.loc[0, "strike"]
        result = demand.score_day(current, contracts(date="2026-10-07"))
        self.assertEqual(result["matched_contracts"], 0)
        self.assertIsNone(result["score"])

    def test_historical_prior_tag_resolves_across_year_and_ignores_later_sessions(self):
        with tempfile.TemporaryDirectory() as folder:
            for tag in ("30dez", "31posdez", "2jan", "3jan"):
                Path(folder, f"venc 16 jan Mensal fechamento ({tag}).csv").touch()
            with patch.object(demand, "discover_date_tags", return_value=("3jan", "2jan")):
                self.assertEqual(demand.resolve_previous_session(folder, "2jan", "2027-01-02"), ("31posdez", "2026-12-31"))

    def test_discovery_includes_volume_without_close(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "venc 16 out Mensal fechamento (8out Volume).csv").touch()
            found = demand.discover_expirations_with_volume(folder, "8out", 2026)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["label_raw"], "16 out mensal")
        self.assertEqual(found[0]["close"], "")

    def test_main_missing_or_empty_close_blocks_aggregate_and_history(self):
        for kind in ("missing", "empty"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as folder:
                Path(folder, "venc 16 out Mensal fechamento (8out Volume).csv").touch()
                if kind == "empty":
                    Path(folder, "venc 16 out Mensal fechamento (8out).csv").write_text("Ativo;Ativo;Strike\n")
                Path(folder, "venc 23 out W4 fechamento (8out).csv").touch()
                Path(folder, "venc 23 out W4 fechamento (7out).csv").touch()
                output = Path(folder, "report.html")
                actual_build = demand.build_day
                def build(close, volume, label, session, exp_date, exp_type, spot_override):
                    if "16 out" in str(close):
                        return actual_build(close, volume, label, session, exp_date, exp_type, spot_override)
                    return contracts(spot=spot_override, date=session)
                argv = ["demand", "--data-dir", folder, "--output", str(output), "--ref-date", "2026-10-08", "--ref-tag", "8out", "--spot-d", "100", "--spot-d1", "100"]
                with patch.object(sys, "argv", argv), patch.object(demand, "build_day", side_effect=build), \
                     patch.object(demand, "load_history", return_value={}), patch.object(demand, "save_history") as save, redirect_stdout(io.StringIO()):
                    demand.main()
                history = save.call_args.args[0]
                payload = demand.build_payload([], history)["history"]["2026-10-08"]
                self.assertIsNotNone(payload["23 Out — W4"]["score"])
                self.assertIsNone(payload["AGG"]["score"])
                self.assertIsNone(payload["AGG"]["coverage"])
                self.assertFalse(payload["AGG"]["universe_complete"])
                self.assertIn("16 Out — Mensal", payload["AGG"]["missing_expiries"])
                self.assertIn("Universo incompleto", output.read_text())
                self.assertIn("16 Out — Mensal", output.read_text())

    def test_main_builds_both_sessions_with_their_manual_spot(self):
        with tempfile.TemporaryDirectory() as folder:
            expiry = dict(label_raw="16 out mensal", label="16 Out — Mensal", exp_date="2026-10-16", exp_type="Mensal", volume="")
            def expiries(data_dir, tag, year):
                return [dict(expiry, close=f"{tag}.csv")]
            def day(close, volume, label, session, exp_date, exp_type, spot_override):
                return contracts(spot=spot_override, date=session)
            argv = ["demand", "--data-dir", folder, "--output", str(Path(folder, "report.html")),
                    "--ref-date", "2026-10-08", "--ref-tag", "8out", "--spot-d", "100", "--spot-d1", "90"]
            with patch.object(sys, "argv", argv), patch.object(demand, "discover_date_tags", return_value=("8out", "7out")), \
                 patch.object(demand, "discover_expirations_with_volume", side_effect=expiries), \
                 patch.object(demand, "build_day", side_effect=day) as build, \
                 patch.object(demand, "load_history", return_value={}), patch.object(demand, "save_history"), redirect_stdout(io.StringIO()):
                demand.main()
            sessions = {call.args[3]: call.kwargs["spot_override"] for call in build.call_args_list}
            self.assertEqual(sessions, {"2026-10-08": 100.0, "2026-10-07": 90.0})
            html = Path(folder, "report.html").read_text()
            self.assertIn("D-1: 2026-10-07", html)
            self.assertIn("Peso ativo", html)
            self.assertNotIn("NaN", html)

    def test_legacy_history_untouched_and_new_version_separate(self):
        history = {"2026-10-08": {"16 Out — Mensal": {"score": 999, "signal": "Bear"}}}
        old = copy.deepcopy(history["2026-10-08"])
        result = demand.score_day(contracts(), contracts(date="2026-10-07"))
        demand.upsert_history(history, "2026-10-08", "16 Out — Mensal", result)
        self.assertEqual(history["2026-10-08"], old)
        self.assertTrue(any(k.startswith("2026-10-08@") for k in history))
        self.assertEqual(demand.build_payload([dict(expiry="16 Out — Mensal", result=result)], history)["history"]["2026-10-08"]["16 Out — Mensal"]["schema_version"], demand.SCHEMA_VERSION)


if __name__ == "__main__":
    unittest.main()
