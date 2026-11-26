import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import bova11_insights_auto as insights


def option(strike=203, iv=25, gamma=.01):
    row = {'strike': strike}
    for side in ('c', 'p'):
        row.update({f'{side}_oi': 100, f'{side}_iv': iv, f'{side}_last': 2,
                    f'{side}_gamma': gamma, f'{side}_delta': .5 if side == 'c' else -.5,
                    f'{side}_theta': -.1, f'{side}_vega': .2})
    return row


class InsightsFixes(unittest.TestCase):
    def test_aggregate_flip_status_distinguishes_unavailable_from_no_crossing(self):
        ranking = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, [option()], [], {}, {}, 203)
        gamma = dict(net=-100, flip=None, dom_s=203, dom_n=-100)
        result = insights.generate_insights({'x': ranking}, {'x': gamma}, 203, '8/out')
        for regime, expected in [('Indisponível', 'Indisponível'), ('Short Gamma', 'Sem cruzamento')]:
            with tempfile.TemporaryDirectory() as folder, patch.object(insights, 'OUTPUT_DIR', folder):
                insights.generate_html(result, 203, -100, None, 'Short Gamma', 0, 0, '7/out', '8/out',
                                       aggregate_model_regime=regime)
                html = next(Path(folder).glob('*.html')).read_text()
            self.assertIn('Regime modelado agregado', html)
            self.assertIn(expected, html)
            self.assertNotIn('Indisponível / sem cruzamento', html)

    def test_main_reports_active_missing_expiry_and_keeps_close_without_volume(self):
        with tempfile.TemporaryDirectory() as folder:
            for expiry, tag in [('23 out W4', '7out'), ('30 out W5', '8out')]:
                Path(folder, f'venc {expiry} fechamento ({tag}).csv').write_text('Header\n')
            files = [dict(label='30 out W5', fech_d='venc 30 out W5 fechamento (8out).csv',
                          fech_d1=None, vol_d=None, vol_d1=None)]
            with patch.object(insights, 'CSV_DIR', folder),                  patch.object(insights, 'discover_dates', return_value=('8out', '7out')),                  patch.object(insights, 'discover_expirations', return_value=files),                  patch.object(insights, 'parse_fech_csv', return_value=[option()]),                  patch.object(insights, 'ask_spot', return_value=203),                  patch.object(insights, 'generate_html') as generate,                  contextlib.redirect_stdout(io.StringIO()):
                insights.main()
            self.assertIsNone(generate.call_args.args[2])
            self.assertEqual(generate.call_args.kwargs['missing_expiries'], ['23 out W4'])

    def test_positive_iv_is_described_as_increase(self):
        rows1, rows2 = [option(iv=23)], [option(iv=25)]
        r = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, rows2, rows1, {}, {}, 203)
        g = dict(net=-100, flip=None, dom_s=203, dom_n=-100)
        text = insights.generate_insights({'x': r}, {'x': g}, 203, '8/out')[0]
        self.assertIn('aumento', text['narrative'])
        self.assertNotIn('theta crush', text['narrative'])
        self.assertEqual(text['regime'], 'Short Gamma')

    def test_missing_iv_stays_unavailable_in_metrics_and_html(self):
        r = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, [option(iv=None)], [], {}, {}, 203)
        self.assertIsNone(r['iv_c_atm'])
        self.assertIsNone(r['div_c_atm'])
        g = dict(net=-100, flip=None, dom_s=203, dom_n=-100,
                 model_regime='Indisponível', flips=[], iv_coverage=0)
        result = insights.generate_insights({'x': r}, {'x': g}, 203, '8/out')
        self.assertIn('indisponível', result[0]['narrative'])
        with tempfile.TemporaryDirectory() as folder, patch.object(insights, 'OUTPUT_DIR', folder):
            insights.generate_html(result, 203, -100, None, 'Short Gamma', 200, 0, '7/out', '8/out')
            html = next(Path(folder).glob('*.html')).read_text()
        self.assertIn('N/A', html)
        self.assertNotIn('acima do flip', html)

    def test_missing_previous_iv_does_not_imply_stability(self):
        r = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, [option(iv=25)], [], {}, {}, 203)
        self.assertIsNone(r['iv_c_atm1'])
        self.assertIsNone(r['div_c_atm'])

    def test_unknown_oi_survives_html_and_totals(self):
        row = option(); row['c_oi'] = None
        r = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, [row], [], {}, {}, 203)
        self.assertIsNone(r['coi'])
        g = insights.build_gex_data([row], '23 out W4', '8out', 203)
        self.assertIsNone(g['net'])
        result = insights.generate_insights({'x': r}, {'x': g}, 203, '8/out')
        with tempfile.TemporaryDirectory() as folder, patch.object(insights, 'OUTPUT_DIR', folder):
            insights.generate_html(result, 203, None, None, 'Indisponível', None, 0, '7/out', '8/out')

    def test_missing_volume_is_not_zero(self):
        r = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, [option()], [], {}, {}, 203)
        self.assertIsNone(r['cvol'])
        self.assertIsNone(r['d_tvol'])

    def test_missing_gamma_does_not_fabricate_static_zero(self):
        rows = [option(gamma=None)]
        g = insights.build_gex_data(rows, '23 out W4', '8out', 203)
        self.assertIsNone(g['net'])
        self.assertEqual(g['static_regime'], 'Indisponível')
        r = insights.build_exp_data({'name': '23 out W4', 'color': 'red'}, rows, [], {}, {}, 203)
        result = insights.generate_insights({'x': r}, {'x': g}, 203, '8/out')
        with tempfile.TemporaryDirectory() as folder, patch.object(insights, 'OUTPUT_DIR', folder):
            insights.generate_html(result, 203, None, None, 'Indisponível', 200, 0, '7/out', '8/out')
            html = next(Path(folder).glob('*.html')).read_text()
            self.assertIn('Indisponível', html)
            self.assertNotIn('dominante em K0', html)

    def test_modeled_flip_uses_all_strikes_and_common_spot_grid(self):
        rows = [option(strike=203)]
        rows[0]['p_oi'] = 300
        g = insights.build_gex_data(rows, '23 out W4', '8out', 203.08)
        self.assertLess(g['net'], 0)
        self.assertEqual(g['static_regime'], 'Short Gamma')
        self.assertEqual(g['model_regime'], 'Short Gamma')
        self.assertIsNone(g['flip'])
        self.assertIn(203.08, [p['spot'] for p in g['profile']])


if __name__ == '__main__':
    unittest.main()
