import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import pandas as pd
import bova11_expiry_family as family
import bova11_tex_dex_vex as profiles
import bova11_max_pain as pain
import bova11_gravity as gravity
import bova11_historical_data as history


def frame():
    return pd.DataFrame([
        dict(strike=190, call_ticker='BOVAJ190', put_ticker='BOVAV190', call_oi=10, put_oi=0,
             call_gamma=.02, put_gamma=None, call_delta=.5, put_delta=None, call_iv=20, put_iv=None),
        dict(strike=200, call_ticker='BOVAJ200', put_ticker='BOVAV200', call_oi=10, put_oi=0,
             call_gamma=None, put_gamma=None, call_delta=None, put_delta=None, call_iv=None, put_iv=None),
    ])


def write_csv(folder, tag='8out', missing=True, duplicate=False, label='16 out Mensal'):
    path = Path(folder) / f'venc {label} fechamento ({tag}).csv'
    row = ['0'] * 23
    row[0], row[22] = 'BOVAJ190', 'BOVAV190'
    row[2], row[20], row[11] = '10', '0', '190'
    row[3], row[4], row[7] = '0,5', '-' if missing else '0,02', '20'
    row[15] = '-'
    row = ';'.join(row)
    path.write_text(';'.join(['header'] * 23) + '\n' + row + '\n' + (row + '\n' if duplicate else ''), encoding='latin-1')
    return path


class ExposureFixTests(unittest.TestCase):
    def test_numeric_parsers_preserve_unknown(self):
        for parser in (profiles.parse_num, pain._parse_br_number, gravity._p, history._p):
            for raw in (None, '-', 'NaN', float('nan')):
                self.assertIsNone(parser(raw), (parser.__name__, raw))

    def test_atm_iv_observed_zero_keeps_its_weight(self):
        self.assertEqual(family.weighted_atm_iv([{"call_oi":10, "put_oi":30, "call_iv":0, "put_iv":40}]), 30)

    def test_inventory_uses_selected_session_and_previous_post(self):
        with tempfile.TemporaryDirectory() as folder:
            selected = write_csv(folder)
            write_csv(folder, tag='8posout')
            write_csv(folder, tag='7out', label='23 out W4')
            write_csv(folder, tag='7posout', label='23 out W4')
            inventory = family.expiration_inventory(folder, '8out', '2026-10-08')
        self.assertEqual(Path(inventory[0]['close']).name, selected.name)

    def test_collapse_keeps_unknown_oi(self):
        data = frame()
        unknown = data.iloc[[0]].copy()
        unknown['call_ticker'] = 'BOVAJ191'
        unknown['call_oi'] = None
        collapsed = family.collapse_option_rows_by_strike(pd.concat([data, unknown]))
        self.assertTrue(pd.isna(collapsed.loc[0, 'call_oi']))

    def test_missing_volume_does_not_score_zero_activity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, missing=False)
            from datetime import date
            cfg = dict(label='16 out Mensal', exp_date='2026-10-16', exp_type='Mensal', file=path.name, vol_file='none.csv')
            result = pain.analyze_expiration(Path(folder), cfg, date(2026,10,8), 190)
        self.assertIsNone(result.total_call_vol)
        self.assertEqual([criterion for criterion in result.criteria if criterion.name == 'vol_activity'][0].detail, 'N/A')

    def test_gravity_empty_active_expiry_is_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, missing=False)
            (Path(folder) / 'venc 23 out W4 fechamento (8out).csv').write_text('Header\n')
            from datetime import date
            result = gravity.analyze_all(Path(folder), date(2026,10,8), '8out', 190)
        self.assertTrue(any('23 out w4' in warning.lower() for warning in result[0].warnings))

    def test_invalid_oi_cannot_cancel_other_positions(self):
        data = frame()
        data.loc[0, 'call_oi'] = -10
        collapsed = family.collapse_option_rows_by_strike(data)
        self.assertTrue(pd.isna(collapsed.loc[0, 'call_oi']))
        _, mp, _ = pain.calc_max_pain(data)
        self.assertIsNone(mp)

    def test_raw_exposure_deduplicates_and_preserves_partial(self):
        data = frame()
        data = pd.concat([data, data.iloc[[0]]], ignore_index=True)
        for module, func in ((pain, pain.calc_gex), (gravity, gravity.calc_exposures)):
            result = func(data, 190)
            self.assertAlmostEqual(result.loc[0, 'call_gex'], .02 * 10 * 190**2 * .01)
            self.assertEqual(result.loc[0, 'put_gex'], 0)
            self.assertTrue(pd.isna(result.loc[1, 'net_gex']))
            self.assertEqual(result.attrs.get('gamma_coverage'), .5)
            self.assertAlmostEqual(result.attrs.get('partial_net_gex'), .02 * 10 * 190**2 * .01)

    def test_max_pain_unknown_oi_is_unavailable(self):
        data = frame()
        data.loc[0, 'call_oi'] = None
        for function in (pain.calc_max_pain, gravity.calc_max_pain):
            _, mp, loss = function(data)
            self.assertIsNone(mp)
            self.assertIsNone(loss)

    def test_observed_zero_oi_is_complete_zero_exposure(self):
        rows = [{'strike':190, 'c_oi':0, 'p_oi':0, 'c_iv':None, 'p_iv':None}]
        profile = profiles.build_unified_spot_profile(rows, '16 out Mensal', '8out', [190], spot_ref=190)
        aggregate = profiles.aggregate_spot_profiles([profile])[0]
        self.assertEqual(aggregate['gex_net'], 0)
        self.assertEqual(aggregate['iv_coverage'], 1)

    def test_profile_unknown_oi_invalidates_model(self):
        result = profiles.build_unified_spot_profile([
            {'strike':190, 'c_oi':None, 'p_oi':0, 'c_iv':20, 'p_iv':None}],
            '16 out Mensal', '8out', [190], spot_ref=190)[0]
        self.assertIsNone(result['gex_net'])
        self.assertIsNone(result['oi_total'])
        self.assertLess(result['iv_coverage'], 1)

    def test_profile_html_preserves_partial_json_metadata(self):
        profile = profiles.build_unified_spot_profile([{'strike':190, 'c_oi':10, 'p_oi':10, 'c_iv':20, 'p_iv':None}], '16 out Mensal', '8out', [190], spot_ref=190)
        aggregate = profiles.aggregate_spot_profiles([profile])
        context = {'avg_iv_d':None, 'avg_iv_d1':None, 'iv_direction':None, 'per_strike_delta_iv':{}}
        html = profiles.generate_html(aggregate, context, '7out', '8out', '7/out', '8/out', 189, 190, {'16 out Mensal':profile}, ['16 out Mensal'])
        self.assertIn('"partial_gex_net"', html)
        self.assertIn('"oi_total"', html)

    def test_profile_missing_expiry_disables_full_coverage(self):
        valid = profiles.build_unified_spot_profile([{'strike':190, 'c_oi':10, 'p_oi':0, 'c_iv':20, 'p_iv':None}], '16 out Mensal', '8out', [190], spot_ref=190)
        missing = profiles.build_unified_spot_profile([], '23 out W4', '8out', [190], spot_ref=190)
        aggregate = profiles.aggregate_spot_profiles([valid, missing])[0]
        self.assertIsNone(aggregate['gex_net'])
        self.assertLess(aggregate['iv_coverage'], 1)
        self.assertGreater(aggregate['partial_gex_net'], 0)

    def test_max_pain_unknown_oi_renders_without_nan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder)
            content = path.read_text(encoding='latin-1').replace(';10;', ';-;')
            path.write_text(content, encoding='latin-1')
            from datetime import date
            cfg = dict(label='16 out Mensal', exp_date='2026-10-16', exp_type='Mensal', file=path.name, vol_file='none.csv')
            result = pain.analyze_expiration(Path(folder), cfg, date(2026,10,8), 190)
            self.assertIn('N/A', pain.build_exp_card_html(result, 0))
            html = pain.generate_dashboard([result], 190, date(2026,10,8), '8out')
            self.assertNotIn('NaN', html)

    def test_gravity_flip_uses_spot_profile_and_ignores_touch(self):
        self.assertIsNone(gravity.find_gex_flip(pd.DataFrame({'spot':[180,190,200], 'gex_net':[1,0,1]}), 190))
        self.assertEqual(gravity.find_gex_flip(pd.DataFrame({'spot':[180,190,200], 'gex_net':[-1,0,1]}), 190), 190)
        self.assertIsNone(gravity.find_gex_flip(pd.DataFrame({'strike':[180,190,200], 'net_gex':[-1,0,1]}), 190))

    def test_pcr_denominator_zero_explicit(self):
        self.assertEqual(pain.score_pcr(0, 10).detail, 'Somente puts')
        self.assertIsNone(pain.score_pcr(0, 10).raw_value)
        self.assertEqual(pain.score_pcr(0, 0).detail, 'N/A')

    def test_published_rounded_totals_close_to_published_sides(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, missing=False)
            lines = path.read_text(encoding='latin-1').splitlines()
            row = lines[1].split(';')
            row[2] = row[20] = '0,5'
            lines[1] = ';'.join(row)
            path.write_text('\n'.join(lines)+'\n', encoding='latin-1')
            volume = ['0'] * 11
            volume[0], volume[10], volume[5] = 'BOVAJ190', 'BOVAV190', '190'
            volume[1] = volume[9] = '0,5'
            (Path(folder) / 'venc 16 out Mensal fechamento (8out Volume).csv').write_text(';'.join(['header']*11)+'\n'+';'.join(volume)+'\n', encoding='latin-1')
            result = history.build_snapshot(folder, '8out', 2026, {'2026-10-08':190}, '', {})
        self.assertEqual(result['oi_total'], result['oi_call'] + result['oi_put'])
        self.assertEqual(result['vol_total'], result['vol_call'] + result['vol_put'])

    def test_historical_missing_greek_and_duplicate(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, duplicate=True)
            result = history.build_snapshot(folder, '8out', 2026, {'2026-10-08':190}, '', {})
        self.assertEqual(result['oi_call'], 10)
        self.assertIsNone(result['gex_net'])
        self.assertEqual(result['iv_atm'], .2)

    def test_historical_excludes_expired_and_reports_previous_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, missing=False)
            write_csv(folder, missing=False, label='8 out Mensal')
            write_csv(folder, tag='7out', label='23 out W4')
            result = history.build_snapshot(folder, '8out', 2026, {'2026-10-08':190}, '', {})
        self.assertIsNone(result['oi_total'])
        self.assertIsNone(result['gex_net'])
        self.assertIn('23 out W4', result['missing_expiries'])
        self.assertEqual(result['partial_oi_call'], 10)

    def test_incomplete_analysis_html_has_null_and_na(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder)
            cfg = dict(label='16 out Mensal', exp_date='2026-10-16', exp_type='Mensal', file=path.name, vol_file='none.csv')
            from datetime import date
            result = pain.analyze_expiration(Path(folder), cfg, date(2026,10,8), 190)
            html = pain.build_exp_card_html(result, 0)
            self.assertIsNone(result.net_gex)
            self.assertIn('null', html)
            self.assertNotIn('NaN', html)
            gravity_result = gravity.analyze_all(Path(folder), date(2026,10,8), '8out', 190)[0]
            self.assertIsNone(gravity_result.net_gex)
            self.assertIn('N/A', gravity.build_card(gravity_result, 0))

    def test_gravity_model_flip_is_saved_separately_from_static_exposure(self):
        with tempfile.TemporaryDirectory() as folder:
            write_csv(folder, missing=False)
            from datetime import date
            result = gravity.analyze_all(Path(folder), date(2026,10,8), '8out', 190)[0]
        self.assertEqual(result.model_iv_coverage, 1)
        self.assertEqual(result.gex_flips, [])
        self.assertIsNone(result.gex_flip)
        self.assertGreater(result.net_gex, 0)

    def test_gravity_html_distinguishes_unavailable_model_from_no_crossing(self):
        analysis = gravity.FullAnalysis(label='16 out Mensal', spot=190, max_pain=190, mu_oi=190,
            mu_gex=190, mu_dex=190, net_gex=1, net_dex=1)
        visible = gravity.build_card(analysis, 0).split('<script>', 1)[0]
        self.assertIn('Regime GEX estático', visible)
        self.assertIn('Regime GEX(S) modelado', visible)
        self.assertIn('Long Gamma', visible)
        self.assertIn('Indisponível', visible)
        self.assertIn('Cobertura IV do modelo', visible)
        self.assertIn('0.0%', visible)
        analysis.model_iv_coverage, analysis.model_regime = 1, 'Long Gamma'
        visible = gravity.build_card(analysis, 0).split('<script>', 1)[0]
        self.assertIn('Sem cruzamento', visible)
        self.assertIn('100.0%', visible)

    def test_gravity_html_lists_all_model_roots_with_coverage_when_static_missing(self):
        import json
        import re
        analysis = gravity.FullAnalysis(label='16 out Mensal', spot=190, max_pain=190, mu_oi=190,
            mu_gex=190, mu_dex=190, net_gex=1, net_dex=1, model_iv_coverage=1,
            model_regime='Short Gamma', gex_flip=185.25, gex_flips=[185.25,194.75])
        for static_gex in (1, None):
            analysis.net_gex = static_gex
            html = gravity.build_card(analysis, 0)
            visible = re.sub(r'<script.*?</script>', '', html, flags=re.S)
            self.assertIn('Todas as raízes', visible)
            self.assertIn('R$ 185.25', visible)
            self.assertIn('R$ 194.75', visible)
            self.assertIn('Short Gamma', visible)
            self.assertIn('100.0%', visible)
        payload = json.loads(re.findall(r'<script type="application/json">(.*?)</script>', html, re.S)[0])
        self.assertEqual(payload['model_regime'], 'Short Gamma')
        self.assertEqual(payload['model_iv_coverage'], 1)
        self.assertEqual(payload['gex_flips'], [185.25,194.75])

    def test_historical_html_shows_collection_warning_and_coverage(self):
        import re
        import subprocess
        snapshot = {'date':'2026-10-08', 'display':'8/Out', 'warnings':['Vencimento ativo ausente: 23 out W4'], 'expiry_coverage':.5, 'gamma_coverage':.25, 'delta_coverage':.75}
        html = history.build_html([snapshot], '2026-10-08')
        script = re.findall(r'<script>(.*?)</script>', html, re.S)[-1]
        prelude = "const elements = {}; global.document = {getElementById:id => elements[id] ||= {innerHTML:'', textContent:''}, querySelectorAll:()=>[]}; global.Chart = class {destroy(){}};"
        assertions = "if (!elements['coverage-status']?.textContent.includes('23 out W4')) throw Error('Warning hidden'); if (!elements['rows'].innerHTML.includes('50.0%')) throw Error('Coverage hidden');"
        result = subprocess.run(['node', '-e', prelude + script + assertions], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_historical_no_oi_requires_both_atm_ivs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_csv(folder, missing=False)
            path.write_text(path.read_text(encoding='latin-1').replace(';10;', ';0;'), encoding='latin-1')
            result = history.build_snapshot(folder, '8out', 2026, {'2026-10-08':190}, '', {})
        self.assertIsNone(result['iv_atm'])


if __name__ == '__main__':
    unittest.main()
