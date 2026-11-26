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


class StatisticsFixTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
