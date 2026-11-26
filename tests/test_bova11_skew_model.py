import os
import sys
import unittest
import json
import re
import shutil
import subprocess
from datetime import date
from unittest.mock import patch

import numpy as np
import pandas as pd

SCRIPTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import bova11_skew_prediction as mod


def daily_fixture(n=30):
    x = np.arange(n, dtype=float)
    return pd.DataFrame({
        "date": [d.date() for d in pd.bdate_range("2026-03-02", periods=n)],
        "close": 190 + np.sin(x) + x / 10,
        "rr25": np.sin(x / 3), "bf25": np.cos(x / 4),
        "atm_iv": 20 + np.sin(x / 4), "term_slope": np.sin(x / 5),
        "delta_oi_put": x * x, "delta_oi_call": x,
        "notional_put": x + 20, "notional_call": x + 10,
        "delta_flow": np.nan, "vega_flow": np.nan,
        "put_call_voi": np.cos(x / 3),
        "net_gex": x * x + 1, "net_dex": x + 1,
        "dist_flip_raw": x + 1, "vanna_charm_proxy": x + 2,
    })


class SkewModelTests(unittest.TestCase):
    def test_unresolved_price_targets_stay_missing(self):
        daily = daily_fixture(4)
        daily.loc[1, "close"] = np.nan
        features = mod.build_features(daily, mod.Config())
        self.assertTrue(features.loc[[0, 1, 3], "target_down"].isna().all())

    def test_missing_option_session_uses_full_spot_calendar(self):
        daily = daily_fixture(3).iloc[[0, 2]].copy()
        spots = pd.DataFrame({"date": daily_fixture(3)["date"], "close": [190, 180, 200]})
        daily["close"] = [190, 200]
        features = mod.build_features(daily, mod.Config(), spot_df=spots)
        self.assertEqual(features.loc[0, "target_down"], 1.0)
        self.assertEqual(features.loc[0, "next_close"], 180)
        self.assertTrue(pd.isna(features.loc[1, "target_down"]))

    def test_missing_spot_quote_does_not_skip_to_later_price(self):
        daily = daily_fixture(3).iloc[[0, 2]].copy()
        spots = pd.DataFrame({"date": daily_fixture(3)["date"], "close": [190, np.nan, 200]})
        features = mod.build_features(daily, mod.Config(), spot_df=spots)
        self.assertTrue(pd.isna(features.loc[0, "target_down"]))
        features = mod.build_features(daily, mod.Config())
        self.assertTrue(pd.isna(features.loc[0, "target_down"]))

    def test_target_horizon_counts_spot_sessions(self):
        daily = daily_fixture(3).iloc[[0]].copy()
        daily["close"] = 190
        spots = pd.DataFrame({"date": daily_fixture(3)["date"], "close": [190, 180, 200]})
        features = mod.build_features(daily, mod.Config(target_horizon=2), spot_df=spots)
        self.assertEqual(features.loc[0, "target_down"], 0.0)
        self.assertEqual(features.loc[0, "next_close"], 200)

    def test_session_absent_from_both_spots_and_options_does_not_shorten_horizon(self):
        daily = daily_fixture(3).iloc[[0,2]].copy()
        features = mod.build_features(daily, mod.Config(), spot_df=daily[['date','close']])
        self.assertTrue(pd.isna(features.loc[0,'target_down']))

    def test_partial_gamma_is_unknown_with_explicit_oi_coverage(self):
        chain = pd.DataFrame({'strike':[100,105], 'gamma_call':[.02,np.nan], 'gamma_put':[.01,.02],
                              'oi_call':[100,100], 'oi_put':[100,100]})
        result=mod.compute_gex_dex(chain,100)
        self.assertTrue(pd.isna(result['net_gex']))
        self.assertEqual(result['gamma_coverage'],.75)

    def test_missing_oi_cannot_publish_complete_dex_or_coverage(self):
        chain=pd.DataFrame({'strike':[100,105], 'gamma_call':[.02,.02], 'gamma_put':[.01,.02],
            'oi_call':[100,np.nan], 'oi_put':[100,100], 'delta_call':[.5,.5], 'delta_put':[-.5,-.5]})
        result=mod.compute_gex_dex(chain,100)
        self.assertTrue(pd.isna(result['net_dex']))
        self.assertTrue(pd.isna(result['gamma_coverage']))

    def test_new_skew_history_never_replaces_legacy_target(self):
        legacy='/tmp/skew.json'
        self.assertNotEqual(mod.resolve_history_output(legacy),legacy)
        self.assertEqual(mod.resolve_history_output('/tmp/skew.skew-change-v3.json'), '/tmp/skew.skew-change-v3.json')

    def test_neutral_and_unknown_labels_are_excluded_from_directional_backtest(self):
        frame = pd.DataFrame({
            "date": [date(2026, 3, i) for i in range(2, 6)],
            "prob_bear": [0.5, 0.7, 0.3, 0.8], "target_down": [0, 1, 0, 1],
            "signal_label": ["NEUTRO", "BEAR FORTE", "BULL FORTE", "SEM DADOS"],
        })
        bt = mod.run_simple_backtest(frame)
        self.assertEqual(len(bt), 2)
        self.assertEqual(bt["hit"].tolist(), [1, 1])

    def test_diagnostics_report_counts_baselines_and_chronological_tail(self):
        bt = pd.DataFrame({
            "date": [date(2026, 3, i) for i in reversed(range(2, 12))],
            "target_down": [0, 0, 0, 1, 1, 1, 1, 1, 1, 1],
            "pred_down": [1] * 10, "hit": [0, 0, 0, 1, 1, 1, 1, 1, 1, 1],
        })
        summary = mod.backtest_summary(bt)
        self.assertEqual(summary["n"], 10)
        self.assertAlmostEqual(summary["majority_baseline"], 0.7)
        self.assertEqual(summary["diagnostic_n"], 3)
        self.assertEqual(summary["diagnostic_accuracy"], 0.0)
        self.assertEqual(summary["diagnostic_baseline"], 0.0)

    def test_daily_closing_snapshot_cannot_measure_aggression(self):
        self.assertTrue(np.isnan(mod.classify_aggression(2.1, 2.0, 2.1)))
        chain = pd.DataFrame({
            "strike": [190], "price_call": [2.1], "price_put": [2.0],
            "bid_call": [2.0], "ask_call": [2.1], "bid_put": [1.9], "ask_put": [2.0],
            "vol_call": [100], "vol_put": [200], "oi_call": [300], "oi_put": [400],
            "delta_call": [.5], "delta_put": [-.5], "vega_call": [1.0], "vega_put": [1.0],
        })
        flows = mod.compute_notional_and_flows(chain)
        self.assertTrue(np.isnan(flows["delta_flow"]))
        self.assertTrue(np.isnan(flows["vega_flow"]))

    def test_supported_flow_weights_renormalize_and_warmup_remains(self):
        features = mod.build_features(daily_fixture(), mod.Config(z_window=5))
        self.assertTrue(features.loc[:3, "score_final"].isna().all())
        last = features.iloc[-1]
        expected_flow = (.25 * last["z_put_call_oi_change"] + .20 * last["z_put_call_voi"]) / .45
        self.assertAlmostEqual(last["score_flow"], expected_flow)
        scores = [(last[k], w) for k, w in [("score_surface", .3), ("score_flow", .3), ("score_regime", .2)] if pd.notna(last[k])]
        expected_raw = sum(v * w for v, w in scores) / sum(w for _, w in scores)
        self.assertAlmostEqual(last["score_final_raw"], expected_raw)

    def test_insufficient_available_inputs_cannot_emit_score(self):
        daily = daily_fixture()
        daily[["bf25", "term_slope", "delta_oi_put", "delta_oi_call", "put_call_voi", "close"]] = np.nan
        daily["rr25"] = 1.0
        features = mod.build_features(daily, mod.Config(z_window=5))
        self.assertTrue(features["score_final"].isna().all())
        self.assertTrue((features["main_driver"] == "N/A").all())

    def test_gex_and_flip_are_context_without_directional_weight(self):
        daily = daily_fixture()
        before = mod.build_features(daily, mod.Config(z_window=5))
        daily["net_gex"] = -daily["net_gex"] * 50
        daily["dist_flip_raw"] = -daily["dist_flip_raw"] * 100
        after = mod.build_features(daily, mod.Config(z_window=5))
        pd.testing.assert_series_equal(before["score_final"], after["score_final"])
        self.assertTrue(after["score_greeks"].isna().all())

    def test_gex_units_are_shared_one_percent_move_and_strike_crossing_is_not_flip(self):
        chain = pd.DataFrame({"strike": [180, 200], "gamma_call": [.02, .01],
                              "gamma_put": [.01, .02], "oi_call": [100, 100], "oi_put": [100, 100]})
        result = mod.compute_gex_dex(chain, 190)
        self.assertAlmostEqual(result["net_gex"], 0.0)
        self.assertTrue(np.isnan(result["gamma_flip"]))
        chain.loc[1, "oi_call"] = 200
        self.assertAlmostEqual(mod.compute_gex_dex(chain, 190)["net_gex"], 361.0)

    def test_flip_uses_spot_profile_api_when_expiry_context_is_available(self):
        chain = pd.DataFrame({"strike": [190], "gamma_call": [.02], "gamma_put": [.01],
                              "oi_call": [100], "oi_put": [100], "iv_call": [20], "iv_put": [22]})
        summary = {"profile": [], "flip": 187.5, "flips": [187.5], "regime": "POSITIVE", "iv_coverage": 1.0}
        with patch.object(mod, "gamma_profile_summary", return_value=summary, create=True) as profile:
            result = mod.compute_gex_dex(chain, 190, venc_name="17 abr Mensal", tag_d="2mar")
        self.assertEqual(result["gamma_flip"], 187.5)
        self.assertEqual(profile.call_args.kwargs["rows"][0]["c_iv"], 20)
        self.assertEqual(profile.call_args.kwargs["spot_ref"], 190)

    def test_missing_iv_does_not_create_gamma_flip(self):
        chain = pd.DataFrame({"strike": [180, 200], "gamma_call": [.02, .01], "gamma_put": [.01, .02],
                              "oi_call": [100, 100], "oi_put": [100, 100], "iv_call": [np.nan, np.nan], "iv_put": [np.nan, np.nan]})
        result = mod.compute_gex_dex(chain, 190, venc_name="17 abr Mensal", tag_d="2mar")
        self.assertTrue(np.isnan(result["gamma_flip"]))

    def test_daily_flip_is_root_of_sum_of_aligned_profiles_not_average_of_roots(self):
        chain = pd.DataFrame({
            "strike": [180, 200], "delta_call": [.75, .25], "delta_put": [-.25, -.75],
            "iv_call": [20, 21], "iv_put": [22, 23], "gamma_call": [.02, .01], "gamma_put": [.01, .02],
        })
        volume = pd.DataFrame({"strike": [180, 200], "vol_call": [100, 100], "vol_put": [100, 100],
                               "oi_call": [100, 100], "oi_put": [100, 100]})
        grids = []

        def summary(rows, venc_name, tag_d, spot_ref, **kwargs):
            grid = kwargs.get("spot_range", [180, 200])
            grids.append(grid)
            slope, root = (1, 185) if "abr" in venc_name else (3, 200)
            profile = [{"spot": s, "gex_call": slope * (s - root), "gex_put": 0,
                        "gex_net": slope * (s - root), "oi_total": 400, "oi_with_iv": 400, "iv_coverage": 1.0} for s in grid]
            return {"profile": profile, "flip": root, "flips": [root], "regime": "POSITIVE", "iv_coverage": 1.0}

        spots = pd.DataFrame({"date": [date(2026, 3, 2)], "close": [190]})
        with patch.object(mod.os, "listdir", return_value=["venc 17 abr Mensal fechamento (2mar).csv", "venc 15 mai Mensal fechamento (2mar).csv"]), \
             patch.object(mod, "read_option_chain", return_value=chain), \
             patch.object(mod, "find_matching_volume_file", return_value="volume.csv"), \
             patch.object(mod, "read_volume_oi", return_value=volume), \
             patch.object(mod, "gamma_profile_summary", side_effect=summary):
            daily = mod.process_folder("unused", spots, mod.Config())
        self.assertAlmostEqual(daily.iloc[0]["gamma_flip"], 196.25)
        self.assertEqual(grids[0], grids[1])

    def test_html_calls_sigmoid_a_transformed_score_and_diagnostics_not_validation(self):
        features = mod.build_features(daily_fixture(), mod.Config())
        rows = mod.build_rows_for_frontend(features)
        html = mod.build_html(rows, mod.Config(), mod.backtest_summary(pd.DataFrame()), "unused")
        self.assertIn("Score transformado", html)
        self.assertIn("não calibrado", html)
        self.assertIn("fora da amostra", html)
        self.assertIn("Maioria", html)
        self.assertNotIn("Probabilidade de viés", html)
        self.assertNotIn("Modelo probabilistico", html)

    @unittest.skipUnless(shutil.which('node'), 'Node unavailable for frontend execution check')
    def test_html_bootstrap_renders_rows_and_charts(self):
        features=mod.build_features(daily_fixture(), mod.Config())
        rows=mod.build_rows_for_frontend(features)
        html=mod.build_html(rows, mod.Config(), mod.backtest_summary(pd.DataFrame()), 'unused')
        scripts=re.findall(r'<script[^>]*>(.*?)</script>',html,re.S)
        script=next(code for code in scripts if 'const ROWS' in code)
        harness='''const vm=require('vm'),fs=require('fs');
const code=JSON.parse(fs.readFileSync(0,'utf8'));
const nodes={};let charts=0;
const document={getElementById:id=>(nodes[id]??={innerHTML:''}),documentElement:{getAttribute:()=>null}};
const sandbox={document,getComputedStyle:()=>({getPropertyValue:()=>''}),toggleTheme:()=>{},
Chart:class {constructor(){charts++}destroy(){}}};
vm.runInNewContext(code,sandbox);
if(!nodes.rowsBody.innerHTML.includes('<tr>')||charts!==2)throw Error('Bootstrap did not render');'''
        result=subprocess.run([shutil.which('node'),'-e',harness],input=json.dumps(script),text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)


if __name__ == "__main__":
    unittest.main()
