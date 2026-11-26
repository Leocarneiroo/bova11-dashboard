import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts'))
import bova11_tex_dex_vex as mod


class EstimatedProfileTests(unittest.TestCase):
    def test_interpolation_is_bounded_and_separate_by_side(self):
        rows = [dict(strike=100, c_iv=20, p_iv=30, c_oi=10, p_oi=10),
                dict(strike=102, c_iv=None, p_iv=None, c_oi=10, p_oi=10),
                dict(strike=104, c_iv=24, p_iv=38, c_oi=10, p_oi=10),
                dict(strike=105, c_iv=None, p_iv=None, c_oi=10, p_oi=10),
                dict(strike=110, c_iv=30, p_iv=float('nan'), c_oi=10, p_oi=10)]
        result = mod.interpolate_profile_iv(rows)
        self.assertEqual((result[1]['c_iv'], result[1]['p_iv']), (22, 34))
        self.assertIsNone(result[3]['c_iv'])
        self.assertIsNone(result[3]['p_iv'])
        self.assertIsNone(rows[1]['c_iv'])

    def test_estimate_coverage_and_sensitivity_preserve_unknown(self):
        rows = [dict(strike=100, c_iv=20, p_iv=20, c_oi=10, p_oi=0),
                dict(strike=102, c_iv=None, p_iv=None, c_oi=10, p_oi=0),
                dict(strike=104, c_iv=24, p_iv=24, c_oi=10, p_oi=0),
                dict(strike=110, c_iv=None, p_iv=None, c_oi=10, p_oi=0)]
        result = mod.estimated_profile_bundle({'8 nov Mensal': rows}, '9out', 100, [95, 100, 105], [])
        entry = result['8 nov Mensal']
        self.assertEqual(entry['coverage']['observed'], .5)
        self.assertEqual(entry['coverage']['interpolated'], .25)
        self.assertEqual(entry['coverage']['missing'], .25)
        self.assertFalse(entry['complete'])
        self.assertIsNone(entry['flip'])
        self.assertNotEqual(entry['sensitivity']['low']['partial_gex_net'], entry['sensitivity']['high']['partial_gex_net'])
        rows[0]['c_oi'] = None
        unknown = mod.estimated_profile_bundle({'8 nov Mensal': rows}, '9out', 100, [100], [])['8 nov Mensal']
        self.assertIsNone(unknown['coverage']['observed'])
        self.assertFalse(unknown['complete'])

    def test_missing_expiry_blocks_aggregate_and_empty_is_not_zero(self):
        rows = [dict(strike=100, c_iv=20, p_iv=20, c_oi=10, p_oi=0)]
        result = mod.estimated_profile_bundle({'8 nov Mensal': rows, '15 nov Mensal': []}, '9out', 100, [100], ['22 nov W3'])
        self.assertFalse(result['15 nov Mensal']['complete'])
        self.assertFalse(result['AGG']['complete'])
        self.assertIsNone(result['AGG']['coverage']['observed'])

    def test_complete_interpolation_and_zero_oi_without_iv(self):
        rows = [dict(strike=100, c_iv=20, p_iv=None, c_oi=10, p_oi=0),
                dict(strike=102, c_iv=None, p_iv=None, c_oi=10, p_oi=0),
                dict(strike=104, c_iv=24, p_iv=None, c_oi=10, p_oi=0)]
        entry = mod.estimated_profile_bundle({'8 nov Mensal': rows}, '9out', 100, [95, 100, 105], [])['AGG']
        self.assertTrue(entry['complete'])
        self.assertAlmostEqual(entry['coverage']['observed'], 2 / 3)
        self.assertAlmostEqual(entry['coverage']['interpolated'], 1 / 3)
        self.assertEqual(entry['coverage']['missing'], 0)
        self.assertIsNotNone(entry['profile'][1]['gex_net'])


if __name__ == '__main__':
    unittest.main()
