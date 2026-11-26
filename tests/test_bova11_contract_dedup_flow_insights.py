from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
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


if __name__ == '__main__':
    unittest.main()
