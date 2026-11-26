from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import bova11_bandas_vol as bandas


def write_csv(folder, tag="8out", label="16 out Mensal", call_iv="20", put_iv="40", call_oi="10", put_oi="30"):
    path = Path(folder) / f"venc {label} fechamento ({tag}).csv"
    row = ["0"] * 23
    month = bandas._MESES_PT[label.split()[1]]
    code = chr(ord("A") + month - 1)
    suffix = f"W{(int(label.split()[0]) + 6)//7}" if " W" in label else ""
    row[0], row[22] = f"BOVA{code}200{suffix}", f"BOVA{chr(ord(code)+12)}200{suffix}"
    row[11], row[7], row[15] = "200", call_iv, put_iv
    row[2], row[20] = call_oi, put_oi
    path.write_text(";".join(["Header"] * 23) + "\n" + ";".join(row) + "\n", encoding="latin-1")
    return path


def fake_yfinance(prices=None):
    if prices is None:
        prices = pd.DataFrame()
    ticker = types.SimpleNamespace(history=lambda **kwargs: prices)
    return types.SimpleNamespace(Ticker=lambda symbol: ticker)


class BandasCalculationFixesTests(unittest.TestCase):
    def analyze(self, folder, **kwargs):
        with patch.dict(sys.modules, {"yfinance": fake_yfinance(), "arch": None}):
            return bandas.run_analysis(folder, "8out", 200, date(2026, 10, 8), **kwargs)

    def test_csv_preserves_missing_iv_and_oi(self):
        with tempfile.TemporaryDirectory() as folder:
            row = bandas.load_b3(write_csv(folder, call_iv="-", put_oi="-"))[0]
        self.assertIsNone(row["call_iv"])
        self.assertIsNone(row["put_oi"])

    def test_atm_requires_iv_for_each_positive_oi_side(self):
        row = {"strike": 200, "call_iv": 20, "put_iv": None, "call_oi": 10, "put_oi": 10}
        self.assertIsNone(bandas.compute_atm_iv([row], 200)[0])

    def test_atm_observed_zero_and_unknown_oi(self):
        row = {"strike": 200, "call_iv": 0, "put_iv": 40, "call_oi": 10, "put_oi": 30}
        self.assertAlmostEqual(bandas.compute_atm_iv([row], 200)[0], .30)
        row["call_oi"] = None
        iv, oi = bandas.compute_atm_iv([row], 200)
        self.assertAlmostEqual(iv, .20)
        self.assertIsNone(oi)
        row["put_iv"] = None
        self.assertIsNone(bandas.compute_atm_iv([row], 200)[0])

    def test_expired_expiry_is_excluded_without_rolling_year(self):
        self.assertLessEqual(bandas.dte_from_label("2 out W1", date(2026, 10, 8)), 0)
        self.assertIsNone(bandas.dte_from_label("invalid", date(2026, 10, 8)))
        self.assertEqual(bandas.dte_from_label("8 jan W2", date(2026, 12, 30)), 7)

    def test_no_csv_means_no_iv_bands_signal_or_fake_stats(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.analyze(folder)
        for key in ("atm_iv", "iv_rank", "iv_percentile", "garch_vol", "hist_vol_30d", "recent_vol_10d", "iv_tendency_pct", "iv_premium"):
            self.assertIsNone(result[key], key)
        self.assertEqual(result["bands"], {})
        self.assertEqual(result["signal"], "N/A")

    def test_invalid_atm_and_expired_rows_do_not_create_band(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, put_iv="-")
            result = self.analyze(folder)
        self.assertIsNone(result["atm_iv"])
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, label="2 out W1")
            result = self.analyze(folder)
        self.assertEqual(result["expiries"], [])

    def test_iv_rank_uses_prior_iv_and_includes_current_in_range(self):
        stats = bandas.compute_iv_statistics(.60, [.20 + i*.01 for i in range(20)])
        self.assertEqual(stats, (100.0, 100.0))
        self.assertEqual(bandas.compute_iv_statistics(.20, [.30] * 20), (0.0, 0.0))
        self.assertEqual(bandas.compute_iv_statistics(.20, [.20] * 20), (None, None))
        self.assertEqual(bandas.compute_iv_statistics(.60, [.20] * 19), (None, None))

    def test_iv_history_prefers_post_and_requires_manual_spot(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, "7out", call_iv="10", put_iv="10")
            write_csv(folder, "7posout", call_iv="30", put_iv="30")
            write_csv(folder, "6out", call_iv="50", put_iv="50")
            path = Path(folder) / "spots.json"
            path.write_text(json.dumps({"2026-10-07": {"spot": 200}, "2026-10-08": {"spot": 200}, "2025-10-01": {"spot": 200}}))
            history = bandas.build_iv_history(folder, date(2026, 10, 8), str(path))
        self.assertEqual(history, {"2026-10-07": .30})

    def test_current_session_prefers_post_without_double_count(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, call_iv="10", put_iv="10")
            write_csv(folder, "8posout", call_iv="30", put_iv="30")
            result = self.analyze(folder)
        self.assertAlmostEqual(result["atm_iv"], .30)
        self.assertEqual(len(result["expiries"]), 1)
        self.assertIsNone(result["iv_rank"])

    def test_rank_from_twenty_csv_sessions_survives_missing_external_prices(self):
        with tempfile.TemporaryDirectory() as folder:
            sessions = list(pd.bdate_range("2026-09-10", "2026-10-07"))
            self.assertEqual(len(sessions), 20)
            spots = {}
            for i, session in enumerate(sessions):
                month = "set" if session.month == 9 else "out"
                write_csv(folder, f"{session.day}{month}", call_iv=str(20+i), put_iv=str(20+i))
                spots[str(session.date())] = {"spot": 200}
            spots["2026-10-08"] = {"spot": 200}
            path = Path(folder) / "spots.json"
            path.write_text(json.dumps(spots))
            write_csv(folder, call_iv="60", put_iv="60")
            result = self.analyze(folder, spot_history_file=str(path))
        self.assertEqual(result["iv_history_count"], 20)
        self.assertEqual(result["iv_rank"], 100)
        self.assertEqual(result["iv_percentile"], 100)
        self.assertIsNone(result["garch_vol"])

    def test_inverse_business_dte_uses_same_weights_in_history_and_current(self):
        with tempfile.TemporaryDirectory() as folder:
            for tag in ("7out", "8out"):
                write_csv(folder, tag, call_iv="20", put_iv="20")
                write_csv(folder, tag, label="23 out W4", call_iv="40", put_iv="40")
            path = Path(folder) / "spots.json"
            path.write_text(json.dumps({"2026-10-07": {"spot": 200}}))
            prior = bandas.build_iv_history(folder, date(2026, 10, 8), str(path))
            current = self.analyze(folder)
        prior_weights = [1/7, 1/12]
        current_weights = [1/6, 1/11]
        self.assertAlmostEqual(prior["2026-10-07"], (.2*prior_weights[0]+.4*prior_weights[1])/sum(prior_weights))
        self.assertAlmostEqual(current["atm_iv"], (.2*current_weights[0]+.4*current_weights[1])/sum(current_weights))

    def test_insufficient_prices_do_not_fake_hv_or_sma(self):
        prices = pd.DataFrame({"Close": [200]*30}, index=pd.bdate_range("2026-08-01", periods=30))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"yfinance": fake_yfinance(prices)}):
            write_csv(folder)
            result = bandas.run_analysis(folder, "8out", 200, date(2026, 10, 8))
        self.assertAlmostEqual(result["atm_iv"], .35)
        self.assertIsNone(result["hist_vol_30d"])
        self.assertIsNone(result["sma_30"])
        self.assertEqual(result["bands"], {})

    def test_missing_iv_in_one_active_expiry_invalidates_global_atm_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            for tag in ("7out", "8out"):
                write_csv(folder, tag, call_iv="20", put_iv="20")
                write_csv(folder, tag, label="23 out W4", call_iv="-", put_iv="-")
            path = Path(folder) / "spots.json"
            path.write_text(json.dumps({"2026-10-07": {"spot": 200}}))
            result = self.analyze(folder, spot_history_file=str(path))
        self.assertIsNone(result["atm_iv"])
        self.assertEqual(result["iv_history_count"], 0)
        self.assertEqual(result["iv_expiry_coverage"], .5)
        self.assertIn("23 out W4", result["missing_iv_expiries"])
        self.assertTrue(any("23 out W4" in warning for warning in result["warnings"]))

    def test_previous_active_expiry_without_current_close_blocks_atm(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder)
            write_csv(folder, "7out", label="23 out W4")
            result = self.analyze(folder)
        self.assertIsNone(result["atm_iv"])
        self.assertIn("23 out W4", result["missing_expiries"])

    def test_volume_only_expiry_without_close_blocks_atm(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder)
            (Path(folder) / "venc 23 out W4 fechamento (8out Volume).csv").write_text("Header\n")
            result = self.analyze(folder)
        self.assertIsNone(result["atm_iv"])
        self.assertIn("23 out W4", result["missing_expiries"])

    def test_arch_fit_failure_labels_hv_fallback(self):
        prices = pd.DataFrame({"Close": [190 + i*.1 + (i%3)*.3 for i in range(60)]}, index=pd.bdate_range("2026-07-01", periods=60))
        def fail_fit(**kwargs):
            raise ValueError("fixture convergence failure")
        arch = types.SimpleNamespace(arch_model=lambda *args, **kwargs: types.SimpleNamespace(fit=fail_fit))
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"yfinance": fake_yfinance(prices), "arch": arch}):
            result = bandas.run_analysis(folder, "8out", 200, date(2026, 10, 8))
            path = Path(folder) / "report.html"
            bandas.generate_html(result, str(path))
            html = path.read_text()
        self.assertEqual(result.get("vol_method"), "HV 30d")
        self.assertEqual(result["garch_vol"], result["hist_vol_30d"])
        self.assertIn("HV 30d anualizado", html)
        self.assertNotIn("EWMA λ=0.94 anualizado", html)

    def test_price_cutoff_is_inclusive_and_realized_metrics_independent_of_iv(self):
        dates = pd.bdate_range("2026-07-01", "2026-10-20")
        hist = pd.DataFrame({"Close": [190 + i*.1 + (i%3)*.3 for i in range(len(dates))]}, index=dates)
        calls = []
        ticker = types.SimpleNamespace(history=lambda **kwargs: calls.append(kwargs) or hist)
        yf = types.SimpleNamespace(Ticker=lambda symbol: ticker)
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules, {"yfinance": yf, "arch": None}):
            result = bandas.run_analysis(folder, "8out", 200, date(2026, 10, 8))
        self.assertEqual(calls[0]["end"], "2026-10-09")
        self.assertEqual(result["price_history_dates"][-1], "2026-10-08")
        self.assertIsNotNone(result["hist_vol_30d"])
        self.assertIsNotNone(result["garch_vol"])
        self.assertIsNone(result["iv_rank"])
        self.assertEqual(result["bands"], {})

    def test_smile_missing_side_is_gap_and_zero_is_observed(self):
        rows = [{"strike": 200, "call_iv": 0, "put_iv": None}]
        self.assertEqual(bandas.compute_smile(rows), ([200], [0], [None]))

    def test_smile_keeps_fully_missing_strike_as_gap(self):
        rows = [{"strike": 190, "call_iv": 20, "put_iv": 30},
                {"strike": 200, "call_iv": None, "put_iv": None},
                {"strike": 210, "call_iv": 30, "put_iv": 40}]
        self.assertEqual(bandas.compute_smile(rows), ([190, 200, 210], [20, None, 30], [30, None, 40]))

    def test_html_missing_values_are_na_and_null_without_reversal_probability(self):
        with tempfile.TemporaryDirectory() as folder:
            data = self.analyze(folder)
            path = Path(folder) / "report.html"
            bandas.generate_html(data, str(path))
            html = path.read_text()
        self.assertIn("N/A", html)
        self.assertIn("const ATM_IV       = null;", html)
        self.assertIn("const BAND_U4      = null;", html)
        self.assertNotIn("Probabilidade de Reversão", html)
        self.assertIn("dias úteis", html)
        self.assertNotIn("None", html)


if __name__ == "__main__":
    unittest.main()
