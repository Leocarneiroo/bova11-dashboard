from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bova11_flow_history as flow
import bova11_insights_auto as insights


def close_row(ticker, call_oi, put_oi, gamma='0,01'):
    row = ['0'] * 23
    row[0], row[22] = f'BOVAJ{ticker}', f'BOVAV{ticker}'
    row[1] = row[21] = '2'
    row[2], row[20], row[11] = str(call_oi), str(put_oi), '190'
    row[3], row[19] = '0,5', '-0,5'
    row[4] = row[18] = gamma
    row[5] = row[17] = '-0,1'
    row[6] = row[16] = '0,2'
    row[7] = row[15] = '20'
    return ';'.join(row)


def write_chain(folder, tag, rows):
    path = Path(folder) / f'venc 16 out Mensal fechamento ({tag}).csv'
    path.write_text(';'.join(['header'] * 23) + '\n' + '\n'.join(rows) + '\n', encoding='latin-1')
    return path


class ContractDedupFlowInsightsTests(unittest.TestCase):
    def test_parsers_deduplicate_contracts_and_keep_distinct_series(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = close_row(190, 10, 30), close_row(191, 20, 40, '0,03')
            path = write_chain(folder, '8out', [a, b, a])
            rows = insights.parse_fech_csv(str(path))
            self.assertEqual(len(rows), 2)
            self.assertEqual(sum(row['c_oi'] for row in rows), 30)
            self.assertEqual({row['c_ticker'] for row in rows}, {'BOVAJ190','BOVAJ191'})
            flow_rows = flow.parse_fech_csv(str(path))
            self.assertEqual(len(flow_rows), 1)
            self.assertEqual(flow_rows[0]['c_oi'], 30)

    def test_flow_consolidates_both_sessions_once_per_strike(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = close_row(190, 10, 30), close_row(191, 20, 40)
            current = write_chain(folder, '8out', [a, b, a])
            prior = write_chain(folder, '7out', [close_row(190, 5, 25), close_row(191, 7, 35)])
            info = dict(fech_d=current.name, fech_d1=prior.name, vol_d=None, vol_d1=None)
            with patch.object(flow, 'CSV_DIR', folder):
                result = flow.compute_strike_flows(info, '8out', '7out')
        self.assertEqual(set(result), {'190'})
        self.assertEqual((result['190']['co1'], result['190']['co2'], result['190']['dco']), (12, 30, 18))
        self.assertEqual((result['190']['po1'], result['190']['po2'], result['190']['dpo']), (60, 70, 10))

    def test_rankings_sum_oi_keep_raw_exposure_and_count_volume_once(self):
        with tempfile.TemporaryDirectory() as folder:
            a, b = close_row(190, 10, 30), close_row(191, 20, 40, '0,03')
            rows = insights.parse_fech_csv(str(write_chain(folder, '8out', [a,b,a])))
            prior = insights.parse_fech_csv(str(write_chain(folder, '7out', [close_row(190,5,25),close_row(191,7,35)])))
        result = insights.build_exp_data({'name':'16 out Mensal','color':'red'}, rows, prior,
            {190:{'cv':11,'pv':13}}, {190:{'cv':2,'pv':3}}, 190)
        self.assertEqual(len(result['strikes']), 1)
        self.assertEqual((result['coi'],result['coi1'],result['dcoi']), (30,12,18))
        self.assertEqual((result['cvol'],result['pvol']), (11,13))
        self.assertAlmostEqual(result['strikes'][0]['c_gamma2'], .7/30)
        gamma = insights.calc_gex(190, rows)[190]
        self.assertAlmostEqual(gamma['c'], .7 * 190**2 * .01)

    def test_unknown_oi_survives_consolidation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = write_chain(folder, '8out', [close_row(190,10,30),close_row(191,'-',40)])
            info = dict(fech_d=path.name,fech_d1=None,vol_d=None,vol_d1=None)
            with patch.object(flow, 'CSV_DIR', folder):
                result = flow.compute_strike_flows(info, '8out', None)
            ranking = insights.build_exp_data({'name':'16 out Mensal','color':'red'}, insights.parse_fech_csv(str(path)), [], {}, {}, 190)
        self.assertIsNone(result['190']['co2'])
        self.assertEqual(result['190']['po2'], 70)
        self.assertIsNone(ranking['coi'])
        self.assertIsNone(ranking['strikes'][0]['c_gamma2'])

    def test_volume_deduplicates_identity_and_sums_distinct_series(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'venc 16 out Mensal fechamento (8out Volume).csv'
            rows = []
            for ticker, call, put in ((190,11,13),(191,17,19),(190,11,13)):
                row = ['0'] * 11
                row[0],row[10] = f'BOVAJ{ticker}',f'BOVAV{ticker}'
                row[1],row[9],row[5] = str(call),str(put),'190'
                rows.append(';'.join(row))
            path.write_text(';'.join(['header']*11)+'\n'+'\n'.join(rows)+'\n',encoding='latin-1')
            for parser in (flow.parse_vol_csv, insights.parse_vol_csv):
                self.assertEqual(parser(str(path))[190], {'cv':28.0,'pv':32.0,'coi':0.0,'poi':0.0})


if __name__ == '__main__':
    unittest.main()
