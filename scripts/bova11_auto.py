#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 Options Dashboard PRO — Versão Automatizada
===================================================
Auto-descobre as datas a partir dos arquivos CSV no diretório.
Solicita apenas o spot price via input no terminal.

Uso:
  python3 bova11_auto.py
  
O script irá:
  1. Detectar automaticamente os arquivos CSV no diretório
  2. Identificar as datas (D-1 e D) baseado na data de modificação
  3. Solicitar o spot price do dia
  4. Gerar os HTMLs de análise
"""

import os, json, math, re, glob, sys
from datetime import datetime
from bova11_expiry_family import matches_expiry_label
from bova11_shared import calc_gex_components, find_profile_roots, parse_br_number, expiry_label_to_iso_nearest, parse_b3_close_csv
from bova11_tex_dex_vex import gamma_profile_summary, aggregate_spot_profiles, build_spot_grid, _resolve_session_and_expiry_t

# ═══════════════════════════════════════
# CONFIG — Apenas ajustes finos
# ═══════════════════════════════════════
_BASEDIR       = os.path.dirname(os.path.abspath(__file__))
CSV_DIR        = os.path.join(_BASEDIR, '..', 'data')
ANO            = str(datetime.now().year)
GEX_STRIKE_MIN = 160
GEX_STRIKE_MAX = 200
OUTPUT_DIR     = os.path.join(_BASEDIR, '..', 'output')

# ═══════════════════════════════════════
# AUTO-DISCOVERY DE DATAS
# ═══════════════════════════════════════
def extract_date_from_filename(filename):
    """Extrai a tag de data do nome do arquivo."""
    # Padrão: venc_<label>_fechamento__<tag>_.csv
    m = re.search(r'fechamento__([a-zA-Z0-9]+)_\.csv$', filename)
    if m:
        return m.group(1)
    # Padrão: venc <label> fechamento (<tag>).csv
    m = re.search(r'fechamento \(([a-zA-Z0-9]+)\)\.csv$', filename)
    if m:
        return m.group(1)
    return None

def parse_date_tag(tag):
    """Converte tag como '12fev' ou '13fev' para data."""
    meses = {
        'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12
    }
    m = re.match(r'(\d{1,2})([a-z]{3})$', tag.lower())
    if m:
        dia = int(m.group(1))
        mes = meses.get(m.group(2), 1)
        return datetime(int(ANO), mes, dia)
    return None

def format_date_label(tag):
    """Formata tag como '12fev' para label '12/Fev'."""
    meses = {
        'jan': 'Jan', 'fev': 'Fev', 'mar': 'Mar', 'abr': 'Abr', 'mai': 'Mai', 'jun': 'Jun',
        'jul': 'Jul', 'ago': 'Ago', 'set': 'Set', 'out': 'Out', 'nov': 'Nov', 'dez': 'Dez'
    }
    m = re.match(r'(\d{1,2})([a-z]{3})$', tag.lower())
    if m:
        dia = m.group(1)
        mes = meses.get(m.group(2), m.group(2).capitalize())
        return f"{dia}/{mes}"
    return tag

def tag_sort_key(tag):
    meses = {
        'jan': 1, 'fev': 2, 'mar': 3, 'abr': 4, 'mai': 5, 'jun': 6,
        'jul': 7, 'ago': 8, 'set': 9, 'out': 10, 'nov': 11, 'dez': 12
    }
    normalized = re.sub(r'(pos|pre)([a-z]{3})$', r'\2', tag.lower())
    m = re.match(r'(\d{1,2})([a-z]{3})$', normalized)
    if not m:
        return (99, 99, tag.lower())
    return (meses.get(m.group(2), 99), int(m.group(1)), normalized)

def is_primary_fechamento_file(filename):
    lower = filename.lower()
    return (
        lower.endswith('.csv')
        and 'fechamento' in lower
        and 'volume' not in lower
        and ' copy' not in lower
    )

def discover_dates():
    """Descobre as datas D-1 e D a partir dos arquivos no diretório."""
    # Encontra todos os arquivos de fechamento
    fechamento_files = []
    
    # Padrão underscore
    for fpath in glob.glob(os.path.join(CSV_DIR, "venc_*_fechamento__*_.csv")):
        filename = os.path.basename(fpath)
        if not is_primary_fechamento_file(filename):
            continue
        tag = extract_date_from_filename(filename)
        if tag:
            mtime = os.path.getmtime(fpath)
            fechamento_files.append((tag, mtime, fpath))
    
    # Padrão espaço
    for fpath in glob.glob(os.path.join(CSV_DIR, "venc * fechamento (*).csv")):
        filename = os.path.basename(fpath)
        if not is_primary_fechamento_file(filename):
            continue
        tag = extract_date_from_filename(filename)
        if tag:
            mtime = os.path.getmtime(fpath)
            fechamento_files.append((tag, mtime, fpath))
    
    if not fechamento_files:
        print("❌ Nenhum arquivo de fechamento encontrado no diretório.")
        print("   Esperado: venc_*_fechamento__<tag>_.csv ou 'venc * fechamento (<tag>).csv'")
        sys.exit(1)
    
    tags_meta = {}
    for tag, mtime, _fpath in fechamento_files:
        meta = tags_meta.setdefault(tag, {"count": 0, "mtime": 0})
        meta["count"] += 1
        meta["mtime"] = max(meta["mtime"], mtime)

    tags = [tag for tag, meta in tags_meta.items() if meta["count"] >= 2]
    if not tags:
        tags = list(tags_meta.keys())
    
    if len(tags) < 1:
        print("❌ Não foi possível identificar datas nos arquivos.")
        sys.exit(1)
    
    # Se só tem uma tag, usa ela como D (snapshot mode)
    if len(tags) == 1:
        print(f"⚠️  Apenas uma data encontrada: {tags[0]}")
        print("   Será usado modo snapshot (apenas D, sem comparação D-1)")
        return tags[0], None
    
    # Ordena por data de modificação (mais antigo = D-1, mais novo = D)
    tags_sorted = sorted(tags, key=tag_sort_key)
    
    # A maior tag cronológica é D; a anterior é D-1
    tag_d = tags_sorted[-1]
    tag_d1 = tags_sorted[-2] if len(tags_sorted) >= 2 else None
    
    return tag_d, tag_d1

def ask_spot():
    """Solicita o spot price ao usuário."""
    while True:
        spot_input = input("  Digite o spot price do BOVA11 (ex: 182.98): ").strip().replace(',', '.')
        try:
            spot = float(spot_input)
            if spot > 0:
                return spot
            else:
                print("  ❌ O spot deve ser maior que zero.")
        except ValueError:
            print("  ❌ Valor inválido. Use formato: 182.98")

# ═══════════════════════════════════════
# PARSERS (idênticos ao original)
# ═══════════════════════════════════════
def parse_num(s):
    """Converte string BR (1.234,56 / 12,34k / 1,2M / 26,74%) → float."""
    s = str(s).strip().replace('%', '')
    if s in ('-', '', '0', 'None', '--'):
        return 0.0
    mult = 1
    if s.endswith('M'):
        s = s[:-1]; mult = 1_000_000
    elif s.endswith('k'):
        s = s[:-1]; mult = 1_000
    s = s.replace('.', '').replace(',', '.')
    try:
        return float(s) * mult
    except ValueError:
        return 0.0


def parse_fech_csv(filepath):
    import os, re
    if not filepath or not os.path.exists(filepath):
        return []
        
    filename = os.path.basename(filepath)
    label = ""
    m = re.match(r'venc_(.+?)_fechamento__', filename)
    if m: label = m.group(1).replace('_', ' ')
    else:
        m = re.match(r'venc (.+?) fechamento', filename)
        if m: label = m.group(1)

    results = []
    seen_contracts = set()
    for parsed in parse_b3_close_csv(filepath):
        if not matches_expiry_label(parsed['call_ticker'], parsed['put_ticker'], label):
            continue
        strike = parsed['strike']
        identity = (strike, parsed['call_ticker'], parsed['put_ticker'])
        if identity in seen_contracts:
            continue
        seen_contracts.add(identity)
        results.append({
            'strike': strike, 'c_ticker': parsed['call_ticker'], 'p_ticker': parsed['put_ticker'],
            **{f'c_{key}': parsed[f'call_{key}'] for key in ('last','oi','delta','gamma','theta','vega','iv','bid','ask')},
            **{f'p_{key}': parsed[f'put_{key}'] for key in ('last','oi','delta','gamma','theta','vega','iv','bid','ask')},
        })
    return results

def parse_vol_csv(filepath):
    """Parse volume CSV. Col 1=VolCall, Col 5=Strike, Col 9=VolPut."""
    if not filepath or not os.path.exists(filepath):
        return {}
    with open(filepath, 'r', encoding='latin-1') as f:
        lines = f.readlines()
    header = lines[0].strip().replace('\r', '').split(';')
    filename = os.path.basename(filepath)
    match = re.match(r'venc_(.+?)_fechamento__', filename) or re.match(r'venc (.+?) fechamento', filename)
    label = match.group(1).replace('_', ' ') if match else ""
    strike_col = 5
    for i, h in enumerate(header):
        if 'strike' in h.lower():
            strike_col = i; break
    results = {}
    for line in lines[1:]:
        line = line.strip().replace('\r', '')
        if not line:
            continue
        p = line.split(';')
        if len(p) < 10:
            continue
        if not matches_expiry_label(p[0], p[10] if len(p) > 10 else "", label):
            continue
        strike = parse_num(p[strike_col])
        values = {
            'cv': parse_br_number(p[1], none_on_blank=True),
            'coi': parse_br_number(p[2], none_on_blank=True),
            'poi': parse_br_number(p[8], none_on_blank=True),
            'pv': parse_br_number(p[9], none_on_blank=True),
        }
        prior = results.setdefault(strike, {key: 0.0 for key in values})
        for key, value in values.items():
            prior[key] = prior[key] + value if prior[key] is not None and value is not None else None
    return results

# ═══════════════════════════════════════
# HELPERS (idênticos ao original)
# ═══════════════════════════════════════
def infer_spot(fech_rows):
    best_diff, best_strike = None, None
    for row in fech_rows:
        c = row.get('c_last', 0) or 0
        p = row.get('p_last', 0) or 0
        if c <= 0 or p <= 0:
            continue
        diff = abs(c - p)
        if best_diff is None or diff < best_diff:
            best_diff = diff; best_strike = row['strike']
    return float(best_strike) if best_strike is not None else None

def calc_max_pain(strikes_data):
    if not strikes_data or any(s.get(k) is None for s in strikes_data for k in ('co2', 'po2')):
        return None
    best_s, best_pain = 0, float('inf')
    for test in strikes_data:
        pain = sum(s['co2'] * max(test['s'] - s['s'], 0) + s['po2'] * max(s['s'] - test['s'], 0)
                   for s in strikes_data)
        if pain < best_pain:
            best_pain = pain; best_s = test['s']
    return best_s

def calc_gex(spot, fech_rows):
    r = {}
    for row in fech_rows:
        sk = row['strike']
        contributions = {}
        for side in ('c', 'p'):
            oi, gamma = row.get(f'{side}_oi'), row.get(f'{side}_gamma')
            contributions[side] = (0.0 if oi == 0 else
                (gamma * oi * spot ** 2 / 100 * (-1 if side == 'p' else 1)
                 if oi is not None and gamma is not None else None))
        record = r.setdefault(sk, {'c': 0.0, 'p': 0.0, 'oi_total': 0.0,
                                   'oi_with_gamma': 0.0, 'oi_complete': True})
        for side in ('c', 'p'):
            record[side] = nullable_sum([record[side], contributions[side]])
            oi, gamma = row.get(f'{side}_oi'), row.get(f'{side}_gamma')
            if oi is None:
                record['oi_complete'] = False
            else:
                record['oi_total'] += oi
                if gamma is not None:
                    record['oi_with_gamma'] += oi
    for record in r.values():
        record['n'] = nullable_sum([record['c'], record['p']])
        if not record['oi_complete']:
            record['gamma_coverage'] = 0.0
        elif record['oi_total']:
            record['gamma_coverage'] = record['oi_with_gamma'] / record['oi_total']
        else:
            record['gamma_coverage'] = 1.0
        record['partial'] = not record['oi_complete'] or record['gamma_coverage'] < 1.0
    return r

def aggregate_model_summary(models, spot, missing_expiries=()):
    active = [model['profile'] for model in models.values() if model['regime'] != 'Expirado']
    profile = aggregate_spot_profiles(active)
    current = min(profile, key=lambda row: abs(row['spot']-spot)) if profile else {}
    complete = current.get('iv_coverage', 0) >= 1 and not missing_expiries
    roots = find_profile_roots(profile) if complete else []
    value = current.get('gex_net') if complete else None
    regime = ('Long Gamma' if value > 0 else 'Short Gamma' if value < 0 else 'Neutro') if value is not None else 'Indisponível'
    return {'profile': profile, 'flips': roots, 'flip': min(roots, key=lambda x: abs(x-spot)) if roots else None,
            'regime': regime, 'iv_coverage': current.get('iv_coverage', 0)}

def gf(n):
    if n is None: return 'N/A'
    return f"+{n/1e6:.1f}M" if n >= 0 else f"−{abs(n)/1e6:.1f}M"

def safe_diff(v2, v1):
    if v2 is None or v1 is None: return None
    return round(v2 - v1, 6)

def safe_pct(v2, v1):
    if v1 is None or v2 is None: return None
    if v1 == 0: return 0.0 if v2 == 0 else 100.0
    return round(((v2 - v1) / abs(v1)) * 100, 2)

def nullable_sum(values):
    values = list(values)
    return sum(values) if all(value is not None for value in values) else None

def safe_ratio(numerator, denominator):
    if numerator is None or denominator is None:
        return None
    return round(numerator / max(denominator, 1), 2)

# ═══════════════════════════════════════
# DISCOVERY DE VENCIMENTOS
# ═══════════════════════════════════════
def discover_expirations(TAG_D, TAG_D1):
    exps = []
    # Padrão underscores: venc_<label>_fechamento__<tag>_.csv
    for fpath in sorted(glob.glob(os.path.join(CSV_DIR, f"venc_*_fechamento__{TAG_D}_.csv"))):
        fn = os.path.basename(fpath)
        m = re.match(r'venc_(.+?)_fechamento__' + re.escape(TAG_D) + r'_\.csv$', fn)
        if not m: continue
        label = m.group(1).strip()
        exps.append({
            "label": label.replace('_', ' '),
            "fech_d": fn,
            "vol_d":   f"venc_{label}_fechamento__{TAG_D}_Volume_.csv",
            "fech_d1": f"venc_{label}_fechamento__{TAG_D1}_.csv" if TAG_D1 else None,
            "vol_d1":  f"venc_{label}_fechamento__{TAG_D1}_Volume_.csv" if TAG_D1 else None,
        })
    # Padrão espaços: venc <label> fechamento (<tag>).csv
    for fpath in sorted(glob.glob(os.path.join(CSV_DIR, f"venc * fechamento ({TAG_D}).csv"))):
        fn = os.path.basename(fpath)
        m = re.match(r'venc (.+) fechamento \(' + re.escape(TAG_D) + r'\)\.csv$', fn)
        if not m: continue
        label = m.group(1).strip()
        if any(e['label'] == label for e in exps): continue
        exps.append({
            "label": label,
            "fech_d": fn,
            "vol_d":   f"venc {label} fechamento ({TAG_D} Volume).csv",
            "fech_d1": f"venc {label} fechamento ({TAG_D1}).csv" if TAG_D1 else None,
            "vol_d1":  f"venc {label} fechamento ({TAG_D1} Volume).csv" if TAG_D1 else None,
        })
    # Verificar existência dos opcionais
    for e in exps:
        for k in ('vol_d', 'fech_d1', 'vol_d1'):
            if e[k] and not os.path.exists(os.path.join(CSV_DIR, e[k])):
                e[k] = None
    return exps

# ═══════════════════════════════════════
# BUILD DATA POR VENCIMENTO
# ═══════════════════════════════════════
def consolidate_dashboard_strikes(rows):
    groups = {}
    for row in rows:
        groups.setdefault(row['strike'], []).append(row)
    output = []
    for strike, contracts in sorted(groups.items()):
        result = {'strike': strike}
        for side in ('c', 'p'):
            result[f'{side}_oi'] = nullable_sum(row.get(f'{side}_oi') for row in contracts)
            for field in ('last', 'iv', 'delta', 'gamma', 'theta', 'vega'):
                key = f'{side}_{field}'
                active = [row for row in contracts if row.get(f'{side}_oi') is not None and row[f'{side}_oi'] > 0]
                if not active:
                    result[key] = contracts[0].get(key) if result[f'{side}_oi'] == 0 else None
                elif result[f'{side}_oi'] is None or any(row.get(key) is None for row in active):
                    result[key] = None
                else:
                    result[key] = sum(row[key] * row[f'{side}_oi'] for row in active) / result[f'{side}_oi']
        output.append(result)
    return output


def build_exp_data(vinfo, rows_d, rows_d1, volmap_d, volmap_d1, spot):
    identity = lambda row: (row['strike'], row.get('c_ticker', ''), row.get('p_ticker', ''))
    contracts_d1 = {identity(row): row for row in rows_d1}
    snapshots = {row['strike'] for row in rows_d if identity(row) not in contracts_d1}
    rows_d = consolidate_dashboard_strikes(rows_d)
    d1_map = {row['strike']: row for row in consolidate_dashboard_strikes(list(contracts_d1.values()))}
    strikes_out = []
    tc_oi = tp_oi = tc_vol = tp_vol = 0
    tc_oi1 = tp_oi1 = tc_vol1 = tp_vol1 = 0

    for r in rows_d:
        s = r['strike']
        r1 = d1_map.get(s)
        v_d = volmap_d.get(s, {})
        v_1 = (volmap_d1 or {}).get(s, {})
        snap = s in snapshots
        r1 = r1 or {}
        cp_pct = safe_pct(r.get('c_last'), r1.get('c_last'))
        pp_pct = safe_pct(r.get('p_last'), r1.get('p_last'))
        co2, po2 = r.get('c_oi'), r.get('p_oi')
        co1 = r1.get('c_oi') if r1.get('c_oi') is not None else v_1.get('coi')
        po1 = r1.get('p_oi') if r1.get('p_oi') is not None else v_1.get('poi')
        cv, pv, cv1, pv1 = (v_d.get('cv'), v_d.get('pv'), v_1.get('cv'), v_1.get('pv'))

        tc_oi = nullable_sum([tc_oi, co2]); tp_oi = nullable_sum([tp_oi, po2])
        tc_oi1 = nullable_sum([tc_oi1, co1]); tp_oi1 = nullable_sum([tp_oi1, po1])
        tc_vol = nullable_sum([tc_vol, cv]); tp_vol = nullable_sum([tp_vol, pv])
        tc_vol1 = nullable_sum([tc_vol1, cv1]); tp_vol1 = nullable_sum([tp_vol1, pv1])

        strikes_out.append({
            's': s, 'snap': snap,
            'c_last1': r1.get('c_last'), 'c_last2': r.get('c_last'), 'cp_pct': cp_pct,
            'p_last1': r1.get('p_last'), 'p_last2': r.get('p_last'), 'pp_pct': pp_pct,
            'co1': co1, 'co2': co2, 'dco': safe_diff(co2, co1),
            'po1': po1, 'po2': po2, 'dpo': safe_diff(po2, po1),
            'c_iv1': r1.get('c_iv'), 'c_iv2': r.get('c_iv'), 'c_ivd': safe_diff(r.get('c_iv'), r1.get('c_iv')),
            'p_iv1': r1.get('p_iv'), 'p_iv2': r.get('p_iv'), 'p_ivd': safe_diff(r.get('p_iv'), r1.get('p_iv')),
            'c_delta1': r1.get('c_delta'), 'c_delta2': r.get('c_delta'), 'dc_delta': safe_diff(r.get('c_delta'), r1.get('c_delta')),
            'c_gamma1': r1.get('c_gamma'), 'c_gamma2': r.get('c_gamma'), 'dc_gamma': safe_diff(r.get('c_gamma'), r1.get('c_gamma')),
            'c_theta1': r1.get('c_theta'), 'c_theta2': r.get('c_theta'), 'dc_theta': safe_diff(r.get('c_theta'), r1.get('c_theta')),
            'c_vega1':  r1.get('c_vega'),  'c_vega2':  r.get('c_vega'),  'dc_vega':  safe_diff(r.get('c_vega'), r1.get('c_vega')),
            'p_delta1': r1.get('p_delta'), 'p_delta2': r.get('p_delta'), 'dp_delta': safe_diff(r.get('p_delta'), r1.get('p_delta')),
            'p_gamma1': r1.get('p_gamma'), 'p_gamma2': r.get('p_gamma'), 'dp_gamma': safe_diff(r.get('p_gamma'), r1.get('p_gamma')),
            'p_theta1': r1.get('p_theta'), 'p_theta2': r.get('p_theta'), 'dp_theta': safe_diff(r.get('p_theta'), r1.get('p_theta')),
            'p_vega1':  r1.get('p_vega'),  'p_vega2':  r.get('p_vega'),  'dp_vega':  safe_diff(r.get('p_vega'), r1.get('p_vega')),
            'cv1': cv1, 'pv1': pv1, 'cv': cv, 'pv': pv,
            'dcv': safe_diff(cv, cv1), 'dpv': safe_diff(pv, pv1),
            'tv1': nullable_sum([cv1, pv1]), 'tv': nullable_sum([cv, pv]),
            'dtv': safe_diff(nullable_sum([cv, pv]), nullable_sum([cv1, pv1])),
        })

    mp = calc_max_pain(strikes_out) if strikes_out else 0
    def avg_iv(key, radius=2):
        vals = [x[key] for x in strikes_out if x[key] is not None and abs(x['s'] - spot) <= radius]
        return round(sum(vals)/len(vals), 2) if vals else None
    iv_c = avg_iv('c_iv2'); iv_c1 = avg_iv('c_iv1')
    iv_p = avg_iv('p_iv2'); iv_p1 = avg_iv('p_iv1')
    pcr_oi  = safe_ratio(tp_oi, tc_oi)
    pcr_oi1 = safe_ratio(tp_oi1, tc_oi1)
    pcr_vol = safe_ratio(tp_vol, tc_vol)
    pcr_vol1 = safe_ratio(tp_vol1, tc_vol1)

    return {
        'name': vinfo['name'], 'color': vinfo['color'], 'strikes': strikes_out, 'mp': mp,
        'iv_c_atm': iv_c, 'iv_c_atm1': iv_c1, 'div_c_atm': safe_diff(iv_c, iv_c1),
        'iv_p_atm': iv_p, 'iv_p_atm1': iv_p1, 'div_p_atm': safe_diff(iv_p, iv_p1),
        'pcr_oi': pcr_oi, 'pcr_oi1': pcr_oi1, 'dpcr_oi': safe_diff(pcr_oi, pcr_oi1),
        'pcr_vol': pcr_vol, 'pcr_vol1': pcr_vol1, 'dpcr_vol': safe_diff(pcr_vol, pcr_vol1),
        'coi': tc_oi, 'poi': tp_oi, 'coi1': tc_oi1, 'poi1': tp_oi1,
        'dcoi': safe_diff(tc_oi, tc_oi1), 'dpoi': safe_diff(tp_oi, tp_oi1),
        'cvol': tc_vol, 'pvol': tp_vol, 'cvol1': tc_vol1, 'pvol1': tp_vol1,
        'd_cvol': safe_diff(tc_vol, tc_vol1), 'd_pvol': safe_diff(tp_vol, tp_vol1),
        'd_tvol': safe_diff(nullable_sum([tc_vol, tp_vol]), nullable_sum([tc_vol1, tp_vol1])),
    }

# ═══════════════════════════════════════
# RANKINGS AGREGADOS (cross-expiration)
# ═══════════════════════════════════════
def build_agg_ranks(all_data, vencimentos):
    strike_agg = {}
    for v in vencimentos:
        k = v['key']
        for st in all_data[k]['strikes']:
            s = st['s']
            if s not in strike_agg:
                strike_agg[s] = {
                    's': s, 'cv': 0, 'pv': 0, 'cv1': 0, 'pv1': 0,
                    'co2': 0, 'po2': 0, 'co1': 0, 'po1': 0,
                    'dcv': 0, 'dpv': 0, 'dtv': 0, 'dco': 0, 'dpo': 0,
                    'c_ivs': [], 'c_ivs1': [], 'p_ivs': [], 'p_ivs1': [],
                    'dc_gamma': 0.0, 'dp_gamma': 0.0,
                    'dc_delta': 0.0, 'dp_delta': 0.0,
                    'dc_vega': 0.0,  'dp_vega': 0.0,
                    'dc_theta': 0.0, 'dp_theta': 0.0,
                    'n_venc': 0,
                }
            a = strike_agg[s]
            for fld in ('cv','pv','cv1','pv1','co2','po2','co1','po1','dcv','dpv','dtv','dco','dpo'):
                a[fld] = nullable_sum([a[fld], st.get(fld)])
            for fld in ('dc_gamma','dp_gamma','dc_delta','dp_delta','dc_vega','dp_vega','dc_theta','dp_theta'):
                value = st.get(fld)
                a[fld] = a[fld] + value if a[fld] is not None and value is not None else None
            if st['c_iv2'] is not None: a['c_ivs'].append(st['c_iv2'])
            if st['c_iv1'] is not None: a['c_ivs1'].append(st['c_iv1'])
            if st['p_iv2'] is not None: a['p_ivs'].append(st['p_iv2'])
            if st['p_iv1'] is not None: a['p_ivs1'].append(st['p_iv1'])
            a['n_venc'] += 1

    for a in strike_agg.values():
        a['c_iv_avg']  = round(sum(a['c_ivs'])/len(a['c_ivs']), 2) if a['c_ivs'] else None
        a['c_iv_avg1'] = round(sum(a['c_ivs1'])/len(a['c_ivs1']), 2) if a['c_ivs1'] else None
        a['p_iv_avg']  = round(sum(a['p_ivs'])/len(a['p_ivs']), 2) if a['p_ivs'] else None
        a['p_iv_avg1'] = round(sum(a['p_ivs1'])/len(a['p_ivs1']), 2) if a['p_ivs1'] else None
        a['div_c'] = round(a['c_iv_avg'] - a['c_iv_avg1'], 2) if (a['c_iv_avg'] is not None and a['c_iv_avg1'] is not None) else None
        a['div_p'] = round(a['p_iv_avg'] - a['p_iv_avg1'], 2) if (a['p_iv_avg'] is not None and a['p_iv_avg1'] is not None) else None
        a['tv'] = nullable_sum([a['cv'], a['pv']]); a['tv1'] = nullable_sum([a['cv1'], a['pv1']])
        for k_del in ('c_ivs', 'c_ivs1', 'p_ivs', 'p_ivs1'):
            del a[k_del]

    return sorted(strike_agg.values(), key=lambda x: x['s'])

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def main():
    print("=" * 65)
    print("  BOVA11 Dashboard PRO — Versão Automatizada")
    print("=" * 65)
    print()
    
    # Auto-descobre as datas
    print("  🔍 Detectando datas nos arquivos CSV...")
    TAG_D, TAG_D1 = discover_dates()
    
    DATA_D = format_date_label(TAG_D)
    DATA_D1 = format_date_label(TAG_D1) if TAG_D1 else DATA_D
    
    print(f"  📅 D-1 detectado: {DATA_D1} (tag: {TAG_D1 or 'N/A'})")
    print(f"  📅 D detectado:   {DATA_D} (tag: {TAG_D})")
    print()
    
    # Solicita o spot
    SPOT = ask_spot()
    
    print()
    print("=" * 65)
    print("  PROCESSANDO DADOS")
    print("=" * 65)
    print(f"  D-1 : {DATA_D1}/{ANO}")
    print(f"  D   : {DATA_D}/{ANO}")
    print(f"  Dir : {os.path.abspath(CSV_DIR)}")
    print(f"  Spot: {SPOT:.2f}")
    print()

    exp_files = discover_expirations(TAG_D, TAG_D1)
    exp_files = [entry for entry in exp_files if _resolve_session_and_expiry_t(entry['label'], TAG_D) != 0]
    if not exp_files:
        print(f"⚠ Nenhum vencimento encontrado para TAG_D='{TAG_D}' em '{CSV_DIR}'.")
        sys.exit(1)

    mon_map = {'jan':1,'fev':2,'mar':3,'abr':4,'mai':5,'jun':6,'jul':7,'ago':8,'set':9,'out':10,'nov':11,'dez':12}
    def exp_sort_key(label):
        m = re.search(r'(\d{1,2})\s*([A-Za-zç]{3})', label.lower())
        if m: return (mon_map.get(m.group(2)[:3], 99), int(m.group(1)), label)
        return (99, 99, label)
    exp_files.sort(key=lambda e: exp_sort_key(e["label"]))

    PALETTE = ["red", "blu", "grn", "yel", "pur", "org", "cyn"]
    VENCIMENTOS = []
    for i, e in enumerate(exp_files):
        key = f"V{i+1}"
        VENCIMENTOS.append({
            "key": key, "name": e["label"],
            "fech_csv":    os.path.join(CSV_DIR, e["fech_d"]),
            "vol_csv":     os.path.join(CSV_DIR, e["vol_d"]) if e["vol_d"] else None,
            "fech_csv_d1": os.path.join(CSV_DIR, e["fech_d1"]) if e["fech_d1"] else None,
            "vol_csv_d1":  os.path.join(CSV_DIR, e["vol_d1"]) if e["vol_d1"] else None,
            "color": PALETTE[i % len(PALETTE)],
        })

    fech_d, vol_d, fech_d1, vol_d1 = {}, {}, {}, {}
    for v in VENCIMENTOS:
        k = v["key"]
        fech_d[k]  = parse_fech_csv(v["fech_csv"])
        vol_d[k]   = parse_vol_csv(v["vol_csv"]) if v.get("vol_csv") else {}
        fech_d1[k] = parse_fech_csv(v["fech_csv_d1"]) if v.get("fech_csv_d1") else []
        vol_d1[k]  = parse_vol_csv(v["vol_csv_d1"]) if v.get("vol_csv_d1") else {}
        s1 = f"OK ({len(fech_d1[k])} strikes)" if fech_d1[k] else "N/A (snapshot)"
        print(f"  ✓ {k}: {v['name']:20s} | D: {len(fech_d[k]):3d} | D-1: {s1}")

    print(f"\n  Spot (fornecido): {SPOT:.2f}")

    rankings = {}
    for v in VENCIMENTOS:
        k = v['key']
        rankings[k] = build_exp_data(v, fech_d[k], fech_d1[k], vol_d[k], vol_d1[k], SPOT)

    # GEX
    gex_by_exp = {v['key']: calc_gex(SPOT, fech_d[v['key']]) for v in VENCIMENTOS}
    all_sk = sorted(set(sk for g in gex_by_exp.values() for sk in g))
    all_rows = [row for rows in fech_d.values() for row in rows]
    grid = build_spot_grid(all_rows, SPOT)
    models = {v['key']: gamma_profile_summary(fech_d[v['key']], v['name'], TAG_D, SPOT, spot_range=grid) for v in VENCIMENTOS}
    previous_labels = {e['label'] for e in discover_expirations(TAG_D1, None)} if TAG_D1 else set()
    reference = parse_date_tag(TAG_D).date().isoformat()
    missing_expiries = sorted(label for label in previous_labels - {v['name'] for v in VENCIMENTOS}
        if (expiry_label_to_iso_nearest(label, reference) or reference) >= reference)
    aggregate_model = aggregate_model_summary(models, SPOT, missing_expiries)
    profile, roots = aggregate_model['profile'], aggregate_model['flips']
    agg_gex = []
    for sk in all_sk:
        c = sum(gex_by_exp[k].get(sk, {}).get('c', 0) for k in gex_by_exp)
        p = sum(gex_by_exp[k].get(sk, {}).get('p', 0) for k in gex_by_exp)
        complete = all(not gex_by_exp[k].get(sk, {}).get('partial', False) for k in gex_by_exp)
        agg_gex.append({'s': sk, 'c': round(c) if complete else None, 'p': round(p) if complete else None, 'n': round(c+p) if complete else None})
    flip = min(roots, key=lambda x: abs(x-SPOT)) if roots else None
    known_strikes = [row for row in agg_gex if row['n'] is not None]
    dominant = max(known_strikes, key=lambda x: abs(x['n'])) if known_strikes else {'s': 0, 'n': None}
    gt = {k: round(sum(vv['n'] for vv in gex_by_exp[k].values())) if gex_by_exp[k] and all(not vv['partial'] for vv in gex_by_exp[k].values()) else None for k in gex_by_exp}
    gt_all = sum(gt.values()) if all(value is not None for value in gt.values()) else None
    print(f"  GEX: {gf(gt_all)} | Flip: {flip} | Dom: K{dominant['s']}")

    agg_ranks = build_agg_ranks(rankings, VENCIMENTOS)

    # ═══════════════════════════════════
    # JSON PREP
    # ═══════════════════════════════════
    rankings_json = json.dumps(rankings, ensure_ascii=False)
    keys_js = json.dumps([v['key'] for v in VENCIMENTOS])
    names_js = json.dumps([v['name'] for v in VENCIMENTOS])
    agg_ranks_json = json.dumps(agg_ranks, ensure_ascii=False)

    exp_list = []
    for v in VENCIMENTOS:
        k = v['key']
        r = rankings[k]
        exp_list.append({
            'key': k, 'name': v['name'], 'color': v['color'], 'gex': gt[k],
            'iv_c_atm': r['iv_c_atm'], 'div_c_atm': r['div_c_atm'],
            'iv_p_atm': r['iv_p_atm'], 'div_p_atm': r['div_p_atm'],
            'mp': r['mp'],
            'pcr_oi': r['pcr_oi'], 'dpcr_oi': r['dpcr_oi'],
            'pcr_vol': r['pcr_vol'], 'dpcr_vol': r['dpcr_vol'],
            'coi': r['coi'], 'poi': r['poi'], 'dcoi': r['dcoi'], 'dpoi': r['dpoi'],
            'cvol': r['cvol'], 'pvol': r['pvol'],
            'd_cvol': r['d_cvol'], 'd_pvol': r['d_pvol'], 'd_tvol': r['d_tvol'],
        })
    exp_json = json.dumps(exp_list, ensure_ascii=False)
    agg_gex_json = json.dumps(agg_gex, ensure_ascii=False)

    # GEX por strike POR VENCIMENTO (para tabs individuais)
    gex_per_exp = {}
    for v in VENCIMENTOS:
        k = v['key']
        gex_map = gex_by_exp[k]
        strikes_sorted = sorted(gex_map)
        gex_strikes = [{'s': s, **{key: round(gex_map[s][key]) if not gex_map[s]['partial'] else None for key in ('c','p','n')}} for s in strikes_sorted]
        exp_flip = models[k]['flip']
        known = [row for row in gex_strikes if row['n'] is not None]
        exp_dom = max(known, key=lambda x: abs(x['n'])) if known else {'s': 0, 'n': None}
        oi_total = sum(row['oi_total'] for row in gex_map.values())
        gamma_coverage = sum(row['oi_with_gamma'] for row in gex_map.values()) / oi_total if oi_total else 0
        gex_per_exp[k] = {
            'strikes': gex_strikes,
            'net': gt[k],
            'flip': exp_flip,
            'dom_s': exp_dom['s'],
            'dom_n': exp_dom['n'],
            'regime': models[k]['regime'], 'iv_coverage': models[k]['iv_coverage'],
            'gamma_coverage': gamma_coverage, 'oi_total': oi_total,
        }
    total_oi = sum(row['oi_total'] for row in gex_per_exp.values())
    gex_per_exp['AGG'] = {'iv_coverage': aggregate_model['iv_coverage'],
        'gamma_coverage': sum(row['oi_total'] * row['gamma_coverage'] for row in gex_per_exp.values()) / total_oi if total_oi else 0,
        'regime': aggregate_model['regime'],
        'missing_expiries': missing_expiries, 'flips': roots}
    gex_per_exp_json = json.dumps(gex_per_exp, ensure_ascii=False)

    # ═══════════════════════════════════════════════════
    # GENERATE HTMLs
    # ═══════════════════════════════════════════════════
    generate_html1(rankings_json, keys_js, names_js, agg_ranks_json, SPOT, TAG_D1 or TAG_D, TAG_D, DATA_D1, DATA_D)
    generate_html2(exp_json, agg_gex_json, gex_per_exp_json, keys_js, names_js, gt, gt_all, flip, dominant, SPOT, TAG_D1 or TAG_D, TAG_D, DATA_D1, DATA_D)
    print("\n  CONCLUÍDO ✅")


# ═══════════════════════════════════════
# HTML GENERATION
# ═══════════════════════════════════════
def generate_html1(rankings_json, keys_js, names_js, agg_ranks_json, SPOT, TAG_D1, TAG_D, DATA_D1, DATA_D):
    """HTML 1: Rankings + Tabela + Gregas + Agregado."""
    html = HTML1_TEMPLATE
    for k, v in {
        '__DATA_D1__': DATA_D1, '__DATA_D__': DATA_D, '__ANO__': ANO,
        '__SPOT__': f"{SPOT:.2f}", '__SPOT_NUM__': f"{SPOT:.2f}",
        '__RANKINGS_JSON__': rankings_json, '__KEYS_JS__': keys_js,
        '__NAMES_JS__': names_js, '__AGG_RANKS_JSON__': agg_ranks_json,
    }.items():
        html = html.replace(k, v)
    out = os.path.join(OUTPUT_DIR, f"bova11_rankings_pro_{TAG_D1}_vs_{TAG_D}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  ✅ HTML 1 (Rankings PRO): {out}")


def generate_html2(exp_json, agg_gex_json, gex_per_exp_json, keys_js, names_js, gt, gt_all, flip, dominant, SPOT, TAG_D1, TAG_D, DATA_D1, DATA_D):
    """HTML 2: GEX por vencimento + agregado."""
    metadata = json.loads(gex_per_exp_json).get('AGG', {})
    gex_color = "neut" if gt_all is None else "pos" if gt_all >= 0 else "neg"
    reg = metadata.get('regime', 'Indisponível')
    reg_color = 'pos' if reg == 'Long Gamma' else 'neg' if reg == 'Short Gamma' else 'neut'
    html = HTML2_TEMPLATE
    for k, v in {
        '__DATA_D__': DATA_D, '__DATA_D1__': DATA_D1, '__ANO__': ANO,
        '__SPOT__': f"{SPOT:.2f}",
        '__FLIP__': str(flip if flip is not None else "–"),
        '__GEX_COLOR__': gex_color,
        '__GEX_ALL__': gf(gt_all),
        '__DOM_K__': str(int(dominant['s'])),
        '__DOM_GEX__': gf(dominant['n']),
        '__REG__': reg, '__REG_COLOR__': reg_color,
        '__RMIN__': 'Todos os strikes', '__RMAX__': 'sem corte',
        '__EXP_JSON__': exp_json, '__AGG_JSON__': agg_gex_json,
        '__GEX_PER_EXP_JSON__': gex_per_exp_json,
        '__KEYS_JS__': keys_js, '__NAMES_JS__': names_js,
    }.items():
        html = html.replace(k, v)
    out = os.path.join(OUTPUT_DIR, f"bova11_gex_pro_{TAG_D1}_vs_{TAG_D}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✅ HTML 2 (GEX): {out}")


# ═══════════════════════════════════════════════════════════
# HTML TEMPLATES  (raw strings to avoid JS ${} conflicts)
# ═══════════════════════════════════════════════════════════

HTML1_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>BOVA11 PRO — Rankings __DATA_D1__→__DATA_D__/__ANO__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="shared.js"></script>
<style>
:root{
  --bg:#f3f5f7; --surface:#ffffff; --surface-2:#f7f9fb; --surface-3:#eef3f8;
  --border:#dce2e8; --border-strong:#c9d3dd; --text:#111827; --muted:#5f6b7a;
  --soft:#7a8696; --accent:#2563eb; --accent-soft:rgba(37,99,235,.08);
  --positive:#15803d; --negative:#c2413b; --warning:#b7791f; --cyan:#0f7b9b;
  --font:'Instrument Sans',system-ui,sans-serif; --mono:'JetBrains Mono',monospace;
  --shadow:0 18px 45px rgba(17,24,39,.06);
}
[data-theme="dark"]{
  --bg:#0f141b; --surface:#141b23; --surface-2:#19212b; --surface-3:#1f2933;
  --border:#273240; --border-strong:#334155; --text:#e5ebf3; --muted:#a3afbf;
  --soft:#7d8a9b; --accent:#6ea8ff; --accent-soft:rgba(110,168,255,.12);
  --positive:#4ade80; --negative:#f87171; --warning:#f6c768; --cyan:#67e8f9;
  --shadow:0 22px 50px rgba(0,0,0,.28);
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;-webkit-font-smoothing:antialiased}
button{font:inherit}
.page{max-width:1480px;margin:0 auto;padding:28px 24px 44px}
.page-header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:18px}
.page-copy{max-width:68ch}
.page-kicker{font:500 .72rem var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--soft);margin-bottom:10px}
.page-header h1{font-size:1.7rem;letter-spacing:-.05em;line-height:1.05}
.page-desc{margin-top:8px;color:var(--muted);line-height:1.55}
.page-tools{display:flex;align-items:flex-start;gap:10px}
.theme-toggle{border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:999px;padding:9px 14px;cursor:pointer;transition:border-color .2s,color .2s}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent)}
.theme-toggle.hidden{display:none}
.scope{background:var(--surface);border:1px solid var(--border);border-radius:22px;padding:18px 20px;box-shadow:var(--shadow);display:flex;justify-content:space-between;gap:20px;align-items:flex-start}
.scope-copy{max-width:70ch}
.scope-label{font:.72rem var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--soft);margin-bottom:8px}
.scope h2{font-size:1.2rem;letter-spacing:-.03em;margin-bottom:6px}
.scope p{color:var(--muted);line-height:1.55}
.view-switch{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.exp-tabs,.view-switch{margin-top:14px}
.exp-tabs{display:flex;gap:8px;flex-wrap:wrap}
.tab,.view-btn{border:1px solid var(--border);background:var(--surface);color:var(--muted);border-radius:999px;padding:10px 14px;cursor:pointer;transition:border-color .2s,background .2s,color .2s}
.tab:hover,.view-btn:hover{border-color:var(--accent);color:var(--text)}
.tab.active,.view-btn.active{background:var(--accent-soft);border-color:rgba(37,99,235,.2);color:var(--accent)}
.summary-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:14px 16px;box-shadow:var(--shadow)}
.kpi .label{font:.68rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.kpi .value{margin-top:8px;font-size:1.3rem;font-weight:700;letter-spacing:-.04em}
.kpi .detail{margin-top:6px;color:var(--muted);font-size:.8rem;line-height:1.45}
.view-panel{display:none}
.view-panel.active{display:block}
.summary-layout{display:grid;grid-template-columns:1.1fr .9fr;gap:14px;margin-bottom:14px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:22px;padding:18px;box-shadow:var(--shadow)}
.panel + .panel{margin-top:14px}
.panel-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:14px}
.panel-head h3{font-size:1.02rem;letter-spacing:-.03em}
.panel-kicker{font:.72rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.panel-sub{color:var(--muted);font-size:.84rem;line-height:1.45}
.signal-list{display:grid;gap:10px}
.signal-row{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--border)}
.signal-row:first-child{padding-top:0;border-top:none}
.signal-row strong{display:block;font-size:.92rem;letter-spacing:-.01em}
.signal-row span{display:block;margin-top:3px;color:var(--muted);font-size:.79rem;line-height:1.45}
.signal-value{text-align:right;white-space:nowrap;font:600 .9rem var(--mono)}
.pos{color:var(--positive)} .neg{color:var(--negative)} .neut{color:var(--muted)} .warn{color:var(--warning)} .cyn{color:var(--cyan)}
.strike-list{display:grid;gap:10px}
.strike-item{padding:12px 14px;border:1px solid var(--border);border-radius:16px;background:var(--surface-2)}
.strike-top{display:flex;justify-content:space-between;gap:12px;align-items:baseline}
.strike-top strong{font-size:.95rem}
.strike-meta{margin-top:4px;color:var(--muted);font-size:.78rem;line-height:1.45}
.section-title{font:.8rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft);margin:18px 0 10px}
.table-wrap{overflow:auto;max-height:560px;border-top:1px solid var(--border)}
table{width:100%;border-collapse:collapse}
th,td{padding:11px 10px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap;font-size:.83rem}
th{position:sticky;top:0;background:var(--surface);z-index:2;color:var(--soft);font:500 .68rem var(--mono);letter-spacing:.08em;text-transform:uppercase}
th:first-child,td:first-child{text-align:left}
tr.focus{background:var(--accent-soft)}
.grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:1180px){.summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:960px){.summary-layout,.grid-2{grid-template-columns:1fr}}
@media(max-width:720px){
  .page{padding:20px 14px 32px}
  .page-header,.scope{flex-direction:column}
  .summary-grid{grid-template-columns:1fr 1fr}
}
@media(max-width:520px){.summary-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="page">
  <header class="page-header">
    <div class="page-copy">
      <div class="page-kicker">Hoje / Rankings</div>
      <h1>Rankings & Overview</h1>
      <p class="page-desc">Comparativo __DATA_D1__ → __DATA_D__/__ANO__ · Spot __SPOT__ · o resumo abre agregado e o detalhe fica em segundo nível.</p>
    </div>
    <div class="page-tools">
      <button id="theme-toggle" class="theme-toggle" onclick="toggleTheme()">Tema</button>
    </div>
  </header>

  <section class="scope">
    <div class="scope-copy">
      <div class="scope-label" id="scopeLabel">Agregado do dia</div>
      <h2 id="scopeTitle">Leitura consolidada dos vencimentos</h2>
      <p id="scopeDesc">Resumo técnico para entender volume, OI e variações implícitas antes de abrir tabela completa.</p>
    </div>
    <div class="view-switch" id="viewSel"></div>
  </section>

  <div class="exp-tabs" id="expTabs"></div>
  <div class="summary-grid" id="kpis"></div>

  <div id="vSummary" class="view-panel"></div>
  <div id="vTable" class="view-panel"></div>
  <div id="vGreeks" class="view-panel"></div>
</div>

<script>
const D = __RANKINGS_JSON__;
const KEYS = __KEYS_JS__;
const NAMES = __NAMES_JS__;
const AGG = __AGG_RANKS_JSON__;
const SPOT = __SPOT_NUM__;
let cur = 'AGG';
let view = 'summary';

const cc = c => ({red:'var(--negative)', blu:'var(--accent)', grn:'var(--positive)', yel:'var(--warning)', pur:'var(--accent)', org:'var(--warning)', cyn:'var(--cyan)'}[c] || 'var(--accent)');
const fI = n => { if(n == null) return '–'; const a = Math.abs(n); return a >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(Math.round(n)); };
const fP = n => n == null ? '–' : `${n >= 0 ? '+' : ''}${n.toFixed(2)}pp`;
const fPct = n => n == null ? '–' : `${n >= 0 ? '+' : ''}${n.toFixed(1)}%`;
const fNum = (n, d = 2) => n == null ? '–' : n.toFixed(d);
const pc = n => n == null ? 'neut' : n >= 0 ? 'pos' : 'neg';
const nullableSum = values => values.every(value => value != null) ? values.reduce((a, b) => a + b, 0) : null;
const rowsForScope = () => cur === 'AGG' ? AGG : D[cur].strikes;
const getCurrent = () => cur === 'AGG' ? null : D[cur];

function pickTop(rows, key, mode = 'abs') {
  const valid = rows.filter(row => row[key] != null);
  if (!valid.length) return null;
  const sorted = [...valid].sort((a, b) => {
    const av = mode === 'abs' ? Math.abs(a[key]) : a[key];
    const bv = mode === 'abs' ? Math.abs(b[key]) : b[key];
    return bv - av;
  });
  return sorted[0];
}

function renderTabs() {
  const el = document.getElementById('expTabs');
  const tabs = [`<button class="tab ${cur === 'AGG' ? 'active' : ''}" data-k="AGG">Agregado</button>`];
  KEYS.forEach((key, index) => {
    tabs.push(`<button class="tab ${cur === key ? 'active' : ''}" data-k="${key}" style="${cur === key ? `border-color:${cc(D[key].color)};color:${cc(D[key].color)}` : ''}">${NAMES[index]}</button>`);
  });
  el.innerHTML = tabs.join('');
  el.querySelectorAll('.tab').forEach(tab => {
    tab.onclick = () => {
      cur = tab.dataset.k;
      if (cur === 'AGG' && view === 'greeks') view = 'summary';
      renderAll();
    };
  });
}

function renderViewSwitch() {
  const el = document.getElementById('viewSel');
  const views = [{id:'summary', label:'Resumo'}, {id:'table', label:'Tabela'}];
  if (cur !== 'AGG') views.push({id:'greeks', label:'Gregas'});
  if (!views.find(item => item.id === view)) view = 'summary';
  el.innerHTML = views.map(item => `<button class="view-btn ${view === item.id ? 'active' : ''}" data-view="${item.id}">${item.label}</button>`).join('');
  el.querySelectorAll('.view-btn').forEach(btn => {
    btn.onclick = () => {
      view = btn.dataset.view;
      renderAll();
    };
  });
}

function renderScope() {
  const label = document.getElementById('scopeLabel');
  const title = document.getElementById('scopeTitle');
  const desc = document.getElementById('scopeDesc');
  if (cur === 'AGG') {
    label.textContent = 'Agregado do dia';
    title.textContent = 'Leitura consolidada dos vencimentos';
    desc.textContent = `Use este resumo para localizar onde volume, OI e IV estão mais ativos antes de abrir o detalhe por strike. ${KEYS.length} vencimentos foram consolidados.`;
    return;
  }
  const e = D[cur];
  label.textContent = e.name;
  title.textContent = `Resumo técnico de ${e.name}`;
  desc.textContent = `Max pain em K${e.mp}, IV ATM e principais deslocamentos de volume/OI reunidos em uma única superfície.`;
}

function renderKPIs() {
  const el = document.getElementById('kpis');
  if (cur === 'AGG') {
    const sum = field => nullableSum(KEYS.map(key => D[key][field]));
    const tco = sum('coi'), tpo = sum('poi'), tcv = sum('cvol'), tpv = sum('pvol');
    const doi = nullableSum([sum('dcoi'), sum('dpoi')]);
    const dvol = sum('d_tvol');
    const pcr = tpo == null || tco == null ? 'N/A' : (tpo / Math.max(tco, 1)).toFixed(2);
    el.innerHTML = [
      ['Vencimentos', KEYS.length, 'Cobertura consolidada do dia', 'cyn'],
      ['OI Total', fI(nullableSum([tco, tpo])), `Call ${fI(tco)} · Put ${fI(tpo)}`, ''],
      ['ΔOI Total', fI(doi), `Call ${fI(sum('dcoi'))} · Put ${fI(sum('dpoi'))}`, pc(doi)],
      ['Volume D', fI(nullableSum([tcv, tpv])), `Call ${fI(tcv)} · Put ${fI(tpv)}`, ''],
      ['PCR OI', pcr, `ΔVolume ${dvol == null ? 'N/A' : (dvol >= 0 ? '+' : '') + fI(dvol)}`, pc(dvol)],
    ].map(([label, value, detail, tone]) => `<div class="kpi"><div class="label">${label}</div><div class="value ${tone}">${value}</div><div class="detail">${detail}</div></div>`).join('');
    return;
  }
  const e = D[cur];
  el.innerHTML = [
    ['Max Pain', e.mp == null ? 'N/A' : `K${e.mp}`, 'Ponto de equilíbrio do vencimento', ''],
    ['IV Call ATM', e.iv_c_atm == null ? 'N/A' : `${e.iv_c_atm}%`, `Δ ${fP(e.div_c_atm)}`, pc(e.div_c_atm)],
    ['IV Put ATM', e.iv_p_atm == null ? 'N/A' : `${e.iv_p_atm}%`, `Δ ${fP(e.div_p_atm)}`, pc(e.div_p_atm)],
    ['PCR OI', e.pcr_oi == null ? 'N/A' : e.pcr_oi, `Δ ${e.dpcr_oi == null ? 'N/A' : (e.dpcr_oi >= 0 ? '+' : '') + e.dpcr_oi.toFixed(2)}`, pc(e.dpcr_oi)],
    ['Volume D', fI(nullableSum([e.cvol, e.pvol])), `Δ ${e.d_tvol == null ? 'N/A' : (e.d_tvol >= 0 ? '+' : '') + fI(e.d_tvol)}`, pc(e.d_tvol)],
  ].map(([label, value, detail, tone]) => `<div class="kpi"><div class="label">${label}</div><div class="value ${tone}">${value}</div><div class="detail">${detail}</div></div>`).join('');
}

function signalRow(label, value, detail, tone = '') {
  return `<div class="signal-row"><div><strong>${label}</strong><span>${detail}</span></div><div class="signal-value ${tone}">${value}</div></div>`;
}

function strikeCard(title, value, meta, tone = '') {
  return `<div class="strike-item"><div class="strike-top"><strong>${title}</strong><span class="${tone}">${value}</span></div><div class="strike-meta">${meta}</div></div>`;
}

function renderSummary() {
  const rows = rowsForScope();
  const topVolume = pickTop(rows, 'tv', 'value');
  const topDeltaVol = pickTop(rows, 'dtv');
  const topDeltaCall = pickTop(rows, 'dco');
  const topDeltaPut = pickTop(rows, 'dpo');
  const topIvCall = pickTop(rows, cur === 'AGG' ? 'div_c' : 'c_ivd');
  const topIvPut = pickTop(rows, cur === 'AGG' ? 'div_p' : 'p_ivd');

  const topStrikeRows = [...rows].sort((a, b) => (b.tv || 0) - (a.tv || 0)).slice(0, 6);
  const strikeCards = topStrikeRows.map(row => {
    const ivCall = cur === 'AGG' ? row.div_c : row.c_ivd;
    return strikeCard(`K${row.s}`, fI(row.tv), `ΔVol ${row.dtv >= 0 ? '+' : ''}${fI(row.dtv)} · ΔIV Call ${ivCall == null ? '–' : fP(ivCall)}`, pc(row.dtv));
  }).join('');

  let summaryHtml = `
    <div class="summary-layout">
      <section class="panel">
        <div class="panel-head">
          <div><div class="panel-kicker">Resumo</div><h3>Top movimentos</h3></div>
          <div class="panel-sub">Leitura rápida para decidir se vale abrir o detalhe por strike.</div>
        </div>
        <div class="signal-list">
          ${topVolume ? signalRow('Maior volume total', `K${topVolume.s} · ${fI(topVolume.tv)}`, `Call ${fI(topVolume.cv)} · Put ${fI(topVolume.pv)}`) : ''}
          ${topDeltaVol ? signalRow('Maior delta de volume', `K${topDeltaVol.s} · ${topDeltaVol.dtv >= 0 ? '+' : ''}${fI(topDeltaVol.dtv)}`, `ΔCall ${topDeltaVol.dcv >= 0 ? '+' : ''}${fI(topDeltaVol.dcv)} · ΔPut ${topDeltaVol.dpv >= 0 ? '+' : ''}${fI(topDeltaVol.dpv)}`, pc(topDeltaVol.dtv)) : ''}
          ${topDeltaCall ? signalRow('Maior delta de OI call', `K${topDeltaCall.s} · ${topDeltaCall.dco >= 0 ? '+' : ''}${fI(topDeltaCall.dco)}`, `OI D ${fI(topDeltaCall.co2)} · OI D-1 ${fI(topDeltaCall.co1)}`, pc(topDeltaCall.dco)) : ''}
          ${topDeltaPut ? signalRow('Maior delta de OI put', `K${topDeltaPut.s} · ${topDeltaPut.dpo >= 0 ? '+' : ''}${fI(topDeltaPut.dpo)}`, `OI D ${fI(topDeltaPut.po2)} · OI D-1 ${fI(topDeltaPut.po1)}`, pc(topDeltaPut.dpo)) : ''}
          ${topIvCall ? signalRow('Maior variação de IV call', `K${topIvCall.s} · ${fP(cur === 'AGG' ? topIvCall.div_c : topIvCall.c_ivd)}`, `Leitura de compressão ou expansão implícita`, pc(cur === 'AGG' ? topIvCall.div_c : topIvCall.c_ivd)) : ''}
          ${topIvPut ? signalRow('Maior variação de IV put', `K${topIvPut.s} · ${fP(cur === 'AGG' ? topIvPut.div_p : topIvPut.p_ivd)}`, `Útil para localizar assimetria de hedge`, pc(cur === 'AGG' ? topIvPut.div_p : topIvPut.p_ivd)) : ''}
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <div><div class="panel-kicker">Foco</div><h3>Strikes de atenção</h3></div>
          <div class="panel-sub">Os níveis com maior combinação de volume e deslocamento.</div>
        </div>
        <div class="strike-list">${strikeCards}</div>
      </section>
    </div>`;

  if (cur === 'AGG') {
    const rowsByExp = KEYS.map(key => D[key]).map(exp => `
      <tr>
        <td style="color:${cc(exp.color)}">${exp.name}</td>
        <td>${exp.mp == null ? 'N/A' : `K${exp.mp}`}</td>
        <td>${fI(nullableSum([exp.coi, exp.poi]))}</td>
        <td class="${pc(nullableSum([exp.dcoi, exp.dpoi]))}">${nullableSum([exp.dcoi, exp.dpoi]) == null ? 'N/A' : (exp.dcoi + exp.dpoi >= 0 ? '+' : '') + fI(exp.dcoi + exp.dpoi)}</td>
        <td>${fI(nullableSum([exp.cvol, exp.pvol]))}</td>
        <td class="${pc(exp.d_tvol)}">${exp.d_tvol == null ? 'N/A' : (exp.d_tvol >= 0 ? '+' : '') + fI(exp.d_tvol)}</td>
        <td>${exp.pcr_oi == null ? 'N/A' : exp.pcr_oi}</td>
        <td>${exp.iv_c_atm == null ? 'N/A' : exp.iv_c_atm + '%'}</td>
      </tr>`).join('');
    summaryHtml += `
      <section class="panel">
        <div class="panel-head">
          <div><div class="panel-kicker">Cobertura</div><h3>Resumo por vencimento</h3></div>
          <div class="panel-sub">Comparação rápida antes de aprofundar em um vencimento específico.</div>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Vencimento</th><th>Max Pain</th><th>OI Total</th><th>ΔOI</th><th>Volume</th><th>ΔVolume</th><th>PCR OI</th><th>IV C ATM</th></tr></thead>
            <tbody>${rowsByExp}</tbody>
          </table>
        </div>
      </section>`;
  } else {
    const exp = D[cur];
    const topPriceCall = pickTop(rows, 'cp_pct');
    const topPricePut = pickTop(rows, 'pp_pct');
    summaryHtml += `
      <section class="panel">
        <div class="panel-head">
          <div><div class="panel-kicker">Contexto</div><h3>Leitura do vencimento</h3></div>
          <div class="panel-sub">Resumo para leitura técnica sem abrir a tabela completa.</div>
        </div>
        <div class="signal-list">
          ${signalRow('OI total', fI(nullableSum([exp.coi, exp.poi])), `Call ${fI(exp.coi)} · Put ${fI(exp.poi)}`)}
          ${signalRow('ΔOI líquido', exp.dcoi == null || exp.dpoi == null ? 'N/A' : `${exp.dcoi + exp.dpoi >= 0 ? '+' : ''}${fI(exp.dcoi + exp.dpoi)}`, `ΔCall ${fI(exp.dcoi)} · ΔPut ${fI(exp.dpoi)}`, pc(exp.dcoi == null || exp.dpoi == null ? null : exp.dcoi + exp.dpoi))}
          ${signalRow('PCR volume', exp.pcr_vol, `Δ ${exp.dpcr_vol == null ? 'N/A' : exp.dpcr_vol.toFixed(2)} vs D-1`, pc(exp.dpcr_vol))}
          ${topPriceCall ? signalRow('Maior alta de call', `K${topPriceCall.s} · ${fPct(topPriceCall.cp_pct)}`, `Preço ${topPriceCall.c_last1.toFixed(2)} → ${topPriceCall.c_last2.toFixed(2)}`, pc(topPriceCall.cp_pct)) : ''}
          ${topPricePut ? signalRow('Maior alta de put', `K${topPricePut.s} · ${fPct(topPricePut.pp_pct)}`, `Preço ${topPricePut.p_last1.toFixed(2)} → ${topPricePut.p_last2.toFixed(2)}`, pc(topPricePut.pp_pct)) : ''}
        </div>
      </section>`;
  }

  document.getElementById('vSummary').innerHTML = summaryHtml;
}

function renderTable() {
  const rows = [...rowsForScope()].sort((a, b) => a.s - b.s);
  let head = '';
  let body = '';
  if (cur === 'AGG') {
    head = '<tr><th>Strike</th><th>OI Call D-1</th><th>OI Call D</th><th>ΔOI Call</th><th>OI Put D-1</th><th>OI Put D</th><th>ΔOI Put</th><th>Vol Call</th><th>Vol Put</th><th>ΔVol Total</th><th>ΔIV Call</th><th>ΔIV Put</th></tr>';
    body = rows.map(row => `
      <tr class="${Math.abs(row.s - SPOT) < 1.5 ? 'focus' : ''}">
        <td>K${row.s}</td>
        <td>${fI(row.co1)}</td><td>${fI(row.co2)}</td><td class="${pc(row.dco)}">${row.dco == null ? '–' : (row.dco >= 0 ? '+' : '') + fI(row.dco)}</td>
        <td>${fI(row.po1)}</td><td>${fI(row.po2)}</td><td class="${pc(row.dpo)}">${row.dpo == null ? '–' : (row.dpo >= 0 ? '+' : '') + fI(row.dpo)}</td>
        <td>${fI(row.cv)}</td><td>${fI(row.pv)}</td><td class="${pc(row.dtv)}">${row.dtv == null ? '–' : (row.dtv >= 0 ? '+' : '') + fI(row.dtv)}</td>
        <td class="${pc(row.div_c)}">${row.div_c == null ? '–' : fP(row.div_c)}</td>
        <td class="${pc(row.div_p)}">${row.div_p == null ? '–' : fP(row.div_p)}</td>
      </tr>`).join('');
  } else {
    head = '<tr><th>Strike</th><th>Call D-1</th><th>Call D</th><th>ΔPreço Call</th><th>Put D-1</th><th>Put D</th><th>ΔPreço Put</th><th>ΔOI Call</th><th>ΔOI Put</th><th>ΔVol Total</th><th>ΔIV Call</th><th>ΔIV Put</th></tr>';
    body = rows.map(row => `
      <tr class="${Math.abs(row.s - SPOT) < 1.5 ? 'focus' : ''}">
        <td>K${row.s}</td>
        <td>${fNum(row.c_last1)}</td><td>${fNum(row.c_last2)}</td><td class="${pc(row.cp_pct)}">${fPct(row.cp_pct)}</td>
        <td>${fNum(row.p_last1)}</td><td>${fNum(row.p_last2)}</td><td class="${pc(row.pp_pct)}">${fPct(row.pp_pct)}</td>
        <td class="${pc(row.dco)}">${row.dco == null ? '–' : (row.dco >= 0 ? '+' : '') + fI(row.dco)}</td>
        <td class="${pc(row.dpo)}">${row.dpo == null ? '–' : (row.dpo >= 0 ? '+' : '') + fI(row.dpo)}</td>
        <td class="${pc(row.dtv)}">${row.dtv == null ? '–' : (row.dtv >= 0 ? '+' : '') + fI(row.dtv)}</td>
        <td class="${pc(row.c_ivd)}">${row.c_ivd == null ? '–' : fP(row.c_ivd)}</td>
        <td class="${pc(row.p_ivd)}">${row.p_ivd == null ? '–' : fP(row.p_ivd)}</td>
      </tr>`).join('');
  }

  document.getElementById('vTable').innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div><div class="panel-kicker">Detalhe</div><h3>${cur === 'AGG' ? 'Tabela agregada por strike' : `Tabela por strike — ${getCurrent().name}`}</h3></div>
        <div class="panel-sub">Abre o detalhe completo sem poluir a primeira dobra.</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>${head}</thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>`;
}

function greekPanel(title, fields) {
  const rows = D[cur].strikes.filter(row => row.s >= 145 && row.s <= 180).sort((a, b) => a.s - b.s);
  const head = fields.map(field => `<th>${field.h}</th>`).join('');
  const body = rows.map(row => `
    <tr class="${Math.abs(row.s - SPOT) < 1.5 ? 'focus' : ''}">
      <td>K${row.s}</td>
      ${fields.map(field => {
        const value = row[field.k];
        const formatted = value == null ? '–' : value.toFixed(field.p || 4);
        return `<td class="${field.d ? pc(value) : ''}">${field.d && value != null && value > 0 ? '+' : ''}${formatted}</td>`;
      }).join('')}
    </tr>`).join('');
  return `
    <section class="panel">
      <div class="panel-head">
        <div><div class="panel-kicker">Gregas</div><h3>${title}</h3></div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Strike</th>${head}</tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>`;
}

function renderGreeks() {
  if (cur === 'AGG') {
    document.getElementById('vGreeks').innerHTML = '';
    return;
  }
  document.getElementById('vGreeks').innerHTML = `
    <div class="grid-2">
      ${greekPanel('Delta Call / Put', [{h:'ΔCall', k:'dc_delta', d:1, p:4}, {h:'Call D-1', k:'c_delta1', p:4}, {h:'Call D', k:'c_delta2', p:4}, {h:'ΔPut', k:'dp_delta', d:1, p:4}, {h:'Put D-1', k:'p_delta1', p:4}, {h:'Put D', k:'p_delta2', p:4}])}
      ${greekPanel('Gamma Call / Put', [{h:'ΔCall', k:'dc_gamma', d:1, p:6}, {h:'Call D-1', k:'c_gamma1', p:6}, {h:'Call D', k:'c_gamma2', p:6}, {h:'ΔPut', k:'dp_gamma', d:1, p:6}, {h:'Put D-1', k:'p_gamma1', p:6}, {h:'Put D', k:'p_gamma2', p:6}])}
      ${greekPanel('Theta Call / Put', [{h:'ΔCall', k:'dc_theta', d:1, p:4}, {h:'Call D-1', k:'c_theta1', p:4}, {h:'Call D', k:'c_theta2', p:4}, {h:'ΔPut', k:'dp_theta', d:1, p:4}, {h:'Put D-1', k:'p_theta1', p:4}, {h:'Put D', k:'p_theta2', p:4}])}
      ${greekPanel('Vega Call / Put', [{h:'ΔCall', k:'dc_vega', d:1, p:4}, {h:'Call D-1', k:'c_vega1', p:4}, {h:'Call D', k:'c_vega2', p:4}, {h:'ΔPut', k:'dp_vega', d:1, p:4}, {h:'Put D-1', k:'p_vega1', p:4}, {h:'Put D', k:'p_vega2', p:4}])}
    </div>`;
}

function showView() {
  document.querySelectorAll('.view-panel').forEach(panel => panel.classList.remove('active'));
  document.getElementById(view === 'summary' ? 'vSummary' : view === 'table' ? 'vTable' : 'vGreeks').classList.add('active');
}

function renderAll() {
  renderScope();
  renderTabs();
  renderViewSwitch();
  renderKPIs();
  renderSummary();
  renderTable();
  renderGreeks();
  showView();
}

renderAll();
</script>
</body>
</html>"""

HTML2_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>BOVA11 — GEX __DATA_D__/__ANO__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="shared.js"></script>
<style>
:root{
  --bg:#f3f5f7; --surface:#ffffff; --surface-2:#f7f9fb; --surface-3:#eef3f8;
  --border:#dce2e8; --border-strong:#c9d3dd; --text:#111827; --muted:#5f6b7a;
  --soft:#7a8696; --accent:#2563eb; --accent-soft:rgba(37,99,235,.08);
  --positive:#15803d; --negative:#c2413b; --warning:#b7791f; --cyan:#0f7b9b;
  --font:'Instrument Sans',system-ui,sans-serif; --mono:'JetBrains Mono',monospace;
  --shadow:0 18px 45px rgba(17,24,39,.06);
}
[data-theme="dark"]{
  --bg:#0f141b; --surface:#141b23; --surface-2:#19212b; --surface-3:#1f2933;
  --border:#273240; --border-strong:#334155; --text:#e5ebf3; --muted:#a3afbf;
  --soft:#7d8a9b; --accent:#6ea8ff; --accent-soft:rgba(110,168,255,.12);
  --positive:#4ade80; --negative:#f87171; --warning:#f6c768; --cyan:#67e8f9;
  --shadow:0 22px 50px rgba(0,0,0,.28);
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;-webkit-font-smoothing:antialiased}
button{font:inherit}
.page{max-width:1440px;margin:0 auto;padding:28px 24px 44px}
.page-header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:18px}
.page-kicker{font:.72rem var(--mono);text-transform:uppercase;letter-spacing:.1em;color:var(--soft);margin-bottom:10px}
.page-header h1{font-size:1.7rem;letter-spacing:-.05em;line-height:1.05}
.page-desc{margin-top:8px;color:var(--muted);line-height:1.55;max-width:70ch}
.theme-toggle{border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:999px;padding:9px 14px;cursor:pointer;transition:border-color .2s,color .2s}
.theme-toggle:hover{border-color:var(--accent);color:var(--accent)}
.theme-toggle.hidden{display:none}
.scope{background:var(--surface);border:1px solid var(--border);border-radius:22px;padding:18px 20px;box-shadow:var(--shadow);display:flex;justify-content:space-between;gap:20px;align-items:flex-start}
.scope-copy{max-width:70ch}
.scope-label{font:.72rem var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--soft);margin-bottom:8px}
.scope h2{font-size:1.2rem;letter-spacing:-.03em;margin-bottom:6px}
.scope p{color:var(--muted);line-height:1.55}
.view-switch,.tabs{margin-top:14px;display:flex;gap:8px;flex-wrap:wrap}
.tab,.view-btn{border:1px solid var(--border);background:var(--surface);color:var(--muted);border-radius:999px;padding:10px 14px;cursor:pointer;transition:border-color .2s,background .2s,color .2s}
.tab:hover,.view-btn:hover{border-color:var(--accent);color:var(--text)}
.tab.active,.view-btn.active{background:var(--accent-soft);border-color:rgba(37,99,235,.2);color:var(--accent)}
.summary-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:18px;padding:14px 16px;box-shadow:var(--shadow)}
.kpi .label{font:.68rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.kpi .value{margin-top:8px;font-size:1.3rem;font-weight:700;letter-spacing:-.04em}
.kpi .detail{margin-top:6px;color:var(--muted);font-size:.8rem;line-height:1.45}
.view-panel{display:none}
.view-panel.active{display:block}
.layout{display:grid;grid-template-columns:1.05fr .95fr;gap:14px;margin-bottom:14px}
.panel{background:var(--surface);border:1px solid var(--border);border-radius:22px;padding:18px;box-shadow:var(--shadow)}
.panel + .panel{margin-top:14px}
.panel-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;margin-bottom:14px}
.panel-head h3{font-size:1.02rem;letter-spacing:-.03em}
.panel-kicker{font:.72rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.panel-sub{color:var(--muted);font-size:.84rem;line-height:1.45}
.signal-list{display:grid;gap:10px}
.signal-row{display:flex;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid var(--border)}
.signal-row:first-child{padding-top:0;border-top:none}
.signal-row strong{display:block;font-size:.92rem;letter-spacing:-.01em}
.signal-row span{display:block;margin-top:3px;color:var(--muted);font-size:.79rem;line-height:1.45}
.signal-value{text-align:right;white-space:nowrap;font:600 .9rem var(--mono)}
.pos{color:var(--positive)} .neg{color:var(--negative)} .neut{color:var(--muted)} .warn{color:var(--warning)} .cyn{color:var(--cyan)}
.bar-list{display:grid;gap:10px}
.bar-item{display:grid;gap:6px;padding:12px 14px;border:1px solid var(--border);border-radius:16px;background:var(--surface-2)}
.bar-top{display:flex;justify-content:space-between;align-items:baseline;gap:12px}
.bar-meta{font-size:.78rem;color:var(--muted);line-height:1.45}
.bar-track{height:10px;border-radius:999px;background:var(--surface-3);overflow:hidden}
.bar-fill{height:100%;border-radius:999px}
.table-wrap{overflow:auto;max-height:600px;border-top:1px solid var(--border)}
table{width:100%;border-collapse:collapse}
th,td{padding:11px 10px;border-bottom:1px solid var(--border);text-align:right;white-space:nowrap;font-size:.83rem}
th{position:sticky;top:0;background:var(--surface);z-index:2;color:var(--soft);font:500 .68rem var(--mono);letter-spacing:.08em;text-transform:uppercase}
th:first-child,td:first-child{text-align:left}
tr.focus{background:var(--accent-soft)}
@media(max-width:1180px){.summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:960px){.layout{grid-template-columns:1fr}}
@media(max-width:720px){
  .page{padding:20px 14px 32px}
  .page-header,.scope{flex-direction:column}
  .summary-grid{grid-template-columns:1fr 1fr}
}
@media(max-width:520px){.summary-grid{grid-template-columns:1fr}}
</style>
</head>
<body>
<div class="page">
  <header class="page-header">
    <div>
      <div class="page-kicker">Hoje / GEX</div>
      <h1>GEX & MaxPain</h1>
      <p class="page-desc">Comparativo __DATA_D1__ → __DATA_D__/__ANO__ · Spot __SPOT__ · abertura em regime agregado, com detalhe por vencimento apenas quando necessário.</p>
    </div>
    <div>
      <button id="theme-toggle" class="theme-toggle" onclick="toggleTheme()">Tema</button>
    </div>
  </header>

  <section class="scope">
    <div class="scope-copy">
      <div class="scope-label" id="scopeLabel">Agregado do dia</div>
      <h2 id="scopeTitle">Regime gamma consolidado</h2>
      <p id="scopeDesc">Visão inicial do flip, do strike dominante e do balanço líquido de exposição antes do detalhe por vencimento.</p>
    </div>
    <div class="view-switch" id="viewSel"></div>
  </section>

  <div class="tabs" id="gexTabs"></div>
  <div class="summary-grid" id="gexKpis"></div>

  <div id="gexSummary" class="view-panel"></div>
  <div id="gexDetail" class="view-panel"></div>
</div>

<script>
const EXP = __EXP_JSON__;
const AGG = __AGG_JSON__;
const GEX_EXP = __GEX_PER_EXP_JSON__;
const KEYS = __KEYS_JS__;
const NAMES = __NAMES_JS__;
const SPOT = parseFloat('__SPOT__');
let gexCur = 'AGG';
let gexView = 'summary';

const cc = c => ({red:'var(--negative)', blu:'var(--accent)', grn:'var(--positive)', yel:'var(--warning)', pur:'var(--accent)', org:'var(--warning)', cyn:'var(--cyan)'}[c] || 'var(--accent)');
const pc = n => n == null ? 'neut' : n >= 0 ? 'pos' : 'neg';
const nullableSum = values => values.every(value => value != null) ? values.reduce((a, b) => a + b, 0) : null;
const fI = n => { if(n == null) return '–'; const a = Math.abs(n); return a >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : a >= 1e3 ? (n / 1e3).toFixed(0) + 'k' : String(Math.round(n)); };
const fG = n => n == null ? '–' : `${n >= 0 ? '+' : '−'}${fI(Math.abs(n))}`;
const signalRow = (label, value, detail, tone = '') => `<div class="signal-row"><div><strong>${label}</strong><span>${detail}</span></div><div class="signal-value ${tone}">${value}</div></div>`;
const currentMeta = () => GEX_EXP[gexCur];
const currentRows = () => gexCur === 'AGG' ? AGG : GEX_EXP[gexCur].strikes;

function renderTabs() {
  const el = document.getElementById('gexTabs');
  const tabs = [`<button class="tab ${gexCur === 'AGG' ? 'active' : ''}" data-k="AGG">Agregado</button>`];
  KEYS.forEach((key, index) => {
    const exp = EXP.find(item => item.key === key);
    tabs.push(`<button class="tab ${gexCur === key ? 'active' : ''}" data-k="${key}" style="${gexCur === key ? `border-color:${cc(exp.color)};color:${cc(exp.color)}` : ''}">${NAMES[index]}</button>`);
  });
  el.innerHTML = tabs.join('');
  el.querySelectorAll('.tab').forEach(tab => {
    tab.onclick = () => {
      gexCur = tab.dataset.k;
      renderAll();
    };
  });
}

function renderViewSel() {
  const el = document.getElementById('viewSel');
  const views = [{id:'summary', label:'Resumo'}, {id:'detail', label:'Detalhe por strike'}];
  el.innerHTML = views.map(view => `<button class="view-btn ${gexView === view.id ? 'active' : ''}" data-view="${view.id}">${view.label}</button>`).join('');
  el.querySelectorAll('.view-btn').forEach(btn => {
    btn.onclick = () => {
      gexView = btn.dataset.view;
      renderAll();
    };
  });
}

function renderScope() {
  const label = document.getElementById('scopeLabel');
  const title = document.getElementById('scopeTitle');
  const desc = document.getElementById('scopeDesc');
  if (gexCur === 'AGG') {
    label.textContent = 'Agregado do dia';
    title.textContent = 'Regime gamma consolidado';
    desc.textContent = 'Resumo operacional do flip agregado, do strike dominante e da distribuição líquida de GEX.';
    return;
  }
  const exp = EXP.find(item => item.key === gexCur);
  const g = GEX_EXP[gexCur];
  const regime = g.regime || 'Indisponível';
  label.textContent = exp.name;
  title.textContent = `Leitura de ${exp.name}`;
  desc.textContent = `${regime} com dominante em K${g.dom_s} e exposição líquida ${fG(g.net)}.`;
}

function renderKpis() {
  const el = document.getElementById('gexKpis');
  if (gexCur === 'AGG') {
    el.innerHTML = [
      ['GEX agregado', '__GEX_ALL__', 'Exposição líquida consolidada', '__GEX_COLOR__', ''],
      ['Regime', '__REG__', 'Leitura do flip agregado', '__REG_COLOR__', ''],
      ['Flip agregado', '__FLIP__', 'Referência versus spot __SPOT__', 'warn', ''],
      ['Dominante', 'K__DOM_K__', 'Net __DOM_GEX__ no strike dominante', '', ''],
      ['Vencimentos', KEYS.length, 'Cobertura agregada do dia', 'cyn', ''],
    ].map(([label, value, detail, tone, style]) => `<div class="kpi"><div class="label">${label}</div><div class="value ${tone}" style="${style}">${value}</div><div class="detail">${detail}</div></div>`).join('');
    return;
  }
  const exp = EXP.find(item => item.key === gexCur);
  const g = GEX_EXP[gexCur];
  const regime = g.regime || 'Indisponível';
  const regClass = regime === 'Long Gamma' ? 'pos' : regime === 'Short Gamma' ? 'neg' : 'neut';
  el.innerHTML = [
    ['Vencimento', exp.name, `Max pain K${exp.mp}`, '', `color:${cc(exp.color)}`],
    ['Net GEX', fG(g.net), `Dominante K${g.dom_s} (${fG(g.dom_n)})`, pc(g.net), ''],
    ['Flip', g.flip == null ? '–' : g.flip, regime, 'warn', ''],
    ['Regime', regime, `Spot ${SPOT.toFixed(2)}`, regClass, ''],
    ['IV Call ATM', exp.iv_c_atm == null ? 'N/A' : `${exp.iv_c_atm == null ? 'N/A' : exp.iv_c_atm + '%'}`, `Δ ${exp.div_c_atm == null ? 'N/A' : (exp.div_c_atm >= 0 ? '+' : '') + exp.div_c_atm.toFixed(2) + 'pp'}`, pc(exp.div_c_atm), ''],
  ].map(([label, value, detail, tone, style]) => `<div class="kpi"><div class="label">${label}</div><div class="value ${tone}" style="${style}">${value}</div><div class="detail">${detail}</div></div>`).join('');
}

function renderBarList(rows) {
  const maxAbs = Math.max(...rows.map(row => Math.abs(row.n)), 1);
  return rows.map(row => {
    const width = Math.max(4, Math.round(Math.abs(row.n) / maxAbs * 100));
    const tone = row.n >= 0 ? 'pos' : 'neg';
    return `
      <div class="bar-item">
        <div class="bar-top"><strong>K${row.s}</strong><span class="${tone}">${fG(row.n)}</span></div>
        <div class="bar-track"><div class="bar-fill" style="width:${width}%;background:${row.n >= 0 ? 'var(--positive)' : 'var(--negative)'}"></div></div>
        <div class="bar-meta">Call ${fG(row.c)} · Put ${fG(row.p)}</div>
      </div>`;
  }).join('');
}

function renderSummary() {
  const rows = currentRows().filter(row => row.n != null);
  const topAbs = [...rows].sort((a, b) => Math.abs(b.n) - Math.abs(a.n)).slice(0, 6);
  const topPositive = [...rows].sort((a, b) => b.n - a.n)[0];
  const topNegative = [...rows].sort((a, b) => a.n - b.n)[0];
  const summaryRows = EXP.map(exp => {
    const g = GEX_EXP[exp.key];
    return `
      <tr>
        <td style="color:${cc(exp.color)}">${exp.name}</td>
        <td class="${pc(g.net)}">${fG(g.net)}</td>
        <td>${g.flip == null ? '–' : g.flip}</td>
        <td>K${g.dom_s}</td>
        <td>${exp.iv_c_atm == null ? 'N/A' : exp.iv_c_atm + '%'}</td>
        <td class="${pc(exp.div_c_atm)}">${exp.div_c_atm == null ? 'N/A' : (exp.div_c_atm >= 0 ? '+' : '') + exp.div_c_atm.toFixed(2) + 'pp'}</td>
        <td>${exp.pcr_oi}</td>
      </tr>`;
  }).join('');

  let html = `
    <div class="layout">
      <section class="panel">
        <div class="panel-head">
          <div><div class="panel-kicker">Resumo</div><h3>Leitura do regime</h3></div>
          <div class="panel-sub">Use este bloco antes de abrir o detalhe por strike.</div>
        </div>
        <div class="signal-list">
          ${gexCur === 'AGG' ? signalRow('Regime agregado', '__REG__', `Flip __FLIP__ versus spot __SPOT__`, '__REG_COLOR__') : signalRow('Regime do vencimento', GEX_EXP[gexCur].regime || 'Indisponível', `Flip ${GEX_EXP[gexCur].flip == null ? '–' : GEX_EXP[gexCur].flip}`, GEX_EXP[gexCur].regime === 'Long Gamma' ? 'pos' : GEX_EXP[gexCur].regime === 'Short Gamma' ? 'neg' : 'neut')}
          ${topPositive ? signalRow('Maior GEX positivo', `K${topPositive.s} · ${fG(topPositive.n)}`, `Call ${fG(topPositive.c)} · Put ${fG(topPositive.p)}`, pc(topPositive.n)) : ''}
          ${topNegative ? signalRow('Maior GEX negativo', `K${topNegative.s} · ${fG(topNegative.n)}`, `Call ${fG(topNegative.c)} · Put ${fG(topNegative.p)}`, pc(topNegative.n)) : ''}
          ${signalRow('Cobertura das gregas', (100 * (currentMeta().gamma_coverage || 0)).toFixed(1) + '% gamma · ' + (100 * (currentMeta().iv_coverage || 0)).toFixed(1) + '% IV', 'Percentual do OI com dados disponíveis. Exposição parcial não define regime.', '')}
          ${signalRow('Hipótese de inventário', 'Call + / Put −', 'GEX estimado em R$ por movimento de 1%; posições dos dealers não são observadas.', '')}
          ${gexCur === 'AGG' && currentMeta().missing_expiries.length ? signalRow('Vencimentos ausentes versus D-1', currentMeta().missing_expiries.join(', '), 'Comparação diária com cobertura diferente.', 'warn') : ''}
          ${signalRow('Spot monitorado', SPOT.toFixed(2), 'Referência usada na leitura de regime', 'cyn')}
          ${signalRow('Range útil', '__RMIN__–__RMAX__', 'Janela usada para o mapa de GEX por strike', '')}
        </div>
      </section>
      <section class="panel">
        <div class="panel-head">
          <div><div class="panel-kicker">Mapa</div><h3>Strikes dominantes</h3></div>
          <div class="panel-sub">Os níveis com maior impacto líquido na exposição.</div>
        </div>
        <div class="bar-list">${renderBarList(topAbs)}</div>
      </section>
    </div>
    <section class="panel">
      <div class="panel-head">
        <div><div class="panel-kicker">Cobertura</div><h3>Resumo por vencimento</h3></div>
        <div class="panel-sub">Comparação rápida para decidir qual vencimento merece aprofundamento.</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Vencimento</th><th>Net GEX</th><th>Flip</th><th>Dominante</th><th>IV C ATM</th><th>ΔIV C</th><th>PCR OI</th></tr></thead>
          <tbody>${summaryRows}</tbody>
        </table>
      </div>
    </section>`;

  document.getElementById('gexSummary').innerHTML = html;
}

function renderDetail() {
  const rows = [...currentRows()].sort((a, b) => a.s - b.s);
  const title = gexCur === 'AGG' ? 'Mapa agregado por strike' : `Mapa por strike — ${EXP.find(item => item.key === gexCur).name}`;
  const body = rows.map(row => `
    <tr class="${Math.abs(row.s - SPOT) < 1.5 ? 'focus' : ''}">
      <td>K${row.s}</td>
      <td class="${pc(row.c)}">${fG(row.c)}</td>
      <td class="${pc(row.p)}">${fG(row.p)}</td>
      <td class="${pc(row.n)}">${fG(row.n)}</td>
      <td>${Math.abs(row.s - SPOT).toFixed(1)}</td>
    </tr>`).join('');
  document.getElementById('gexDetail').innerHTML = `
    <section class="panel">
      <div class="panel-head">
        <div><div class="panel-kicker">Detalhe</div><h3>${title}</h3></div>
        <div class="panel-sub">Tabela completa por strike disponível sob demanda.</div>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Strike</th><th>Call GEX</th><th>Put GEX</th><th>Net</th><th>Distância do spot</th></tr></thead>
          <tbody>${body}</tbody>
        </table>
      </div>
    </section>`;
}

function showView() {
  document.querySelectorAll('.view-panel').forEach(panel => panel.classList.remove('active'));
  document.getElementById(gexView === 'summary' ? 'gexSummary' : 'gexDetail').classList.add('active');
}

function renderAll() {
  renderScope();
  renderTabs();
  renderViewSel();
  renderKpis();
  renderSummary();
  renderDetail();
  showView();
}

renderAll();
</script>
</body>
</html>"""

if __name__ == "__main__":
    main()
