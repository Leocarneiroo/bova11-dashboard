import math
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'scripts'))
import bova11_shared as shared
import bova11_tex_dex_vex as profiles
import bova11_auto as dashboard


class GammaCoreTests(unittest.TestCase):
    def test_theta_matches_calendar_derivative_of_price(self):
        spot, strike, sigma, tau, rate, carry = 100, 105, .25, 30 / 365, .10, .03
        def price(t, side):
            d1 = (math.log(spot / strike) + (rate - carry + sigma**2 / 2) * t) / (sigma * math.sqrt(t))
            d2 = d1 - sigma * math.sqrt(t)
            if side == 'call':
                return spot * math.exp(-carry*t) * shared.norm_cdf(d1) - strike * math.exp(-rate*t) * shared.norm_cdf(d2)
            return strike * math.exp(-rate*t) * shared.norm_cdf(-d2) - spot * math.exp(-carry*t) * shared.norm_cdf(-d1)
        for side in ('call', 'put'):
            with self.subTest(side=side):
                h = 1e-6
                expected = -(price(tau+h, side)-price(tau-h, side)) / (2*h*365)
                self.assertAlmostEqual(shared.bs_theta_per_day(spot, strike, sigma, tau, side, rate, carry), expected, places=8)

    def test_charm_decomposition_matches_aging_without_spot_or_iv_changes(self):
        now = shared.bs_delta(100, 105, .25, 30/365, 'call')
        prev = shared.bs_delta(100, 105, .25, 31/365, 'call')
        result = shared.calc_convexity_decomposition(strike=105, option_type='call', spot_d=100, spot_d1=100,
            delta_d=now, delta_d1=prev, iv_d=25, iv_d1=25, expiry_label='8 nov Mensal',
            session_date_d='2026-10-09', session_date_d1='2026-10-08')
        self.assertAlmostEqual(result['charm_contrib'], now-prev, delta=1e-6)
        self.assertLess(abs(result['residual']), 1e-6)

    def test_profile_roots_require_a_real_sign_change(self):
        self.assertEqual(shared.find_profile_roots([{'spot': s, 'gex_net': n} for s,n in [(90,1),(100,0),(110,1)]]), [])
        self.assertEqual(shared.find_profile_roots([{'spot': s, 'gex_net': n} for s,n in [(90,-1),(100,0),(110,1),(120,-1)]]), [100,115])
        self.assertEqual(shared.find_profile_roots([{'spot':90,'gex_net':-1},{'spot':100,'gex_net':None},{'spot':110,'gex_net':1}]), [])

    def test_missing_iv_does_not_become_zero_gex(self):
        result = profiles.build_unified_spot_profile([{'strike':100,'c_oi':1000,'p_oi':800,'c_iv':None,'p_iv':None}],
            '8 nov Mensal', '9out', [100], spot_ref=100)[0]
        self.assertIsNone(result['gex_net'])
        self.assertEqual(result['iv_coverage'], 0)

    def test_partial_profile_cannot_produce_a_model_regime(self):
        result = profiles.gamma_profile_summary([{'strike':100,'c_oi':1000,'p_oi':800,'c_iv':25,'p_iv':None}],
            '8 nov Mensal', '9out', 100)
        self.assertIsNone(result['flip'])
        self.assertEqual(result['regime'], 'Indisponível')
        self.assertAlmostEqual(result['iv_coverage'], 1000/1800)

    def test_gamma_summary_matches_full_profile_without_calculating_other_greeks(self):
        rows = [{'strike':100,'c_oi':1000,'p_oi':800,'c_iv':25,'p_iv':27}]
        grid = [95,100,105]
        full = profiles.build_unified_spot_profile(rows,'8 nov Mensal','9out',grid,spot_ref=100)
        with patch.object(profiles, 'bs_delta', side_effect=AssertionError('Unused Greek calculated')):
            summary = profiles.gamma_profile_summary(rows,'8 nov Mensal','9out',100,spot_range=grid)
        for expected, actual in zip(full, summary['profile']):
            self.assertEqual(expected['gex_net'], actual['gex_net'])
            self.assertEqual(expected['iv_coverage'], actual['iv_coverage'])

    def test_aggregate_does_not_discard_unknown_expiry(self):
        known = profiles.build_unified_spot_profile([{'strike':100,'c_oi':1000,'p_oi':0,'c_iv':25}], '8 nov Mensal','9out',[100])
        unknown = profiles.build_unified_spot_profile([{'strike':100,'c_oi':1000,'p_oi':0}], '8 nov Mensal','9out',[100])
        aggregate = profiles.aggregate_spot_profiles([known,unknown])[0]
        self.assertIsNone(aggregate['gex_net'])
        self.assertAlmostEqual(aggregate['iv_coverage'], .5)

    def test_aggregate_cannot_treat_header_only_chain_as_known_zero(self):
        known = profiles.gamma_profile_summary([{'strike':100,'c_oi':1000,'p_oi':0,'c_iv':25}], '8 nov Mensal','9out',100)
        empty = profiles.gamma_profile_summary([], '15 nov Mensal','9out',100,spot_range=[r['spot'] for r in known['profile']])
        aggregate = profiles.aggregate_spot_profiles([known['profile'],empty['profile']])
        self.assertTrue(all(row['gex_net'] is None for row in aggregate))

    def test_dashboard_model_ignores_expired_but_blocks_missing_active_expiry(self):
        profile = [{'spot':90,'gex_net':-1,'iv_coverage':1,'oi_total':100,'oi_with_iv':100},
                   {'spot':110,'gex_net':1,'iv_coverage':1,'oi_total':100,'oi_with_iv':100}]
        models = {'active': {'regime':'Long Gamma','profile':profile},
                  'expired': {'regime':'Expirado','profile':[{'spot':90,'gex_net':None}]}}
        summary = dashboard.aggregate_model_summary(models, 110)
        self.assertEqual(summary['flip'],100)
        self.assertEqual(summary['regime'],'Long Gamma')
        summary = dashboard.aggregate_model_summary(models, 110, ['16 out Mensal'])
        self.assertIsNone(summary['flip'])
        self.assertEqual(summary['regime'],'Indisponível')

    def test_profile_cex_is_calendar_delta_change_per_day(self):
        rows = [{'strike':105,'c_oi':1000,'p_oi':0,'c_iv':25}]
        result = profiles.build_unified_spot_profile(rows,'8 nov Mensal','9out',[100],rate=0)[0]
        tau=30/365
        h=1e-6
        expected=-(shared.bs_delta(100,105,.25,tau+h,'call')-shared.bs_delta(100,105,.25,tau-h,'call'))/(2*h*365)*1000
        self.assertAlmostEqual(result['cex_call'],expected,places=7)

    def test_expired_contract_is_not_given_an_extra_day(self):
        self.assertEqual(profiles._resolve_session_and_expiry_t('8 out Mensal','9out'), 0)
        result=profiles.gamma_profile_summary([{'strike':100,'c_oi':1000,'c_iv':25,'p_oi':0}], '8 out Mensal','9out',100)
        self.assertEqual(result['regime'],'Expirado')

    def test_dashboard_sums_contract_exposures_at_same_strike(self):
        result=dashboard.calc_gex(100,[{'strike':100,'c_gamma':.01,'c_oi':1000,'p_gamma':0,'p_oi':0},
            {'strike':100,'c_gamma':.1,'c_oi':1,'p_gamma':0,'p_oi':0}])
        self.assertAlmostEqual(result[100]['n'],1010)

    def test_dashboard_same_strike_compares_contracts_and_counts_volume_once(self):
        def row(ticker, oi):
            return {'strike':100,'c_ticker':ticker,'p_ticker':ticker+'P','c_oi':oi,'p_oi':0,
                **{side+'_'+key: 1 for side in ('c','p') for key in ('last','iv','delta','gamma','theta','vega')}}
        result=dashboard.build_exp_data({'name':'expiry','color':'red'},[row('A',110),row('B',210)],
            [row('A',100),row('B',200)],{100:{'cv':30,'pv':0}}, {}, 100)
        self.assertEqual(result['coi1'],300)
        self.assertEqual(result['dcoi'],20)
        self.assertEqual(result['cvol'],30)
        self.assertEqual(len(result['strikes']),1)

    def test_zero_exposure_and_missing_values_have_distinct_formatting(self):
        self.assertEqual(profiles.fmt_num(None),'N/A')
        self.assertEqual(profiles.fmt_num(0),'0')

    def test_missing_greek_on_zero_position_does_not_poison_known_exposure(self):
        rows=[{'strike':100,'c_oi':0,'p_oi':0,'c_gamma':None,'p_gamma':None}]
        self.assertFalse(dashboard.calc_gex(100,rows)[100]['partial'])
        self.assertEqual(profiles.gamma_profile_summary(rows,'8 nov Mensal','9out',100)['regime'],'Neutro')

    def test_html_preserves_null_profile_and_unavailable_cards(self):
        profile=profiles.build_unified_spot_profile([{'strike':100,'c_oi':1000,'p_oi':0}], '8 nov Mensal','9out',[100])
        html=profiles.generate_html(profile,{'avg_iv_d':None,'avg_iv_d1':None,'iv_direction':None},
            '8out','9out','08/10','09/10',99,100,{'8 nov Mensal':profile},['8 nov Mensal'])
        self.assertIn('"gex_net": null',html)
        self.assertIn('N/A',html)
        self.assertIn('iv_coverage',html)


if __name__ == '__main__':
    unittest.main()
