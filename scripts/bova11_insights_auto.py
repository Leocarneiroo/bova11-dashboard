#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 Insights Generator — Versão Automatizada
================================================
Auto-descobre as datas a partir dos arquivos CSV no diretório.
Solicita apenas o spot price via input no terminal.

Uso:
  python3 bova11_insights_auto.py
  
O script irá:
  1. Detectar automaticamente os arquivos CSV no diretório
  2. Identificar as datas (D-1 e D) baseado na data de modificação
  3. Solicitar o spot price do dia
  4. Gerar o HTML de insights
"""

import os, json, math, re, glob, sys
from datetime import datetime, timedelta
from bova11_expiry_family import deduplicate_option_records, collapse_option_records_by_strike, matches_expiry_label, finite_number, expiration_inventory
from bova11_shared import find_profile_roots, expiry_label_to_iso_nearest, tag_to_iso, parse_br_number, parse_b3_close_csv
from bova11_tex_dex_vex import gamma_profile_summary, build_spot_grid, aggregate_spot_profiles

# ═══════════════════════════════════════
# CONFIG — Apenas ajustes finos
# ═══════════════════════════════════════
_BASEDIR       = os.path.dirname(os.path.abspath(__file__))
CSV_DIR        = os.path.join(_BASEDIR, '..', 'data')
ANO            = str(datetime.now().year)
OUTPUT_DIR     = os.path.join(_BASEDIR, '..', 'output')

# Mapeamento de vencimentos → data real para cálculo de DTE
# Atualize conforme necessário
VENC_DATES = {
    "13 fev W2":     "2025-02-13",
    "20 fev Mensal": "2025-02-20",
    "27 fev W4":     "2025-02-27",
    "6 mar W2":      "2025-03-06",
    "20 mar Mensal": "2025-03-20",
}

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
    print()
    print("=" * 65)
    print("  CONFIGURAÇÃO DO SPOT")
    print("=" * 65)
    
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
    for parsed in parse_b3_close_csv(filepath):
        if not matches_expiry_label(parsed['call_ticker'], parsed['put_ticker'], label):
            continue
        results.append({
            'strike': parsed['strike'], 'c_ticker': parsed['call_ticker'], 'p_ticker': parsed['put_ticker'],
            **{f'c_{key}': parsed[f'call_{key}'] for key in ('last','oi','delta','gamma','theta','vega','iv','bid','ask')},
            **{f'p_{key}': parsed[f'put_{key}'] for key in ('last','oi','delta','gamma','theta','vega','iv','bid','ask')},
        })
    return deduplicate_option_records(results, ticker_keys=("c_ticker", "p_ticker"))

def parse_vol_csv(filepath):
    if not filepath or not os.path.exists(filepath):
        return {}
    filename = os.path.basename(filepath)
    label = ""
    m = re.match(r'venc_(.+?)_fechamento__', filename)
    if m:
        label = m.group(1).replace('_', ' ')
    else:
        m = re.match(r'venc (.+?) fechamento', filename)
        if m:
            label = m.group(1)
    with open(filepath, 'r', encoding='latin-1') as f:
        lines = f.readlines()
    if not lines:
        return {}
    header = lines[0].strip().replace('\r', '').split(';')
    strike_col = 5
    for i, h in enumerate(header):
        if 'strike' in h.lower():
            strike_col = i; break
    rows = []
    for line in lines[1:]:
        line = line.strip().replace('\r', '')
        if not line:
            continue
        p = line.split(';')
        if len(p) < 10:
            continue
        # Mesmo filtro de ticker do fechamento para evitar mistura de vencimentos.
        if not matches_expiry_label(p[0], p[10] if len(p) > 10 else "", label):
            continue
        strike = parse_num(p[strike_col])
        if strike <= 0:
            continue
        rows.append({'strike':strike, 'c_ticker':p[0].strip(), 'p_ticker':p[10].strip() if len(p) > 10 else "",
                     'c_oi':parse_br_number(p[2], none_on_blank=True), 'p_oi':parse_br_number(p[8], none_on_blank=True),
                     'cv':parse_br_number(p[1], none_on_blank=True), 'pv':parse_br_number(p[9], none_on_blank=True)})
    return {row['strike']:{'coi':row.get('c_oi'), 'poi':row.get('p_oi'), 'cv':row.get('cv'), 'pv':row.get('pv')}
            for row in collapse_option_records_by_strike(rows)}



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
    return float(best_strike) if best_strike is not None else 180.0

def calc_max_pain(strikes_data):
    if not strikes_data or any(finite_number(s.get(k)) is None for s in strikes_data for k in ('co2', 'po2')):
        return None
    best_s, best_pain = 0, float('inf')
    for test in strikes_data:
        pain = sum(s['co2'] * max(test['s'] - s['s'], 0) + s['po2'] * max(s['s'] - test['s'], 0)
                   for s in strikes_data)
        if pain < best_pain:
            best_pain = pain; best_s = test['s']
    return best_s

def nullable_sum(values):
    values = list(values)
    return sum(values) if all(finite_number(v) is not None for v in values) else None


def calc_gex(spot, fech_rows):
    result = {}
    for row in fech_rows:
        parts = []
        for side, sign in (('c', 1), ('p', -1)):
            oi, gamma = finite_number(row.get(side + '_oi')), finite_number(row.get(side + '_gamma'))
            parts.append(0.0 if oi == 0 else sign * spot ** 2 * gamma * oi / 100
                         if oi is not None and oi > 0 and gamma is not None and gamma >= 0 else None)
        previous = result.get(row['strike'], {'c': 0, 'p': 0})
        call, put = nullable_sum([previous['c'], parts[0]]), nullable_sum([previous['p'], parts[1]])
        result[row['strike']] = {'c': call, 'p': put, 'n': nullable_sum([call, put])}
    return result


def gamma_regime(value):
    if value is None:
        return 'Indisponível'
    return 'Long Gamma' if value > 0 else 'Short Gamma' if value < 0 else 'Neutro'


def build_gex_data(rows, label, tag, spot, spot_range=None):
    static = calc_gex(spot, rows)
    values = [item['n'] for item in static.values()]
    net = sum(values) if values and all(v is not None for v in values) else None
    model = gamma_profile_summary(rows, label, tag, spot, spot_range=spot_range)
    valid = [(strike, item['n']) for strike, item in static.items() if item['n'] is not None]
    dominant = max(valid, key=lambda item: abs(item[1])) if valid else (None, None)
    return {'net': net, 'static_regime': gamma_regime(net),
            'model_regime': model['regime'], 'flip': model['flip'], 'flips': model['flips'],
            'profile': model['profile'], 'iv_coverage': model['iv_coverage'],
            'dom_s': dominant[0], 'dom_n': dominant[1]}


def safe_diff(v2, v1):
    if v2 is None or v1 is None: return None
    return round(v2 - v1, 6)

def safe_pct(v2, v1):
    if v1 is None or v2 is None: return None
    if v1 == 0: return 0.0 if v2 == 0 else 100.0
    return round(((v2 - v1) / abs(v1)) * 100, 2)

def fI(n):
    """Formata inteiro grande → 1.2M / 345k / 123"""
    if n is None: return '–'
    a = abs(n)
    s = '' if n >= 0 else '−'
    if a >= 1_000_000:
        return f"{s}{a/1e6:.1f}M"
    elif a >= 1_000:
        return f"{s}{a/1e3:.0f}k"
    else:
        return f"{s}{round(a)}"

def fP(n):
    """Formata com sinal +/-"""
    if n is None: return '–'
    sign = '+' if n >= 0 else ''
    return f"{sign}{fI(n)}"

def fPct(n):
    if n is None: return '–'
    sign = '+' if n >= 0 else ''
    return f"{sign}{n:.1f}%"

def css_class(n):
    if n is None: return 'neut'
    return 'pos' if n >= 0 else 'neg'

def get_dte(label, DATA_D):
    """DTE calendário; vencimentos passados continuam passados."""
    reference = tag_to_iso(DATA_D.replace('/', ''), int(ANO))
    expiry = expiry_label_to_iso_nearest(label, reference)
    return (datetime.fromisoformat(expiry) - datetime.fromisoformat(reference)).days if expiry and reference else None

# ═══════════════════════════════════════
# DISCOVERY DE VENCIMENTOS
# ═══════════════════════════════════════
def discover_expirations(TAG_D, TAG_D1):
    exps = []
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
    for e in exps:
        for k in ('vol_d', 'fech_d1', 'vol_d1'):
            if e[k] and not os.path.exists(os.path.join(CSV_DIR, e[k])):
                e[k] = None
    return exps

# ═══════════════════════════════════════
# BUILD DATA POR VENCIMENTO
# ═══════════════════════════════════════
def build_exp_data(vinfo, rows_d, rows_d1, volmap_d, volmap_d1, spot):
    rows_d = collapse_option_records_by_strike(rows_d)
    rows_d1 = collapse_option_records_by_strike(rows_d1)
    d1_map = {r['strike']: r for r in rows_d1} if rows_d1 else {}
    strikes_out = []
    tc_oi = tp_oi = tc_vol = tp_vol = 0
    tc_oi1 = tp_oi1 = tc_vol1 = tp_vol1 = 0

    for r in rows_d:
        s = r['strike']
        r1 = d1_map.get(s)
        v_d = volmap_d.get(s, {'cv': None, 'pv': None})
        v_1 = (volmap_d1 or {}).get(s, {'cv': None, 'pv': None})
        cv, pv = finite_number(v_d.get('cv')), finite_number(v_d.get('pv'))
        cv1, pv1 = finite_number(v_1.get('cv')), finite_number(v_1.get('pv'))
        snap = r1 is None
        r1 = r1 or {}
        cp_pct = safe_pct(r.get('c_last'), r1.get('c_last'))
        pp_pct = safe_pct(r.get('p_last'), r1.get('p_last'))
        co1 = finite_number(r1.get('c_oi')) if r1.get('c_oi') is not None else finite_number(v_1.get('coi'))
        po1 = finite_number(r1.get('p_oi')) if r1.get('p_oi') is not None else finite_number(v_1.get('poi'))
        co2, po2 = finite_number(r.get('c_oi')), finite_number(r.get('p_oi'))

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
            'c_iv1': r1.get('c_iv') if not snap else None, 'c_iv2': r.get('c_iv'), 'c_ivd': safe_diff(r.get('c_iv'), r1.get('c_iv')) if not snap else None,
            'p_iv1': r1.get('p_iv') if not snap else None, 'p_iv2': r.get('p_iv'), 'p_ivd': safe_diff(r.get('p_iv'), r1.get('p_iv')) if not snap else None,
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
            'tv1': nullable_sum([cv1, pv1]), 'tv': nullable_sum([cv, pv]), 'dtv': safe_diff(nullable_sum([cv, pv]), nullable_sum([cv1, pv1])),
        })

    mp = calc_max_pain(strikes_out) if strikes_out else None
    def avg_iv(key, radius=2):
        vals = [x[key] for x in strikes_out if x[key] is not None and abs(x['s'] - spot) <= radius]
        return round(sum(vals)/len(vals), 2) if vals else None
    iv_c = avg_iv('c_iv2'); iv_c1 = avg_iv('c_iv1')
    iv_p = avg_iv('p_iv2'); iv_p1 = avg_iv('p_iv1')
    pcr_oi  = round(tp_oi / tc_oi, 2) if tc_oi is not None and tc_oi > 0 and tp_oi is not None else None
    pcr_oi1 = round(tp_oi1 / tc_oi1, 2) if tc_oi1 is not None and tc_oi1 > 0 and tp_oi1 is not None else None
    pcr_vol = round(tp_vol / tc_vol, 2) if tc_vol is not None and tc_vol > 0 and tp_vol is not None else None
    pcr_vol1 = round(tp_vol1 / tc_vol1, 2) if tc_vol1 is not None and tc_vol1 > 0 and tp_vol1 is not None else None

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
# GERAÇÃO DE INSIGHTS POR VENCIMENTO
# ═══════════════════════════════════════
def generate_insights(rankings, gex_data, spot, DATA_D):
    """
    Gera lista de dicts com insights textuais e métricas para cada vencimento.
    """
    insights = []
    for key, r in rankings.items():
        g = gex_data[key]
        st = r['strikes']
        dte = get_dte(r['name'], DATA_D)

        # Top strikes
        top_vol = sorted([x for x in st if x['tv'] is not None], key=lambda x: x['tv'], reverse=True)[:5]
        top_doi_c = sorted([x for x in st if x['dco'] is not None], key=lambda x: abs(x['dco']), reverse=True)[:3]
        top_doi_p = sorted([x for x in st if x['dpo'] is not None], key=lambda x: abs(x['dpo']), reverse=True)[:3]
        top_div_c = sorted([s for s in st if s['c_ivd'] is not None], key=lambda x: abs(x['c_ivd']), reverse=True)[:3]
        top_cp = sorted([x for x in st if x['cp_pct'] is not None], key=lambda x: abs(x['cp_pct']), reverse=True)[:3]
        top_pp = sorted([x for x in st if x['pp_pct'] is not None], key=lambda x: abs(x['pp_pct']), reverse=True)[:3]

        # Top OI entry combined
        oi_combined = sorted([x for x in st if x['dco'] is not None and x['dpo'] is not None], key=lambda x: (x['dco'] + x['dpo']), reverse=True)[:5]

        # Call com maior alta / baixa
        calls_up = sorted([s for s in st if s['cp_pct'] is not None and s['cp_pct'] > 0], key=lambda x: x['cp_pct'], reverse=True)
        calls_down = sorted([s for s in st if s['cp_pct'] is not None and s['cp_pct'] < 0], key=lambda x: x['cp_pct'])
        puts_up = sorted([s for s in st if s['pp_pct'] is not None and s['pp_pct'] > 0], key=lambda x: x['pp_pct'], reverse=True)
        puts_down = sorted([s for s in st if s['pp_pct'] is not None and s['pp_pct'] < 0], key=lambda x: x['pp_pct'])

        # Skew ATM
        skew_atm = round(r['iv_p_atm'] - r['iv_c_atm'], 2) if r['iv_p_atm'] is not None and r['iv_c_atm'] is not None else None

        # Volume D total e variação %
        vol_total = nullable_sum([r['cvol'], r['pvol']])
        vol_total1 = nullable_sum([r['cvol1'], r['pvol1']])
        vol_pct = round(((vol_total - vol_total1) / max(vol_total1, 1)) * 100, 1) if vol_total is not None and vol_total1 is not None and vol_total1 > 0 else None

        # Regime
        flip = g['flip']
        regime = g.get('static_regime', gamma_regime(g['net']))

        # Narrative generation
        narrative = build_narrative(r, g, spot, dte, top_vol, top_doi_c, top_doi_p, 
                                     calls_up, calls_down, puts_up, puts_down,
                                     skew_atm, vol_pct, regime, flip)

        insights.append({
            'key': key, 'name': r['name'], 'color': r['color'],
            'dte': dte,
            'iv_c_atm': r['iv_c_atm'], 'div_c_atm': r['div_c_atm'],
            'iv_p_atm': r['iv_p_atm'], 'div_p_atm': r['div_p_atm'],
            'mp': r['mp'],
            'pcr_oi': r['pcr_oi'], 'dpcr_oi': r['dpcr_oi'],
            'pcr_vol': r['pcr_vol'],
            'gex_net': g['net'], 'flip': flip, 'regime': regime,
            'model_regime': g.get('model_regime', 'Indisponível'),
            'flips': g.get('flips', []), 'iv_coverage': g.get('iv_coverage', 0),
            'dom_s': g['dom_s'], 'dom_n': g['dom_n'],
            'coi': r['coi'], 'poi': r['poi'],
            'dcoi': r['dcoi'], 'dpoi': r['dpoi'],
            'cvol': r['cvol'], 'pvol': r['pvol'],
            'd_cvol': r['d_cvol'], 'd_pvol': r['d_pvol'], 'd_tvol': r['d_tvol'],
            'vol_total': vol_total, 'vol_pct': vol_pct,
            'skew_atm': skew_atm,
            'top_vol': top_vol,
            'top_doi_c': top_doi_c, 'top_doi_p': top_doi_p,
            'top_div_c': top_div_c,
            'calls_up': calls_up[:2] if calls_up else [],
            'calls_down': calls_down[:2] if calls_down else [],
            'puts_up': puts_up[:2] if puts_up else [],
            'puts_down': puts_down[:2] if puts_down else [],
            'oi_combined': oi_combined,
            'narrative': narrative,
        })
    return insights


def build_narrative(r, g, spot, dte, top_vol, top_doi_c, top_doi_p,
                    calls_up, calls_down, puts_up, puts_down,
                    skew_atm, vol_pct, regime, flip):
    """Gera texto narrativo automático para cada vencimento."""
    parts = []

    # DTE context
    if dte is not None:
        if dte <= 5:
            parts.append(f'Vencimento muito <span class="highlight">curto ({dte} DTE)</span>')
        elif dte <= 15:
            parts.append(f'Vencimento de <span class="highlight">médio prazo ({dte} DTE)</span>')
        else:
            parts.append(f'Vencimento mais longo (<span class="highlight">{dte} DTE</span>)')

    # Ausência não indica estabilidade; a direção vem do sinal observado.
    div = r['div_c_atm']
    put_change = r['div_p_atm']
    if div is None:
        parts.append('com <span class="highlight">variação de IV call indisponível</span>')
    else:
        direction = 'aumento' if div > 0 else 'queda' if div < 0 else 'estabilidade'
        magnitude = 'acentuado' if abs(div) >= 1.5 else 'moderado' if abs(div) >= .5 else 'pequeno'
        parts.append(f'com <span class="highlight">{direction} de IV call ({magnitude})</span> ({div:+.2f}pp)')
    parts.append(f'IV put: {put_change:+.2f}pp' if put_change is not None else 'IV put indisponível')

    # OI major moves
    if top_doi_c:
        best = top_doi_c[0]
        if abs(best['dco']) >= 1_000_000:
            direction = "explodiu em OI" if best['dco'] > 0 else "teve saída massiva de OI"
            parts.append(f'O <span class="highlight">K{best["s"]:.0f} {direction}</span> em calls ({fP(best["dco"])})')
    if top_doi_p:
        best = top_doi_p[0]
        if abs(best['dpo']) >= 1_000_000:
            direction = "ganhou OI massivo" if best['dpo'] > 0 else "perdeu OI massivo"
            parts.append(f'e <span class="highlight">K{best["s"]:.0f} {direction}</span> em puts ({fP(best["dpo"])})')

    # PCR
    dpcr = r['dpcr_oi']
    if dpcr is not None and abs(dpcr) >= 0.1:
        if dpcr < 0:
            parts.append(f'O <span class="highlight">PCR OI caiu de {r["pcr_oi1"]:.2f} para {r["pcr_oi"]:.2f}</span>, mostrando que calls ganharam participação relativa')
        else:
            parts.append(f'O <span class="highlight">PCR OI subiu de {r["pcr_oi1"]:.2f} para {r["pcr_oi"]:.2f}</span>, indicando aumento de puts relativo')

    # Volume
    if vol_pct is not None and abs(vol_pct) >= 15:
        if vol_pct > 0:
            parts.append(f'O <span class="highlight grn">volume subiu {vol_pct:.0f}%</span> vs D-1 ({fP(r["d_tvol"])})')
        else:
            parts.append(f'O <span class="highlight red">volume caiu {abs(vol_pct):.0f}%</span> vs D-1 ({fP(r["d_tvol"])})')

    # Top volume strike
    if top_vol:
        parts.append(f'com <span class="highlight">K{top_vol[0]["s"]:.0f} como o mais negociado ({fI(top_vol[0]["tv"])})</span>')

    # MaxPain distance
    mp_dist = abs(spot - r['mp']) if r['mp'] is not None else None
    if mp_dist is None:
        parts.append('Max Pain indisponível')
    elif mp_dist >= 5:
        parts.append(f'Com <span class="highlight">MaxPain em K{r["mp"]:.0f}</span> e spot em {spot:.0f}, há uma <span class="highlight red">distância significativa ({mp_dist:.0f} pontos)</span>')
    else:
        parts.append(f'<span class="highlight">MaxPain em K{r["mp"]:.0f}</span> próximo do spot ({spot:.0f})')

    # GEX fornecido e GEX modelado têm definições e cobertura distintas.
    parts.append(f'GEX estático {fP(g["net"])}: regime {regime}')
    modeled = g.get('model_regime', 'Indisponível')
    flip_text = str(flip) if flip is not None else ('Sem cruzamento' if modeled not in ('Indisponível', 'Expirado') else 'Indisponível')
    parts.append(f'Regime modelado no spot: {modeled}; gamma flip: {flip_text}')
    if g.get('flips'):
        parts.append('Raízes modeladas: ' + ', '.join(number_format(v) for v in g['flips']))

    # Skew
    if skew_atm is not None and abs(skew_atm) >= 1.0:
        parts.append(f'O <span class="highlight org">skew put-call ATM de {skew_atm:+.2f}pp</span> mostra que o mercado precifica {"mais risco de queda" if skew_atm > 0 else "mais demanda por calls"}')

    return '. '.join(parts) + '.'


# ═══════════════════════════════════════
# HTML TEMPLATE
# ═══════════════════════════════════════
def number_format(value):
    return f'{value:.2f}' if value is not None else 'N/A'


def iv_format(value):
    return f'{value:.2f}%' if value is not None else 'N/A'


def change_format(value):
    return f'{value:+.2f}pp' if value is not None else 'N/A'


def generate_html(insights, spot, agg_gex_total, agg_flip, agg_regime, total_oi, total_vol, DATA_D1, DATA_D, missing_expiries=None, aggregate_flips=None, aggregate_model_regime="Indisponível"):
    """Gera HTML completo de insights."""
    COLORS = {
        'red': ('var(--red)', 'rgba(207,34,46,.08)'),
        'blu': ('var(--blu)', 'rgba(9,105,218,.08)'),
        'grn': ('var(--grn)', 'rgba(26,127,55,.08)'),
        'yel': ('var(--yel)', 'rgba(154,103,0,.08)'),
        'pur': ('var(--pur)', 'rgba(130,80,223,.08)'),
        'org': ('var(--org)', 'rgba(188,76,0,.08)'),
        'cyn': ('var(--cyn)', 'rgba(9,105,218,.08)'),
    }

    def gf(n):
        if n is None: return "N/A"
        return f"+{n/1e6:.1f}M" if n >= 0 else f"−{abs(n)/1e6:.1f}M"

    iv_values = [i['iv_c_atm'] for i in insights if i['iv_c_atm'] is not None]
    avg_iv = sum(iv_values) / len(iv_values) if iv_values else None
    total_dcoi = nullable_sum(i['dcoi'] for i in insights)
    total_dpoi = nullable_sum(i['dpoi'] for i in insights)
    total_doi = nullable_sum([total_dcoi, total_dpoi])
    total_dvol = nullable_sum(i['d_tvol'] for i in insights)
    regime_color = "var(--grn)" if agg_regime == "Long Gamma" else "var(--red)"
    regime_class = "pos" if agg_regime == "Long Gamma" else "neg"

    def metric_card(label, value, detail, tone=""):
        return (
            f'<div class="summary-card"><div class="sc-label">{label}</div>'
            f'<div class="sc-value {tone}">{value}</div><div class="sc-detail">{detail}</div></div>'
        )

    missing_expiries = missing_expiries or []
    summary_cards_html = metric_card("Cobertura de vencimentos", "Incompleta" if missing_expiries else "Completa",
        "Ausentes: " + ", ".join(missing_expiries) if missing_expiries else "Fechamentos ativos presentes")
    aggregate_flip_status = "Indisponível" if aggregate_model_regime in ("Indisponível", "Expirado") else "Sem cruzamento"
    summary_cards_html += metric_card("Gamma flips modelados", ", ".join(number_format(v) for v in (aggregate_flips or [])) or aggregate_flip_status, "Raízes da curva agregada por spot")
    summary_cards_html += metric_card("Regime modelado agregado", aggregate_model_regime, "Sinal de GEX(S) no spot atual")
    summary_cards_html += "".join([
        metric_card("Regime estático agregado", agg_regime, f"Sinal do GEX fornecido · Spot {spot:.2f}", regime_class),
        metric_card("GEX agregado", gf(agg_gex_total), "Exposição líquida consolidada", css_class(agg_gex_total)),
        metric_card("OI total", fI(total_oi), f"Call {fI(nullable_sum(i['coi'] for i in insights))} · Put {fI(nullable_sum(i['poi'] for i in insights))}"),
        metric_card("ΔOI líquido", fP(total_doi), f"ΔCall {fP(total_dcoi)} · ΔPut {fP(total_dpoi)}", css_class(total_doi)),
        metric_card("ΔVolume", fP(total_dvol), f"Volume D {fI(total_vol)} · IV média {iv_format(avg_iv)}", css_class(total_dvol)),
    ])

    selector_items = []
    exp_sections = []
    for idx, ins in enumerate(insights):
        color_main, color_bg = COLORS.get(ins['color'], ('var(--blu)', 'rgba(9,105,218,.08)'))
        dte_str = f"{ins['dte']} DTE" if ins['dte'] is not None else "DTE n/d"
        mp_gap = abs(spot - ins['mp']) if ins['mp'] is not None else None
        gap_note = "max pain indisponível" if mp_gap is None else "max pain próximo do spot" if mp_gap <= 3 else f"max pain afastado em {mp_gap:.0f} pts"
        dominant_text = f"K{ins['dom_s']:g}" if ins['dom_s'] is not None else 'N/A'
        headline = f"{ins['regime']} com dominante {dominant_text}; {gap_note}."

        selector_items.append(
            f'<button class="exp-chip{" active" if idx == 0 else ""}" data-target="exp-{idx}">'
            f'<strong>{ins["name"]}</strong><span>{dte_str}</span></button>'
        )

        oi_rows = [
            f'<div class="detail-row"><span>OI Call</span><strong>{fI(ins["coi"])} <em class="{css_class(ins["dcoi"])}">{fP(ins["dcoi"])}</em></strong></div>',
            f'<div class="detail-row"><span>OI Put</span><strong>{fI(ins["poi"])} <em class="{css_class(ins["dpoi"])}">{fP(ins["dpoi"])}</em></strong></div>',
        ]
        if ins['top_doi_c']:
            base = ins['top_doi_c'][0]
            oi_rows.append(
                f'<div class="detail-row"><span>{"Entrada OI Call" if base["dco"] >= 0 else "Saída OI Call"}</span>'
                f'<strong class="{css_class(base["dco"])}">K{base["s"]:.0f} · {fP(base["dco"])}</strong></div>'
            )
        if ins['top_doi_p']:
            base = ins['top_doi_p'][0]
            oi_rows.append(
                f'<div class="detail-row"><span>{"Entrada OI Put" if base["dpo"] >= 0 else "Saída OI Put"}</span>'
                f'<strong class="{css_class(base["dpo"])}">K{base["s"]:.0f} · {fP(base["dpo"])}</strong></div>'
            )

        vol_rows = [
            f'<div class="detail-row"><span>Volume total</span><strong>{fI(ins["vol_total"])} <em class="{css_class(ins["d_tvol"])}">{fP(ins["d_tvol"])}</em></strong></div>',
            f'<div class="detail-row"><span>Volume call</span><strong>{fI(ins["cvol"])} <em class="{css_class(ins["d_cvol"])}">{fP(ins["d_cvol"])}</em></strong></div>',
            f'<div class="detail-row"><span>Volume put</span><strong>{fI(ins["pvol"])} <em class="{css_class(ins["d_pvol"])}">{fP(ins["d_pvol"])}</em></strong></div>',
        ]
        if ins['top_vol']:
            vol_rows.append(
                f'<div class="detail-row"><span>Strike mais negociado</span><strong>K{ins["top_vol"][0]["s"]:.0f} · {fI(ins["top_vol"][0]["tv"])}</strong></div>'
            )

        iv_rows = [
            f'<div class="detail-row"><span>IV Call ATM</span><strong>{iv_format(ins["iv_c_atm"])} <em class="{css_class(ins["div_c_atm"])}">{change_format(ins["div_c_atm"])}</em></strong></div>',
            f'<div class="detail-row"><span>IV Put ATM</span><strong>{iv_format(ins["iv_p_atm"])} <em class="{css_class(ins["div_p_atm"])}">{change_format(ins["div_p_atm"])}</em></strong></div>',
        ]
        if ins['skew_atm'] is not None:
            iv_rows.append(
                f'<div class="detail-row"><span>Skew ATM</span><strong class="{css_class(ins["skew_atm"])}">{ins["skew_atm"]:+.2f}pp</strong></div>'
            )
        if ins['top_div_c']:
            base = ins['top_div_c'][0]
            if base['c_ivd'] is not None:
                iv_rows.append(
                    f'<div class="detail-row"><span>Maior variação IV Call</span><strong class="{css_class(base["c_ivd"])}">K{base["s"]:.0f} · {base["c_ivd"]:+.2f}pp</strong></div>'
                )

        price_rows = []
        if ins['calls_up']:
            base = ins['calls_up'][0]
            price_rows.append(f'<div class="detail-row"><span>Call em alta</span><strong class="pos">K{base["s"]:.0f} · {fPct(base["cp_pct"])}</strong></div>')
        if ins['calls_down']:
            base = ins['calls_down'][0]
            price_rows.append(f'<div class="detail-row"><span>Call em baixa</span><strong class="neg">K{base["s"]:.0f} · {fPct(base["cp_pct"])}</strong></div>')
        if ins['puts_up']:
            base = ins['puts_up'][0]
            price_rows.append(f'<div class="detail-row"><span>Put em alta</span><strong class="pos">K{base["s"]:.0f} · {fPct(base["pp_pct"])}</strong></div>')
        if ins['puts_down']:
            base = ins['puts_down'][0]
            price_rows.append(f'<div class="detail-row"><span>Put em baixa</span><strong class="neg">K{base["s"]:.0f} · {fPct(base["pp_pct"])}</strong></div>')
        if not price_rows:
            price_rows.append('<div class="detail-row"><span>Preço</span><strong>Sem destaque relevante</strong></div>')

        volume_focus_html = ""
        if ins['top_vol']:
            max_tv = max(ins['top_vol'][0]['tv'], 1)
            bars = []
            for tv in ins['top_vol'][:5]:
                width = round(tv['tv'] / max_tv * 100)
                bars.append(
                    f'<div class="bar-row"><span>K{tv["s"]:.0f}</span><div class="bar-track">'
                    f'<div class="bar-fill" style="width:{width}%;background:{color_main}"></div></div>'
                    f'<strong>{fI(tv["tv"])}</strong></div>'
                )
            volume_focus_html = (
                '<div class="detail-card detail-wide"><h4>Top volume por strike</h4>'
                f'<div class="bar-list">{"".join(bars)}</div></div>'
            )

        exp_sections.append(
            f'''
  <article class="exp-panel{" active" if idx == 0 else ""}" id="exp-{idx}">
    <div class="exp-head">
      <div>
        <div class="exp-eyebrow" style="color:{color_main}">{dte_str}</div>
        <h3>{ins["name"]}</h3>
        <p class="exp-headline">{headline}</p>
      </div>
      <div class="exp-meta">
        <div class="mini-stat"><span>GEX</span><strong class="{css_class(ins["gex_net"])}">{gf(ins["gex_net"])}</strong></div>
        <div class="mini-stat"><span>Gamma flip (modelo)</span><strong>{ins["flip"] if ins["flip"] is not None else ("Sem cruzamento" if ins["model_regime"] not in ("Indisponível", "Expirado") else "Indisponível")}</strong></div>
        <div class="mini-stat"><span>Max Pain</span><strong>K{number_format(ins["mp"])}</strong></div>
        <div class="mini-stat"><span>PCR OI</span><strong>{ins["pcr_oi"]}</strong></div>
      </div>
    </div>

    <div class="brief-grid">
      <div class="brief-card">
        <div class="brief-label">Regime</div>
        <div class="brief-value {css_class(ins["gex_net"])}">Estático: {ins["regime"]}</div>
        <div class="brief-detail">Modelado: {ins["model_regime"]} · Cobertura IV {ins["iv_coverage"]:.0%}</div>
        <div class="brief-detail">Dominante em K{number_format(ins["dom_s"])}</div>
      </div>
      <div class="brief-card">
        <div class="brief-label">IV Call ATM</div>
        <div class="brief-value {css_class(ins["div_c_atm"])}">{iv_format(ins["iv_c_atm"])}</div>
        <div class="brief-detail">Δ {change_format(ins["div_c_atm"])}</div>
      </div>
      <div class="brief-card">
        <div class="brief-label">OI líquido</div>
        <div class="brief-value {css_class(nullable_sum([ins["dcoi"], ins["dpoi"]]))}">{fP(nullable_sum([ins["dcoi"], ins["dpoi"]]))}</div>
        <div class="brief-detail">Call {fP(ins["dcoi"])} · Put {fP(ins["dpoi"])}</div>
      </div>
      <div class="brief-card">
        <div class="brief-label">Volume</div>
        <div class="brief-value {css_class(ins["d_tvol"])}">{fI(ins["vol_total"])}</div>
        <div class="brief-detail">Δ {fP(ins["d_tvol"])}</div>
      </div>
    </div>

    <details class="exp-details">
      <summary>Ver leitura completa</summary>
      <div class="details-grid">
        <div class="detail-card"><h4>Open interest</h4>{"".join(oi_rows)}</div>
        <div class="detail-card"><h4>Volume</h4>{"".join(vol_rows)}</div>
        <div class="detail-card"><h4>Volatilidade implícita</h4>{"".join(iv_rows)}</div>
        <div class="detail-card"><h4>Preço</h4>{"".join(price_rows)}</div>
        {volume_focus_html}
        <div class="detail-card detail-wide">
          <h4>Leitura do vencimento</h4>
          <p class="narrative">{ins["narrative"]}</p>
        </div>
      </div>
    </details>
  </article>'''
        )

    dom_exp = max(insights, key=lambda x: abs(x['gex_net']) if x['gex_net'] is not None else -1)
    dom_pct = round(abs(dom_exp['gex_net']) / abs(agg_gex_total) * 100) if agg_gex_total and dom_exp['gex_net'] is not None else 'N/A'
    key_levels = sorted({level for ins in insights for level in (ins['mp'], ins['dom_s']) if level is not None})
    levels_str = ", ".join(f"K{level:.0f}" for level in key_levels)

    changes = [i for i in insights if i['div_c_atm'] is not None]
    avg_div = sum(i['div_c_atm'] for i in changes) / len(changes) if changes else None
    max_name = max(changes, key=lambda x: abs(x['div_c_atm']))['name'] if changes else 'N/A'
    building = [i for i in insights if i['dcoi'] is not None and i['dpoi'] is not None and i['dcoi'] > 0 and i['dpoi'] > 0 and i['key'] != dom_exp['key']]

    conclusion_points = [
        f'Regime agregado em <b class="{regime_class}">{agg_regime}</b>, pelo sinal do GEX fornecido; gamma flip modelado {agg_flip if agg_flip is not None else aggregate_flip_status}.',
        f'IV call ATM média em {iv_format(avg_iv)} e variação média de {change_format(avg_div)}; o extremo do dia ficou em <b>{max_name}</b>.',
        f'Vencimento dominante: <b>{dom_exp["name"]}</b>, concentrando {dom_pct}% do GEX agregado.',
        f'Faixa de níveis a monitorar: <b>{levels_str}</b>.',
    ]
    if building:
        conclusion_points.insert(3, f'Construção simultânea de OI em <b>{", ".join(i["name"] for i in building)}</b>, sugerindo rolagem ou abertura nova.')

    conclusion_html = "".join(f"<li>{item}</li>" for item in conclusion_points)
    exp_html = "\n".join(exp_sections)
    selector_html = "".join(selector_items)

    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOVA11 — Insights __DATA_D1__ → __DATA_D__ __ANO__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
<script src="shared.js"></script>
<style>
:root{
  --bg:#f3f5f7; --surface:#ffffff; --surface-2:#f7f9fb; --surface-3:#eef3f8;
  --border:#dce2e8; --text:#111827; --muted:#5f6b7a; --soft:#7a8696;
  --blu:#2563eb; --grn:#15803d; --red:#c2413b; --yel:#b7791f; --pur:#8250df; --org:#bc4c00; --cyn:#0f7b9b;
  --font:'Instrument Sans',system-ui,sans-serif; --mono:'JetBrains Mono',monospace;
  --shadow:0 18px 45px rgba(17,24,39,.06);
}
[data-theme="dark"]{
  --bg:#0f141b; --surface:#141b23; --surface-2:#19212b; --surface-3:#1f2933;
  --border:#273240; --text:#e5ebf3; --muted:#a3afbf; --soft:#7d8a9b;
  --blu:#6ea8ff; --grn:#4ade80; --red:#f87171; --yel:#f6c768; --pur:#b388ff; --org:#ff8a65; --cyn:#67e8f9;
  --shadow:0 22px 50px rgba(0,0,0,.28);
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--font);-webkit-font-smoothing:antialiased}
button{font:inherit}
.page{max-width:1320px;margin:0 auto;padding:28px 24px 44px}
.page-header{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:18px}
.page-copy{max-width:72ch}
.page-kicker{font:.72rem var(--mono);letter-spacing:.1em;text-transform:uppercase;color:var(--soft);margin-bottom:10px}
.page-header h1{font-size:1.72rem;letter-spacing:-.05em;line-height:1.05}
.page-desc{margin-top:8px;color:var(--muted);line-height:1.55}
.theme-toggle{border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:999px;padding:9px 14px;cursor:pointer;transition:border-color .2s,color .2s}
.theme-toggle:hover{border-color:var(--blu);color:var(--blu)}
.theme-toggle.hidden{display:none}
.summary-grid{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin-bottom:14px}
.summary-card,.conclusion-box,.selector-wrap,.exp-panel{background:var(--surface);border:1px solid var(--border);border-radius:22px;box-shadow:var(--shadow)}
.summary-card{padding:14px 16px}
.sc-label{font:.68rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.sc-value{margin-top:8px;font-size:1.28rem;font-weight:700;letter-spacing:-.04em}
.sc-detail{margin-top:6px;font-size:.8rem;color:var(--muted);line-height:1.45}
.conclusion-box{padding:18px 20px;margin-bottom:14px}
.conclusion-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;margin-bottom:12px}
.conclusion-head h2{font-size:1.06rem;letter-spacing:-.03em}
.conclusion-head span{font:.72rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.conclusion-list{display:grid;gap:10px;padding-left:18px}
.conclusion-list li{color:var(--muted);line-height:1.55}
.selector-wrap{padding:16px 18px;margin-bottom:14px}
.selector-head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;margin-bottom:12px}
.selector-head h3{font-size:1.02rem;letter-spacing:-.03em}
.selector-head p{font-size:.82rem;color:var(--muted)}
.exp-selector{display:flex;gap:8px;flex-wrap:wrap}
.exp-chip{border:1px solid var(--border);background:var(--surface);color:var(--muted);border-radius:999px;padding:10px 14px;cursor:pointer;display:flex;flex-direction:column;gap:3px;transition:border-color .2s,background .2s,color .2s}
.exp-chip strong{font-size:.9rem;color:var(--text);font-weight:600}
.exp-chip span{font:.72rem var(--mono);color:var(--soft)}
.exp-chip.active{background:rgba(37,99,235,.08);border-color:rgba(37,99,235,.2)}
.exp-panel{display:none;padding:18px 20px}
.exp-panel.active{display:block}
.exp-head{display:flex;justify-content:space-between;gap:18px;align-items:flex-start;padding-bottom:16px;border-bottom:1px solid var(--border)}
.exp-eyebrow{font:.72rem var(--mono);letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px}
.exp-head h3{font-size:1.2rem;letter-spacing:-.04em}
.exp-headline{margin-top:6px;color:var(--muted);line-height:1.55;max-width:62ch}
.exp-meta{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;min-width:min(520px,100%)}
.mini-stat{padding:10px 12px;border:1px solid var(--border);border-radius:16px;background:var(--surface-2)}
.mini-stat span{display:block;font:.68rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.mini-stat strong{display:block;margin-top:6px;font-size:.95rem}
.brief-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:16px 0}
.brief-card{padding:14px;border:1px solid var(--border);border-radius:18px;background:var(--surface-2)}
.brief-label{font:.68rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft)}
.brief-value{margin-top:8px;font-size:1.08rem;font-weight:700;letter-spacing:-.03em}
.brief-detail{margin-top:6px;font-size:.8rem;color:var(--muted);line-height:1.45}
.exp-details{border-top:1px solid var(--border);padding-top:14px}
.exp-details summary{cursor:pointer;list-style:none;font-weight:600;color:var(--text)}
.exp-details summary::-webkit-details-marker{display:none}
.details-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}
.detail-card{padding:16px;border:1px solid var(--border);border-radius:18px;background:var(--surface-2)}
.detail-card.detail-wide{grid-column:1 / -1}
.detail-card h4{font:.78rem var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--soft);margin-bottom:10px}
.detail-row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;border-top:1px solid var(--border)}
.detail-row:first-child{padding-top:0;border-top:none}
.detail-row span{color:var(--muted);font-size:.82rem}
.detail-row strong{font:600 .85rem var(--mono)}
.detail-row em{font-style:normal;margin-left:6px}
.bar-list{display:grid;gap:8px}
.bar-row{display:grid;grid-template-columns:auto 1fr auto;gap:10px;align-items:center}
.bar-row span,.bar-row strong{font:.8rem var(--mono)}
.bar-track{height:10px;border-radius:999px;background:var(--surface-3);overflow:hidden}
.bar-fill{height:100%;border-radius:999px}
.narrative{color:var(--muted);line-height:1.65;font-size:.92rem}
.pos{color:var(--grn)} .neg{color:var(--red)} .neut{color:var(--muted)}
.disclaimer{text-align:center;color:var(--soft);font:.72rem var(--mono);margin-top:26px}
@media(max-width:1180px){.summary-grid{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:980px){.exp-meta,.brief-grid,.details-grid{grid-template-columns:1fr 1fr}}
@media(max-width:760px){
  .page{padding:20px 14px 32px}
  .page-header,.exp-head{flex-direction:column}
  .summary-grid,.exp-meta,.brief-grid,.details-grid{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="page">
  <header class="page-header">
    <div class="page-copy">
      <div class="page-kicker">Hoje / Insights</div>
      <h1>Insights analíticos</h1>
      <p class="page-desc">Comparativo entre __DATA_D1__ e __DATA_D__/__ANO__ · Spot __SPOT__ · leitura rápida do dia no topo e detalhe por vencimento sob demanda.</p>
    </div>
    <div><button id="theme-toggle" class="theme-toggle" onclick="toggleTheme()">Tema</button></div>
  </header>

  <section class="summary-grid">__SUMMARY_CARDS__</section>

  <section class="conclusion-box">
    <div class="conclusion-head">
      <div><span>Conclusão global</span><h2>O que merece atenção hoje</h2></div>
      <div class="page-kicker">Spot __SPOT__</div>
    </div>
    <ul class="conclusion-list">__CONCLUSION_LIST__</ul>
  </section>

  <section class="selector-wrap">
    <div class="selector-head">
      <div><h3>Por vencimento</h3><p>Selecione um vencimento para manter a leitura focada.</p></div>
    </div>
    <div class="exp-selector">__SELECTOR_HTML__</div>
  </section>

  __EXP_PANELS__

  <div class="disclaimer">Dados de fechamento B3 · Geração automática · Não constitui recomendação de investimento</div>
</div>

<script>
document.querySelectorAll('.exp-chip').forEach(chip => {
  chip.addEventListener('click', () => {
    const target = chip.dataset.target;
    document.querySelectorAll('.exp-chip').forEach(item => item.classList.remove('active'));
    document.querySelectorAll('.exp-panel').forEach(panel => panel.classList.remove('active'));
    chip.classList.add('active');
    const panel = document.getElementById(target);
    if (panel) panel.classList.add('active');
  });
});

</script>
</body>
</html>"""

    replacements = {
        "__DATA_D1__": DATA_D1,
        "__DATA_D__": DATA_D,
        "__ANO__": ANO,
        "__SPOT__": f"{spot:.2f}",
        "__SUMMARY_CARDS__": summary_cards_html,
        "__CONCLUSION_LIST__": conclusion_html,
        "__SELECTOR_HTML__": selector_html,
        "__EXP_PANELS__": exp_html,
    }
    for key, value in replacements.items():
        html = html.replace(key, str(value))

    out = os.path.join(OUTPUT_DIR, f"bova11_insights_{DATA_D1.replace('/', '')}_vs_{DATA_D.replace('/', '')}.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  ✅ HTML (Insights): {out}")


# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def main():
    print("=" * 65)
    print("  BOVA11 Insights — Versão Automatizada")
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

    def has_valid_volume(vol_map):
        if not vol_map:
            return False
        return any((v.get('cv', 0) + v.get('pv', 0)) > 0 for v in vol_map.values())

    fech_d, vol_d, fech_d1, vol_d1 = {}, {}, {}, {}
    valid_vencimentos = []
    for v in VENCIMENTOS:
        k = v["key"]
        fech_d[k]  = parse_fech_csv(v["fech_csv"])
        vol_d[k]   = parse_vol_csv(v["vol_csv"]) if v.get("vol_csv") else {}
        fech_d1[k] = parse_fech_csv(v["fech_csv_d1"]) if v.get("fech_csv_d1") else []
        vol_d1[k]  = parse_vol_csv(v["vol_csv_d1"]) if v.get("vol_csv_d1") else {}
        s1 = f"OK ({len(fech_d1[k])} strikes)" if fech_d1[k] else "N/A (snapshot)"
        vol_state = "OK" if has_valid_volume(vol_d[k]) else "sem volume válido"
        if fech_d[k]:
            valid_vencimentos.append(v)
            print(f"  ✓ {k}: {v['name']:20s} | D: {len(fech_d[k]):3d} | D-1: {s1} | Vol: {vol_state}")
        else:
            print(f"  ⚠ {k}: {v['name']:20s} | D: {len(fech_d[k]):3d} | D-1: {s1} | Vol: {vol_state} — removido")

    if not valid_vencimentos:
        print("\n  ERRO: nenhum vencimento com fechamento válido.")
        sys.exit(1)

    reference_iso = tag_to_iso(TAG_D, int(ANO))
    inventory = expiration_inventory(CSV_DIR, TAG_D, reference_iso)
    valid_names = {v['name'] for v in valid_vencimentos}
    missing_expiries = [e['label'] for e in inventory if e['exp_date'] and e['exp_date'] > reference_iso and e['label'] not in valid_names]
    VENCIMENTOS = [v for v in valid_vencimentos if (get_dte(v['name'], DATA_D) or 0) > 0]

    print(f"\n  Spot (fornecido): {SPOT:.2f}")

    rankings = {}
    for v in VENCIMENTOS:
        k = v['key']
        rankings[k] = build_exp_data(v, fech_d[k], fech_d1[k], vol_d[k], vol_d1[k], SPOT)

    # Todos os vencimentos usam a mesma grade de spot, sem corte de strikes.
    common_grid = build_spot_grid([row for v in VENCIMENTOS for row in fech_d[v['key']]], SPOT)
    gex_data = {v['key']: build_gex_data(fech_d[v['key']], v['name'], TAG_D, SPOT, common_grid)
                for v in VENCIMENTOS}
    totals = [g['net'] for g in gex_data.values()]
    gt_all = sum(totals) if not missing_expiries and all(value is not None for value in totals) else None
    agg_regime = gamma_regime(gt_all)
    agg_profile = aggregate_spot_profiles([g['profile'] for g in gex_data.values()])
    current_profile = min(agg_profile, key=lambda row: abs(row['spot'] - SPOT)) if agg_profile else {}
    model_complete = not missing_expiries and current_profile.get('iv_coverage', 0) >= 1
    aggregate_model_regime = gamma_regime(current_profile.get('gex_net')) if model_complete else 'Indisponível'
    roots = find_profile_roots(agg_profile) if model_complete else []
    agg_flip = min(roots, key=lambda value: abs(value - SPOT)) if roots else None
    print(f"  GEX estático agregado: {fP(gt_all)} | Flip modelado: {agg_flip} | Regime estático: {agg_regime}")

    # Generate insights
    insights = generate_insights(rankings, gex_data, SPOT, DATA_D)

    # Totals
    total_oi = nullable_sum(nullable_sum([r['coi'], r['poi']]) for r in rankings.values())
    total_vol = nullable_sum(nullable_sum([r['cvol'], r['pvol']]) for r in rankings.values())

    if missing_expiries:
        total_oi = total_vol = None
    # Generate HTML
    generate_html(insights, SPOT, gt_all, agg_flip, agg_regime, total_oi, total_vol, DATA_D1, DATA_D,
                  missing_expiries=missing_expiries, aggregate_flips=roots, aggregate_model_regime=aggregate_model_regime)
    print("\n  CONCLUÍDO ✅")


if __name__ == "__main__":
    main()
