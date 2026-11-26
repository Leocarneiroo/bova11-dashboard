import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


class HistoryRepairTests(unittest.TestCase):
    def test_missing_sources_preserved_and_second_run_identical(self):
        import bova11_repair_history as repair
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'data').mkdir(); (root / 'history').mkdir()
            values = {'bova11_flow_history.json': {'31/dez': {'legacy': {'79': {'co2': 10, 'po2': 20}}}},
                      'bova11_market_history.json': {'2025-12-31': {'date': '2025-12-31', 'spot_close': 100}},
                      'bova11_spot_history.json': {'2025-12-31': {'spot': 100}}}
            for name, value in values.items():
                (root / 'history' / name).write_text(json.dumps(value))
            before = {name: (root / 'history' / name).read_bytes() for name in values}
            first = repair.repair_histories(root / 'data', root / 'history')
            second = repair.repair_histories(root / 'data', root / 'history')
            self.assertEqual(first['preserved'], second['preserved'])
            for name, content in before.items():
                self.assertEqual((root / 'history' / name).read_bytes(), content)
            self.assertEqual(len(first['preserved']['flow']), 1)
            self.assertEqual(len(first['preserved']['market']), 1)

    def test_flow_preserves_changes_and_volume_without_previous_source(self):
        import bova11_repair_history as repair
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'data').mkdir(); (root / 'history').mkdir()
            record = {'8/out': {'23 out W4': {'203': {'co1': 10, 'co2': 20, 'dco': 10,
                      'po1': 20, 'po2': 40, 'dpo': 20, 'cv': 50, 'pv': 60, 'snap': False}}}}
            path = root / 'history' / 'bova11_flow_history.json'; path.write_text(json.dumps(record))
            (root / 'history' / 'bova11_market_history.json').write_text('{}')
            row = ['0'] * 23
            row[0], row[22], row[2], row[20], row[11] = 'BOVAJ203W4', 'BOVAV203W4', '20', '40', '203'
            (root / 'data' / 'venc 23 out W4 fechamento (8out).csv').write_text(';'.join(['Header'] * 23) + '\n' + ';'.join(row))
            report = repair.repair_histories(root / 'data', root / 'history')
            self.assertEqual(json.loads(path.read_text()), record)
            self.assertIn('8/out / 23 out W4', report['preserved']['flow'])

    def test_empty_previous_csv_cannot_reset_existing_flow(self):
        import bova11_repair_history as repair
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'data').mkdir(); (root / 'history').mkdir()
            record = {'8/out': {'23 out W4': {'203': {'co1': 10, 'co2': 20, 'dco': 10,
                      'po1': 20, 'po2': 40, 'dpo': 20, 'cv': 50, 'pv': 60, 'snap': False}}}}
            path = root / 'history' / 'bova11_flow_history.json'; path.write_text(json.dumps(record))
            (root / 'history' / 'bova11_market_history.json').write_text('{}')
            row = ['0'] * 23
            row[0], row[22], row[2], row[20], row[11] = 'BOVAJ203W4', 'BOVAV203W4', '20', '40', '203'
            (root / 'data' / 'venc 23 out W4 fechamento (8out).csv').write_text(';'.join(['Header'] * 23) + '\n' + ';'.join(row))
            for suffix in ('7out', '7out Volume', '8out Volume'):
                (root / 'data' / f'venc 23 out W4 fechamento ({suffix}).csv').write_text('Header\n')
            report = repair.repair_histories(root / 'data', root / 'history')
            self.assertEqual(json.loads(path.read_text()), record)
            self.assertIn('8/out / 23 out W4', report['preserved']['flow'])

    def test_historical_tag_uses_stored_year_and_repair_is_idempotent(self):
        import bova11_repair_history as repair
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'data').mkdir(); (root / 'history').mkdir()
            record = {'2026-03-03': {'date': '2026-03-03', 'tag': '3mar', 'spot_close': 203},
                      '2026-10-08': {'date': '2026-10-08', 'tag': '8out', 'spot_close': 203}}
            path = root / 'history' / 'bova11_market_history.json'; path.write_text(json.dumps(record))
            (root / 'history' / 'bova11_flow_history.json').write_text('{}')
            row = ['0'] * 23
            row[0], row[22], row[2], row[20], row[11], row[7], row[15] = 'BOVAD203', 'BOVAP203', '20', '40', '203', '20', '20'
            (root / 'data' / 'venc 17 abr Mensal fechamento (3mar).csv').write_text(';'.join(['Header'] * 23) + '\n' + ';'.join(row))
            report = repair.repair_histories(root / 'data', root / 'history')
            self.assertIn('2026-03-03', report['recomputed']['market'])
            first = path.read_bytes()
            self.assertEqual(set(json.loads(first)), set(record))
            repair.repair_histories(root / 'data', root / 'history')
            self.assertEqual(path.read_bytes(), first)

    def test_real_fractional_chain_is_repaired_with_complete_source_files(self):
        import bova11_repair_history as repair
        import shutil
        project = Path(__file__).resolve().parents[1]
        source = project / 'data' / 'venc 17 jul Mensal fechamento (25mai).csv'
        if not source.exists():
            self.skipTest('CSV real de controle não disponível')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); (root / 'data').mkdir(); (root / 'history').mkdir()
            for tag in ('21mai', '25mai'):
                for suffix in ('', ' Volume'):
                    name = f'venc 17 jul Mensal fechamento ({tag}{suffix}).csv'
                    shutil.copy2(project / 'data' / name, root / 'data' / name)
            original = json.loads((project / 'history' / 'bova11_flow_history.json').read_text())
            legacy = {str(int(float(strike))): row for strike, row in original['25/mai']['17 jul Mensal'].items()}
            self.assertLess(len(legacy), 165)
            (root / 'history' / 'bova11_flow_history.json').write_text(json.dumps({'25/mai': {'17 jul Mensal': legacy}}))
            (root / 'history' / 'bova11_market_history.json').write_text('{}')
            repair.repair_histories(root / 'data', root / 'history')
            rows = json.loads((root / 'history' / 'bova11_flow_history.json').read_text())['25/mai']['17 jul Mensal']
            self.assertEqual(len(rows), 165)
            self.assertEqual(sum(row['co2'] + row['po2'] for row in rows.values()), 50812939)

    def test_atomic_save_rejects_nan_and_keeps_original(self):
        import bova11_repair_history as repair
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'history.json'; path.write_text('{"old":1}')
            with self.assertRaises(ValueError):
                repair.atomic_save(path, {'invalid': float('nan')})
            self.assertEqual(path.read_text(), '{"old":1}')
            repair.atomic_save(path, {'new': 2})
            self.assertEqual(json.loads(path.read_text()), {'new': 2})
            self.assertTrue(any(path.parent.glob('backups/*/history.json')))


class HistoricalIVTests(unittest.TestCase):
    def test_constant_or_short_iv_history_is_unavailable(self):
        import bova11_historical_data as market
        for values in ([.2, .3], [.2] * 25):
            rows = [{'date': f'2026-09-{i + 1:02}', 'iv_atm': value} for i, value in enumerate(values)]
            market.enrich_iv_metrics(rows)
            self.assertIsNone(rows[-1]['iv_rank'])
            self.assertIsNone(rows[-1]['iv_percentile'])

    def test_iv_rank_window_and_percentile_exclude_current(self):
        import bova11_historical_data as market
        rows = [{'date': '2024-09-01', 'iv_atm': .9}]
        rows += [{'date': f'2026-09-{i + 1:02}', 'iv_atm': .1 + i * .005} for i in range(20)]
        rows += [{'date': '2026-09-21', 'iv_atm': .3}]
        market.enrich_iv_metrics(rows)
        self.assertEqual(rows[-1]['iv_rank'], 100)
        self.assertEqual(rows[-1]['iv_percentile'], 100)


if __name__ == '__main__':
    unittest.main()
