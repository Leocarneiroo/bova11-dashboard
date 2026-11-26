#!/usr/bin/env python3
"""Repara apenas sessões com fonte local; preserva registros sem fonte."""
import argparse
from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

import bova11_flow_history as flow
import bova11_historical_data as market
from bova11_shared import tag_to_iso, normalize_tag


def atomic_save(path, payload):
    path = Path(path)
    content = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    assert json.loads(content) == payload
    if path.exists() and json.loads(path.read_text()) == payload:
        return False
    if path.exists():
        backup = path.parent / 'backups' / datetime.now().strftime('repair_%Y%m%d_%H%M%S_%f')
        backup.mkdir(parents=True)
        target = backup / path.name
        shutil.copy2(path, target)
        assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(target.read_bytes()).digest()
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
            temp_name = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        assert json.loads(Path(temp_name).read_text()) == payload
        os.replace(temp_name, path)
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)
    return True


def repair_histories(data_dir, history_dir):
    data_dir, history_dir = Path(data_dir), Path(history_dir)
    paths = {name: history_dir / f'bova11_{name}_history.json' for name in ('flow', 'market')}
    original = {name: json.loads(path.read_text()) if path.exists() else {} for name, path in paths.items()}
    repaired = deepcopy(original)
    report = {'recomputed': {'flow': [], 'market': []}, 'preserved': {'flow': [], 'market': []},
              'changed_files': [], 'tape': 'Sem workbooks originais: resultados legados não certificados; nenhuma recomputação.',
              'warnings': {}, 'preserved_reasons': {'flow': {}, 'market': {}}}
    sourced_flow = set()
    old_dir = flow.CSV_DIR
    flow.CSV_DIR = str(data_dir)
    try:
        tags = [tag for tag, _ in flow.discover_all_date_tags()]
        for i, tag in enumerate(tags):
            prior = tags[i - 1] if i else None
            label = flow.tag_to_label(tag)
            for expiry in flow.discover_expirations_for_tag(tag, prior):
                identity = f"{label} / {expiry['label']}"
                existing = original['flow'].get(label, {}).get(expiry['label'])
                if existing:
                    needs_previous = any(not row.get('snap', False) for row in existing.values())
                    missing_source = not expiry.get('vol_d') or (needs_previous and
                                      (not expiry.get('fech_d1') or not expiry.get('vol_d1')))
                    if missing_source:
                        report['preserved_reasons']['flow'][identity] = 'Fechamento/Volume D ou D-1 insuficiente para recompor todos os campos.'
                        continue
                rows = flow.parse_fech_csv(data_dir / expiry['fech_d'])
                if not rows:
                    continue
                if existing:
                    current_volume = flow.parse_vol_csv(data_dir / expiry['vol_d'])
                    previous_rows = flow.parse_fech_csv(data_dir / expiry['fech_d1']) if expiry.get('fech_d1') else []
                    previous_volume = flow.parse_vol_csv(data_dir / expiry['vol_d1']) if expiry.get('vol_d1') else {}
                    current_strikes = {r['strike'] for r in rows}
                    previous_strikes = {r['strike'] for r in previous_rows}
                    # Legacy integer keys may already have collided; validate source chains, not corrupted keys.
                    complete = current_strikes <= set(current_volume)
                    if needs_previous:
                        complete &= bool(previous_rows) and previous_strikes <= set(previous_volume)
                    if not complete:
                        report['preserved_reasons']['flow'][identity] = 'Fonte local vazia/incompleta: não permite recompor ΔOI e volumes observados.'
                        continue
                if any(row.get(side + '_oi') is None for row in rows for side in ('c', 'p')):
                    report['preserved_reasons']['flow'][identity] = 'OI desconhecido no fechamento atual.'
                    continue
                if existing and needs_previous and any(row.get(side + '_oi') is None for row in previous_rows for side in ('c', 'p')):
                    report['preserved_reasons']['flow'][identity] = 'OI desconhecido no fechamento anterior.'
                    continue
                result = flow.compute_strike_flows(expiry, tag, prior)
                assert len(result) == len(rows)
                for side, field in (('c', 'co2'), ('p', 'po2')):
                    assert sum(r[field] for r in result.values()) == sum(int(r[side + '_oi']) for r in rows)
                repaired['flow'].setdefault(label, {})[expiry['label']] = result
                identity = f"{label} / {expiry['label']}"
                sourced_flow.add(identity)
                report['recomputed']['flow'].append(identity)
    finally:
        flow.CSV_DIR = old_dir
    for date, expiries in original['flow'].items():
        for expiry in expiries:
            identity = f'{date} / {expiry}'
            if identity not in sourced_flow:
                report['preserved']['flow'].append(identity)
                report['preserved_reasons']['flow'].setdefault(identity, 'Sem fechamento local suficiente para reparar este registro.')

    # Uma observação por sessão; pós-mercado precede fechamento e pré-mercado.
    sessions = {}
    known_dates = {normalize_tag(record['tag']): date for date, record in original['market'].items() if record.get('tag')}
    for tag in market.discover_tags(str(data_dir)):
        date = known_dates.get(normalize_tag(tag))
        if date is None:
            # Mtime supplies the source year; latest history date must not move old sessions to another year.
            source_dates = [datetime.fromtimestamp(path.stat().st_mtime) for path in data_dir.glob('*.csv')
                            if market.extract_tag(path.name) == tag]
            year = max(source_dates).year if source_dates else datetime.now().year
            date = tag_to_iso(tag, year)
        if date is None:
            continue
        quality = 2 if 'pos' in tag.lower() else 0 if 'pre' in tag.lower() else 1
        if date not in sessions or quality > sessions[date][0]:
            sessions[date] = (quality, tag)
    for date, (_, tag) in sorted(sessions.items()):
        snapshot = market.build_snapshot(str(data_dir), tag, int(date[:4]), {},
                                         str(history_dir / 'bova11_spot_history.json'), original['market'])
        # Ausência da referência de spot não autoriza substituir uma sessão existente.
        if snapshot is None or snapshot.get('spot_close') is None:
            continue
        assert snapshot['date'] == date
        if snapshot.get('oi_total') is not None:
            assert snapshot['oi_total'] == snapshot['oi_call'] + snapshot['oi_put']
        if snapshot.get('vol_total') is not None:
            assert snapshot['vol_total'] == snapshot['vol_call'] + snapshot['vol_put']
        repaired['market'][date] = {**original['market'].get(date, {}), **snapshot}
        report['recomputed']['market'].append(date)
        if snapshot.get('warnings'):
            report['warnings'][date] = snapshot['warnings']
    computed = [repaired['market'][date] for date in report['recomputed']['market']]
    market.enrich_iv_metrics(computed)
    report['preserved']['market'] = sorted(set(original['market']) - set(report['recomputed']['market']))
    report['preserved_reasons']['market'] = {date: 'Sem CSV/spot suficientes para recompor esta sessão.'
                                            for date in report['preserved']['market']}
    assert set(original['flow']) <= set(repaired['flow'])
    assert set(original['market']) <= set(repaired['market'])
    for name, path in paths.items():
        if atomic_save(path, repaired[name]):
            report['changed_files'].append(path.name)
    return report


def main():
    base = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(base / 'data'))
    parser.add_argument('--history-dir', default=str(base / 'history'))
    parser.add_argument('--report', default=str(base / 'docs' / 'reparo_historicos_calculos.json'))
    args = parser.parse_args()
    report = repair_histories(args.data_dir, args.history_dir)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({key: {name: len(rows) for name, rows in report[key].items()}
                      for key in ('recomputed', 'preserved')}, ensure_ascii=False))
    print('Arquivos alterados:', ', '.join(report['changed_files']) or 'nenhum')


if __name__ == '__main__':
    main()
