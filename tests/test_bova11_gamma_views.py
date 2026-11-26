from datetime import datetime
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
sys.path.insert(0, SCRIPTS)
import bova11_market_gamma as market
import bova11_gex_history as history
import bova11_diagnostico_4d as diagnostic


def write_csv(folder, tag="8out", gamma_missing=False, iv="-"):
    path = os.path.join(folder, f"venc 16 out Mensal fechamento ({tag}).csv")
    rows = []
    for strike, call_oi, put_oi in ((180, 10, 100), (200, 300, 10)):
        row = ["0"] * 23
        row[0], row[22] = f"BOVAJ{strike}", f"BOVAV{strike}"
        row[2], row[20], row[11] = str(call_oi), str(put_oi), str(strike)
        row[3], row[19] = "0,5", "-0,5"
        row[4], row[18] = ("-" if gamma_missing else "0,02"), "0,02"
        row[5], row[17], row[6], row[16] = "-0,1", "-0,1", "0,2", "0,2"
        row[7], row[15] = iv, iv
        rows.append(";".join(row))
    with open(path, "w", encoding="latin-1") as fh:
        fh.write(";".join(["Header"] * 23) + "\n" + "\n".join(rows) + "\n")
    return path


class GammaViewsTests(unittest.TestCase):
    def test_invalid_filename_tag_cannot_break_missing_expiry_detection(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, '8out')
            write_csv(folder, '8outbackup')
            self.assertEqual(market.missing_previous_expiries(folder, '8out')['missing_expiries'], [])

    def test_missing_oi_disables_model_without_crashing(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, iv="20,0")
            with open(path, encoding="latin-1") as fh:
                text = fh.read().replace(";10;", ";-;", 1)
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(text)
            rows = market.gamma_model.parse_csv(path)
            result = market.summarize_gamma_profiles([("16 out Mensal", rows)], "8out", 190)
        self.assertEqual(result["regime"], "Indisponível")
        self.assertIsNone(result["flip"])
        self.assertEqual(result["iv_coverage"], 0)

    def test_missing_oi_remains_null_in_both_views(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, iv="20,0")
            with open(path, encoding="latin-1") as fh:
                text = fh.read().replace(";10;", ";-;", 1)
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(text)
            result = diagnostic.calc_greeks(diagnostic.load_b3(path), 190)
            curve = market.to_curve_payload(market.parse_gamma_curve(path, "16 out Mensal", 190), 190)
        self.assertIsNone(result["gex_net"][0])
        self.assertIsNone(result["gex_call"][0])
        self.assertEqual(result["coverage"]["gamma"], 0)
        self.assertIsNone(curve["gamma_atual"])
        self.assertIsNone(curve["net"][0])
        self.assertEqual(curve["gamma_coverage"], 0)

    def test_both_pages_generate_with_unknown_oi_and_quotes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, iv="20,0")
            with open(path, encoding="latin-1") as fh:
                text = fh.read().replace(";10;", ";-;", 1)
            lines = text.splitlines()
            row = lines[1].split(";")
            row[9] = "-"
            lines[1] = ";".join(row)
            with open(path, "w", encoding="latin-1") as fh:
                fh.write("\n".join(lines) + "\n")
            market_html = self.run_view_in_memory(market, folder, ["--spot", "190"])
            diagnostic_html = self.run_view_in_memory(diagnostic, folder, ["--spot-d", "190", "--spot-d1", "189"])
        self.assertIn('"gamma_atual": null', market_html)
        self.assertIn("Diagnóstico indisponível", diagnostic_html)

    def test_execution_skips_rows_with_unknown_inputs(self):
        rows = [{"strike": 180, "call_oi": None, "put_oi": None},
                {"strike": 190, "call_bid": None}]
        self.assertEqual(diagnostic._collapse_rows_for_execution(rows), {})

    def test_no_crossing_does_not_fabricate_last_strike_flip(self):
        self.assertIsNone(diagnostic.find_gamma_flip([180, 190, 200], [10, 20, 30]))

    def test_market_current_gamma_uses_all_strikes(self):
        curve = {180: {"call": 10, "put": -3, "net": 7},
                 200: {"call": 25, "put": -5, "net": 20}}
        payload = market.to_curve_payload(curve, 181)
        self.assertEqual(payload["gamma_atual"], 27)
        self.assertIsNone(payload["flip"])

    def test_missing_iv_disables_model_flip_despite_cumulative_crossing(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder)
            payload = history.compute_gex_for_tag(folder, "8out", 190)
        self.assertIsNone(payload["gamma_flip"])
        self.assertEqual(payload.get("iv_coverage"), 0)
        self.assertEqual(payload["regime"], "Indisponível")
        self.assertEqual(payload.get("static_regime"), "Long Gamma")
        self.assertTrue(payload["total_gex_b"] > 0)

    def test_gamma_coverage_preserves_blank_positive_oi(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, gamma_missing=True)
            curve = market.parse_gamma_curve(path, "16 out Mensal", 190)
            payload = market.to_curve_payload(curve, 190)
        self.assertAlmostEqual(payload.get("gamma_coverage", -1), 110 / 420)
        self.assertIsNone(payload.get("regime"))

    def test_four_dimensional_missing_greeks_disable_signals(self):
        self.assertEqual(diagnostic.determine_signals(None, 1, 1, 1),
                         ("N/A", "N/A", "N/A", "N/A"))

    def test_four_dimensional_csv_blank_is_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = diagnostic.load_b3(write_csv(folder, gamma_missing=True))
        result = diagnostic.calc_greeks(rows, 190)
        self.assertAlmostEqual(result.get("coverage", {}).get("gamma", -1), 110 / 420)

    def test_visual_strike_limits_preserve_total_and_model_flip(self):
        curve = {180: {"call": 10, "put": -3, "net": 7},
                 200: {"call": 25, "put": -5, "net": 20}}
        model = {"flip": 191.2, "profile": [], "iv_coverage": 1.0}
        payload = market.to_curve_payload(curve, 190, model, strike_min=199)
        self.assertEqual(payload["strikes"], [200])
        self.assertEqual(payload["gamma_atual"], 27)
        self.assertEqual(payload["flip"], 191.2)

    def test_missing_previous_active_expiry_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, tag="7out")
            with open(os.path.join(folder, "venc 23 out W4 fechamento (8out).csv"), "w") as fh:
                fh.write("Header\n")
            self.assertEqual(market.missing_previous_expiries(folder, "8out")["missing_expiries"],
                             ["16 out Mensal"])

    def test_incomplete_four_dimensional_html_suppresses_suggestions(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = diagnostic.load_b3(write_csv(folder, gamma_missing=True))
        per_strike = diagnostic.calc_greeks(rows, 190)
        html = diagnostic.build_html("2026-10-08", "8out", 190, 189,
            None, 1, 1, 1, None, None, "N/A", "N/A", "N/A", "N/A", None,
            per_strike, [])
        self.assertIn("Diagnóstico indisponível", html)
        self.assertNotIn('class="active-row"', html)


    def test_four_dimensional_thresholds_follow_rescaled_units(self):
        self.assertEqual(diagnostic.determine_signals(1, 1, 5.01, 2.01),
                         ("LONG", "POS", "ALTO", "ALTO"))
        self.assertEqual(diagnostic.determine_signals(1, 1, 5, 2),
                         ("LONG", "POS", "BAIXO", "BAIXO"))

    def test_previous_expiry_discovery_crosses_year_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            old = write_csv(folder, tag="30dez")
            os.rename(old, os.path.join(folder, "venc 9 jan W2 fechamento (30dez).csv"))
            write_csv(folder, tag="2jan")
            with patch.object(market, "datetime") as clock:
                clock.now.return_value = datetime(2027, 1, 2)
                warning = market.missing_previous_expiries(folder, "2jan")
        self.assertEqual(warning["previous_tag"], "30dez")
        self.assertEqual(warning["missing_expiries"], ["9 jan W2"])

    def test_empty_active_chain_is_explicit_and_has_no_model_regime(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = market.gamma_model.parse_csv(write_csv(folder, iv="20,0"))
        result = market.summarize_gamma_profiles([("16 out Mensal", rows), ("23 out W4", [])], "8out", 190)
        self.assertEqual(result.get("empty_expiries"), ["23 out W4"])
        self.assertEqual(result["regime"], "Indisponível")

    def test_unknown_gamma_is_null_in_market_totals_and_arrays(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, gamma_missing=True)
            curve = market.parse_gamma_curve(path, "16 out Mensal", 190)
            result = market.to_curve_payload(curve, 190)
        self.assertIsNone(result["gamma_atual"])
        self.assertEqual(result["call"], [None, None])
        self.assertEqual(result["net"], [None, None])
        self.assertIsNone(result["gamma_score"])

    def test_unknown_gamma_is_null_in_history_totals_and_arrays(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, gamma_missing=True)
            result = history.compute_gex_for_tag(folder, "8out", 190)
        self.assertIsNone(result["total_gex_b"])
        self.assertEqual(result["gex_call"], [None, None])
        self.assertEqual(result["gex_net"], [None, None])
        self.assertEqual(result["cum_gex"], [None, None])
        self.assertIn("N/A", history.build_html([result], "2026-10-08"))

    def test_unknown_gamma_is_null_in_four_dimensional_arrays(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = diagnostic.load_b3(write_csv(folder, gamma_missing=True))
        result = diagnostic.calc_greeks(rows, 190)
        self.assertEqual(result["gex_call"], [None, None])
        self.assertEqual(result["gex_net"], [None, None])

    def test_reported_zero_gamma_remains_numeric_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder)
            with open(path, encoding="latin-1") as fh:
                content = fh.read().replace("0,02", "0,0000")
            with open(path, "w", encoding="latin-1") as fh:
                fh.write(content)
            market_result = market.to_curve_payload(market.parse_gamma_curve(path, "16 out Mensal", 190), 190)
            history_result = history.compute_gex_for_tag(folder, "8out", 190)
        self.assertEqual(market_result["gamma_atual"], 0)
        self.assertEqual(history_result["total_gex_b"], 0)
        self.assertEqual(history_result["gamma_coverage"], 1)


    @staticmethod
    def add_expired_chain(folder):
        active = write_csv(folder, iv="20,0")
        with open(active, encoding="latin-1") as fh:
            lines = fh.read().splitlines()
        for i in range(1, len(lines)):
            row = lines[i].split(";")
            row[2], row[20], row[4], row[18] = "1,0M", "0", "1,0", "1,0"
            lines[i] = ";".join(row)
        with open(os.path.join(folder, "venc 8 out Mensal fechamento (8out).csv"), "w", encoding="latin-1") as fh:
            fh.write("\n".join(lines) + "\n")
        return active

    @staticmethod
    def run_view_in_memory(module, folder, extra):
        output = os.path.join(folder, "in-memory.html")
        original_open = open
        buffer = io.StringIO()
        def intercept_open(path, *args, **kwargs):
            return contextlib.nullcontext(buffer) if str(path) == output else original_open(path, *args, **kwargs)
        argv = [module.__name__, "--data-dir", folder, "--output", output,
                "--ref-date", "2026-10-08", "--ref-tag", "8out"] + extra
        with patch("sys.argv", argv), patch("builtins.open", side_effect=intercept_open), contextlib.redirect_stdout(io.StringIO()):
            module.main()
        return buffer.getvalue()

    def test_history_static_aggregate_excludes_expired_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, iv="20,0")
            expected = history.compute_gex_for_tag(folder, "8out", 190)
            self.add_expired_chain(folder)
            result = history.compute_gex_for_tag(folder, "8out", 190)
        self.assertAlmostEqual(result["total_gex_b"], expected["total_gex_b"])
        self.assertEqual(result["expired_expiries"], ["8 out Mensal"])

    def test_market_static_aggregate_excludes_expired_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            active = self.add_expired_chain(folder)
            expected = market.to_curve_payload(market.parse_gamma_curve(active, "16 out Mensal", 190), 190)
            html = self.run_view_in_memory(market, folder, ["--spot", "190"])
        result = json.loads(html.split("const DATA = ", 1)[1].split(";\n", 1)[0])
        self.assertAlmostEqual(result["curves"]["AGREGADO"]["gamma_atual"], expected["gamma_atual"])
        self.assertNotIn("8 out Mensal", result["curves"])

    def test_four_dimensional_snapshot_and_execution_exclude_expired_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            active = self.add_expired_chain(folder)
            expected = diagnostic.calc_greeks(diagnostic.load_b3(active), 190)
            with patch.object(diagnostic, "build_html", wraps=diagnostic.build_html) as html_builder, \
                 patch.object(diagnostic, "generate_strategy_suggestions", return_value=[]) as suggestions:
                self.run_view_in_memory(diagnostic, folder, ["--spot-d", "190", "--spot-d1", "189"])
        arguments = html_builder.call_args.kwargs
        self.assertAlmostEqual(arguments["total_gex"], sum(expected["gex_net"]) / 1e6)
        self.assertEqual([expiry["expiry"] for expiry in suggestions.call_args.kwargs["expiries"]], ["16 out Mensal"])


    def test_previous_expiry_on_reference_day_is_not_missing_active_chain(self):
        with tempfile.TemporaryDirectory() as folder:
            previous = write_csv(folder, tag="7out")
            os.rename(previous, os.path.join(folder, "venc 8 out Mensal fechamento (7out).csv"))
            write_csv(folder, tag="8out")
            self.assertEqual(market.missing_previous_expiries(folder, "8out")["missing_expiries"], [])



if __name__ == "__main__":
    unittest.main()
