import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict
from datetime import date
from unittest.mock import patch
from pathlib import Path

import pandas as pd

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import bova11_oi_stats_light as oi
import bova11_hunter_walls as hunter
import bova11_options_tape_flow as tape
import bova11_options_contract_premium as premium


def leg(delta=None, side='call'):
    return tape.summarize_trades([
        {'qty': 40, 'price': 2.5, 'side': 'buy', 'time_s': 1, 'row': 1},
        {'qty': 10, 'price': 2.0, 'side': 'sell', 'time_s': 2, 'row': 2},
    ], 'BOVAQ200', 200., side, delta)


def expiry():
    return tape.combine_expiry('15 Mai Mensal', [leg(.5)], [leg(None, 'put')], '', '', {}, {})


def groups(exp=None):
    return [{'date': '24/04/2026', 'iso': '2026-04-24', 'expiries': [exp or expiry()]}]


class StatisticsAndTapeFixTests(unittest.TestCase):
    def test_oi_parser_preserves_missing_cells_and_observed_zero(self):
        columns = ['0'] * 23
        columns[0], columns[22], columns[11] = 'BOVAJ200', 'BOVAV200', '200'
        columns[2], columns[7], columns[3], columns[18] = '-', '', '--', 'invalid'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'venc 16 out Mensal fechamento (8out).csv'
            path.write_text(';'.join(['Header'] * 23) + '\n' + ';'.join(columns), encoding='latin-1')
            row = oi.load_b3(path).iloc[0]
        for key in ('call_oi', 'call_iv', 'call_delta', 'put_gamma'):
            self.assertTrue(pd.isna(row[key]), key)
        self.assertEqual(row['put_oi'], 0)

    def test_unknown_oi_disables_max_pain_moments_and_reports_observed_contribution(self):
        frame = pd.DataFrame({'strike':[199.,200.,201.], 'call_oi':[10.,None,30.], 'put_oi':[20.,0.,40.]})
        curve, max_pain, loss = oi.calc_max_pain(frame)
        self.assertIsNone(max_pain)
        self.assertIsNone(loss)
        self.assertTrue(curve.empty)
        result = oi.compute_oi_stats(frame, max_pain, 200.)
        for key in ('total_oi_sum', 'max_pain', 'oi_weighted_mean', 'oi_weighted_std',
                    'skewness', 'kurtosis', 'hhi', 'gini', 'entropy', 'normal_r_squared'):
            self.assertIsNone(getattr(result, key), key)
        self.assertAlmostEqual(result.oi_coverage, 5/6)
        self.assertEqual(result.partial_oi_sum, 100.)
        self.assertIsNone(result.call_oi[1])
        doc = oi.generate_dashboard([oi.ExpResult('16 Out Mensal', '2026-10-16', 'Mensal', 8, result)], date(2026,10,8), '8out')
        self.assertIn('Contribuição observada', doc)
        self.assertIn('83.3%', doc)
        self.assertIn('N/A', doc)
        self.assertNotRegex(doc.lower(), r'\bnan\b')

    def test_oi_inventory_retains_active_expiry_without_closing_source(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'venc 23 out W4 fechamento (8out Volume).csv').write_text('header\n', encoding='latin-1')
            configs = oi._discover_expirations(folder, '8out')
            self.assertEqual(len(configs), 1)
            result = oi.analyze_one(folder, configs[0], date(2026,10,8), 200.)
        self.assertIsNotNone(result)
        self.assertIsNone(result.dist.total_oi_sum)
        self.assertEqual(result.dist.oi_coverage, 0.)
        self.assertIn('N/A', oi.build_exp_html(result, 0))

    def test_partial_observed_oi_survives_same_strike_consolidation(self):
        frame = pd.DataFrame({'strike':[200.,200.], 'call_oi':[10.,None], 'put_oi':[20.,30.], 'call_delta':[.5,.5]})
        config = dict(file='source.csv', label='16 Out Mensal', type='Mensal', exp_date='2026-10-16')
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / 'source.csv').touch()
            with patch.object(oi, 'load_b3', return_value=frame):
                result = oi.analyze_one(folder, config, date(2026,10,8), 200.)
        self.assertIsNone(result.dist.total_oi_sum)
        self.assertEqual(result.dist.partial_oi_sum, 60.)
        self.assertAlmostEqual(result.dist.oi_coverage, .75)

    def test_invalid_strike_does_not_certify_empty_oi_inventory(self):
        columns = ['10'] * 23
        columns[0], columns[22], columns[11] = 'BOVAJ200', 'BOVAV200', '-'
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            name = 'venc 16 out Mensal fechamento (8out).csv'
            (folder / name).write_text(';'.join(['Header'] * 23) + '\n' + ';'.join(columns), encoding='latin-1')
            config = dict(file=name, label='16 Out Mensal', type='Mensal', exp_date='2026-10-16')
            result = oi.analyze_one(folder, config, date(2026,10,8), 200.)
        self.assertEqual(result.dist.oi_coverage, 0.)
        self.assertIsNone(result.dist.total_oi_sum)

    def test_discovery_relative_directory_does_not_duplicate_path(self):
        columns = ['10'] * 23
        columns[0], columns[22], columns[11] = 'BOVAJ200', 'BOVAV200', '200'
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            folder = Path(directory)
            relative = folder.relative_to(Path.cwd())
            (folder / 'venc 16 out Mensal fechamento (8out).csv').write_text(
                ';'.join(['Header'] * 23) + '\n' + ';'.join(columns), encoding='latin-1')
            configs = oi._discover_expirations(relative, '8out', date(2026,10,8))
            result = oi.analyze_one(relative, configs[0], date(2026,10,8), 200.)
        self.assertEqual(result.dist.oi_coverage, 1.)
        self.assertEqual(result.dist.total_oi_sum, 20.)

    def test_oi_is_descriptive_without_synthetic_sample_or_normality_verdict(self):
        frame = pd.DataFrame({'strike': [198., 199., 200., 201., 202.],
                              'call_oi': [1., 8., 20., 8., 1.], 'put_oi': [1.] * 5})
        result = oi.compute_oi_stats(frame, 200., 200.)
        self.assertAlmostEqual(result.oi_weighted_mean, 200.)
        self.assertEqual(len(result.normal_fit_y), 5)
        self.assertFalse({'ks_statistic', 'ks_pvalue', 'is_normal'} & asdict(result).keys())
        doc = oi.build_exp_html(oi.ExpResult('15 Mai Mensal', '2026-05-15', 'Mensal', 21, result), 0)
        self.assertNotIn('Rejeita H₀', doc)
        self.assertNotIn('p-valor', doc)
        self.assertIn('descritivo', doc.lower())

    def test_pcr_put_only_is_undefined_and_labelled_at_every_level(self):
        stats = hunter.compute_expiry_stats({200.: {'call_vol': 0., 'put_vol': 300.}})
        self.assertIsNone(stats['pcr'])
        doc = hunter.build_html('2026-04-24', '24abr', 200., [('15 Mai Mensal', stats)])
        self.assertIn('Somente puts', doc)
        self.assertNotIn('Bullish (&lt; 0.8)', doc)
        self.assertNotIn('Bullish (< 0.8)', doc)

    def test_pcr_empty_is_na_and_positive_calls_keeps_ratio(self):
        empty = hunter.compute_expiry_stats({200.: {'call_vol': 0., 'put_vol': 0.}})
        self.assertIsNone(empty['pcr'])
        self.assertIn('N/A', hunter.build_html('2026-04-24', '', 200., [('Empty', empty)]))
        finite = hunter.compute_expiry_stats({200.: {'call_vol': 100., 'put_vol': 200.}})
        self.assertEqual(finite['pcr'], 2.)

    def test_tape_known_delta_cannot_certify_underlying_units(self):
        row = leg(.5)
        self.assertEqual(row['volume'], 50.)
        self.assertEqual(row['financial'], 120.)
        self.assertAlmostEqual(row['vwap'], 2.4)
        for key in ('delta_volume', 'delta_volume_buy', 'delta_volume_sell'):
            self.assertIsNone(row[key])

    def test_tape_missing_delta_remains_unknown_in_totals(self):
        summary = expiry()['summary']
        for key in ('call_delta_volume', 'put_delta_volume', 'total_delta_volume'):
            self.assertIsNone(summary[key])

    def test_history_masks_uncertified_legacy_delta_without_changing_raw_qty(self):
        old = expiry()
        old['summary'].update(call_delta_volume=1500., put_delta_volume=0., total_delta_volume=1500.)
        old['rows'][0]['call'].update(delta_volume=1500., delta_volume_buy=2000., delta_volume_sell=-500.)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'history.json')
            tape.save_options_history(path, groups(old))
            loaded = tape.load_options_history(path)
        result = loaded[0]['expiries'][0]
        self.assertIsNone(result['summary']['total_delta_volume'])
        self.assertIsNone(result['rows'][0]['call']['delta_volume'])
        self.assertEqual(result['rows'][0]['call']['volume'], 50.)
        self.assertEqual(result['quantity_unit'], 'uncertified')

    def test_tape_html_marks_units_and_displays_unknown_delta(self):
        doc = tape.build_html(groups(), [], '2026-04-24', '24abr', 200., 0.)
        self.assertIn('unidades não certificadas', doc)
        self.assertNotIn('contratos × 100 × delta', doc)
        self.assertNotIn('eq. ações', doc)
        self.assertNotIn('R$ por contrato', doc)
        self.assertRegex(doc, r'id="top-net-dv">N/A</div>')

    @unittest.skipUnless(shutil.which('node'), 'Node needed for generated dashboard JS')
    def test_js_date_totals_and_detail_keep_null_as_na(self):
        doc = tape.build_html(groups(), [], '2026-04-24', '24abr', 200., 0.)
        script = re.findall(r'<script>([\s\S]*?)</script>', doc)[0]
        script = script[:script.index("document.querySelectorAll('.date-tab')")]
        check = """
let totals = totalsFor([{summary:{call_delta_volume:5,put_delta_volume:null,total_delta_volume:null}},
                        {summary:{call_delta_volume:null,put_delta_volume:3,total_delta_volume:null}}]);
if (totals.call_delta_volume !== null || totals.put_delta_volume !== null || totals.total_delta_volume !== null)
  throw Error('Unknown delta became zero or partial aggregate');
if (fSigned(null) !== 'N/A') throw Error('Null display lost');
const elements = {};
global.document = {getElementById:id => elements[id] ||= {classList:{remove(){},add(){}}}};
global.Chart = function() { this.destroy = () => {}; };
render();
const cells = elements['detail-body'].innerHTML.match(/<td>[\\s\\S]*?<\\/td>/g);
if (!cells.slice(10,13).every(cell => cell.includes('N/A'))) throw Error('Missing row delta became zero');
"""
        result = subprocess.run(['node', '-e', script + check], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_premium_caller_labels_raw_price_quantity_without_currency(self):
        doc = premium.build_html(groups(), '2026-04-24', '24abr')
        self.assertIn('unidades não certificadas', doc)
        self.assertIn('Preço × qtd', doc)
        self.assertNotIn('fBRL(r.total_financial)', doc)


if __name__ == '__main__':
    unittest.main()
