#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 Historical Data
----------------------
Consolida métricas históricas diárias a partir dos CSVs de fechamento/volume
(agregado entre vencimentos), persiste em history/bova11_market_history.json
e gera um dashboard HTML estilo histórico com filtros de janela.
"""

from __future__ import annotations

from bova11_shared import parse_br_number, parse_b3_close_csv
from bova11_expiry_family import finite_number, deduplicate_option_rows, raw_exposures_by_strike, weighted_atm_iv, expiration_inventory, deduplicate_option_records, raw_exposure_records

import argparse
import glob
import json
import math
import os
import re
from datetime import datetime, timedelta, date
from typing import Dict, List, Optional, Tuple

from bova11_shared import (
    calc_dex_components,
    calc_gex_components,
    calc_max_pain,
    load_json,
    resolve_spot,
)
from bova11_expiry_family import matches_expiry_label


MESES_NUM = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}
MESES_LABEL = {
    1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
    7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez",
}


def _p(raw):
    return finite_number(parse_br_number(raw, none_on_blank=True))


def normalize_tag(tag: str) -> str:
    return re.sub(r"(pos|pre)([a-z]{3})$", r"\2", tag.lower())


def tag_sort_key(tag: str) -> Tuple[int, int, str]:
    t = normalize_tag(tag)
    m = re.match(r"(\d{1,2})([a-z]{3})$", t)
    if not m:
        return (99, 99, t)
    return (MESES_NUM.get(m.group(2), 99), int(m.group(1)), t)


def tag_to_iso(tag: str, year: int) -> str:
    t = normalize_tag(tag)
    m = re.match(r"(\d{1,2})([a-z]{3})$", t)
    if not m:
        return f"{year}-01-01"
    d = int(m.group(1))
    mo = MESES_NUM.get(m.group(2), 1)
    return f"{year}-{mo:02d}-{d:02d}"


def iso_to_display(iso_date: str) -> str:
    dt = datetime.strptime(iso_date, "%Y-%m-%d").date()
    return f"{dt.day}/{MESES_LABEL.get(dt.month, dt.month)}"


def is_primary_close(filename: str) -> bool:
    lower = filename.lower()
    return (
        lower.endswith(".csv")
        and "fechamento" in lower
        and "volume" not in lower
        and " copy" not in lower
    )


def extract_tag(filename: str) -> Optional[str]:
    m = re.search(r"fechamento__([a-zA-Z0-9]+)_\.csv$", filename)
    if m:
        return m.group(1)
    m = re.search(r"fechamento \(([a-zA-Z0-9]+)\)\.csv$", filename)
    if m:
        return m.group(1)
    return None


def extract_label(filename: str) -> str:
    m = re.match(r"venc_(.+?)_fechamento__", filename)
    if m:
        return m.group(1).replace("_", " ")
    m = re.match(r"venc (.+?) fechamento", filename)
    if m:
        return m.group(1)
    return ""


def discover_tags(data_dir: str) -> List[str]:
    tags = {}
    patterns = [
        os.path.join(data_dir, "venc * fechamento (*).csv"),
        os.path.join(data_dir, "venc_*_fechamento__*_.csv"),
    ]
    for pat in patterns:
        for fpath in glob.glob(pat):
            fn = os.path.basename(fpath)
            if not is_primary_close(fn):
                continue
            tag = extract_tag(fn)
            if not tag:
                continue
            n = normalize_tag(tag)
            if n not in tags or "pos" in tag.lower():
                tags[n] = tag
    return sorted(tags.values(), key=tag_sort_key)


def discover_expiries_for_tag(data_dir: str, tag: str) -> List[dict]:
    out = []
    target = normalize_tag(tag)
    patterns = [
        os.path.join(data_dir, "venc * fechamento (*).csv"),
        os.path.join(data_dir, "venc_*_fechamento__*_.csv"),
    ]
    seen = set()
    for pat in patterns:
        for close_path in glob.glob(pat):
            fn = os.path.basename(close_path)
            if not is_primary_close(fn):
                continue
            ftag = extract_tag(fn)
            if not ftag or normalize_tag(ftag) != target:
                continue
            label = extract_label(fn)
            if not label:
                continue
            key = (label.lower(), os.path.abspath(close_path))
            if key in seen:
                continue
            seen.add(key)

            vol_path = None
            if "fechamento (" in fn:
                vol_name = fn.replace(f"({ftag}).csv", f"({ftag} Volume).csv")
            else:
                vol_name = fn.replace(f"__{ftag}_.csv", f"__{ftag}_Volume_.csv")
            candidate = os.path.join(data_dir, vol_name)
            if os.path.exists(candidate):
                vol_path = candidate

            out.append({"label": label, "close": close_path, "volume": vol_path})
    return out


def parse_close_csv(path: str, label: str) -> List[dict]:
    rows = []
    for row in parse_b3_close_csv(path) if path else []:
        if not matches_expiry_label(row["call_ticker"], row["put_ticker"], label):
            continue
        if row["strike"] is None or row["strike"] <= 0:
            continue
        rows.append({key: row[key] for key in (
            "strike", "call_ticker", "put_ticker", "call_oi", "put_oi",
            "call_gamma", "put_gamma", "call_delta", "put_delta", "call_iv", "put_iv",
        )})
    return deduplicate_option_records(rows)


def parse_volume_csv(path: Optional[str], label: str) -> Dict[float, dict]:
    agg: Dict[float, dict] = {}
    if not path or not os.path.exists(path):
        return agg
    with open(path, "r", encoding="latin-1") as f:
        lines = f.readlines()
    if not lines:
        return agg

    header = lines[0].strip().replace("\r", "").split(";")
    strike_col = 5
    for i, h in enumerate(header):
        if "strike" in h.lower():
            strike_col = i
            break

    rows = []
    for line in lines[1:]:
        p = line.strip().replace("\r", "").split(";")
        if len(p) < 10 or not matches_expiry_label(p[0], p[10] if len(p) > 10 else "", label):
            continue
        strike = _p(p[strike_col])
        if strike is None or strike <= 0:
            continue
        rows.append({"strike":strike, "call_ticker":p[0], "put_ticker":p[10] if len(p) > 10 else "", "cv":_p(p[1]), "pv":_p(p[9])})
    for row in deduplicate_option_records(rows):
        aggregate = agg.setdefault(row["strike"], {"cv":0.0, "pv":0.0})
        for key in ("cv", "pv"):
            value = row[key]
            aggregate[key] = aggregate[key] + value if aggregate[key] is not None and value is not None else None
    return agg


def fetch_spot_close_map(iso_dates: List[str]) -> Dict[str, float]:
    if not iso_dates:
        return {}
    try:
        import yfinance as yf
    except Exception:
        return {}

    dts = sorted(datetime.strptime(d, "%Y-%m-%d").date() for d in iso_dates)
    start = dts[0] - timedelta(days=7)
    end = dts[-1] + timedelta(days=7)
    try:
        hist = yf.Ticker("BOVA11.SA").history(start=start.isoformat(), end=end.isoformat())
    except Exception:
        return {}
    if hist is None or len(hist) == 0:
        return {}

    by_day = {}
    for idx, row in hist.iterrows():
        day = idx.date().isoformat()
        try:
            by_day[day] = float(row["Close"])
        except Exception:
            pass

    out = {}
    for d in dts:
        key = d.isoformat()
        if key in by_day:
            out[key] = by_day[key]
            continue
        # fallback: último fechamento anterior disponível
        prev = [k for k in by_day.keys() if k <= key]
        if prev:
            out[key] = by_day[sorted(prev)[-1]]
    return out


def build_snapshot(
    data_dir: str,
    tag: str,
    year: int,
    spot_close_map: Dict[str, float],
    spot_history_file: str,
    stored_history: Dict[str, dict],
) -> Optional[dict]:
    iso_date = tag_to_iso(tag, year)
    expiries = expiration_inventory(data_dir, tag, iso_date)
    if not expiries:
        return None
    active = [exp for exp in expiries if not exp["exp_date"] or exp["exp_date"] > iso_date]
    expired = [exp["label"] for exp in expiries if exp["exp_date"] and exp["exp_date"] <= iso_date]
    rows = []
    volume_map = {}
    missing = []
    for exp in active:
        chain = parse_close_csv(exp["close"], exp["label"])
        if not chain:
            missing.append(exp["label"])
        rows.extend(chain)
        for strike, volume in parse_volume_csv(exp["volume"], exp["label"]).items():
            aggregate = volume_map.setdefault(strike, {"cv":0.0, "pv":0.0})
            for key in ("cv", "pv"):
                value = volume.get(key)
                aggregate[key] = aggregate[key] + value if aggregate[key] is not None and value is not None else None
    if not rows and not missing:
        return None
    stored_entry = stored_history.get(iso_date, {}) if isinstance(stored_history, dict) else {}
    spot_close, spot_source, spot_warning = resolve_spot(
        spot_history_file=spot_history_file, ref_date=iso_date, ref_tag=tag,
        stored_spot=stored_entry.get("spot_close") if isinstance(stored_entry, dict) else None,
        fetcher=lambda ref_iso: spot_close_map.get(ref_iso))

    def total(values):
        values = list(values)
        return sum(values) if values and all(finite_number(v) is not None and v >= 0 for v in values) else None

    calls = total(r["call_oi"] for r in rows)
    puts = total(r["put_oi"] for r in rows)
    total_oi = calls + puts if calls is not None and puts is not None else None
    partial_calls = sum(r["call_oi"] for r in rows if finite_number(r["call_oi"]) is not None and r["call_oi"] >= 0)
    partial_puts = sum(r["put_oi"] for r in rows if finite_number(r["put_oi"]) is not None and r["put_oi"] >= 0)
    iv_atm = mp = gex_net = dex_net = None
    exposure_metadata = {}
    if rows and spot_close is not None and spot_close > 0:
        exposures, exposure_metadata = raw_exposure_records(rows, spot_close, include_delta=True)
        for column in ("net_gex", "net_dex"):
            values = [row[column] for row in exposures]
            result = sum(values) if all(value is not None for value in values) and not missing else None
            if column == "net_gex":
                gex_net = result
            else:
                dex_net = result
        nearest = min((r["strike"] for r in rows), key=lambda strike: abs(strike - spot_close))
        value = weighted_atm_iv([r for r in rows if r["strike"] == nearest])
        iv_atm = value / 100 if value is not None else None
    strike_map = {}
    if calls is not None and puts is not None and not missing:
        for r in rows:
            strike = strike_map.setdefault(r["strike"], {"call_oi":0.0, "put_oi":0.0})
            strike["call_oi"] += r["call_oi"]
            strike["put_oi"] += r["put_oi"]
        _, mp, _ = calc_max_pain(strike_map)
    if missing:
        calls = puts = total_oi = iv_atm = None
        exposure_metadata.update(gamma_coverage=0.0, delta_coverage=0.0)
    exposure_metadata["expiry_coverage"] = (len(active) - len(missing)) / len(active) if active else 0.0
    call_vol = total(v["cv"] for v in volume_map.values())
    put_vol = total(v["pv"] for v in volume_map.values())
    complete_volume = all(exp.get("volume") for exp in active) and not missing
    if not complete_volume:
        call_vol = put_vol = None
    def rounded(value, digits=0):
        return round(value, digits) if value is not None else None
    warnings = ([spot_warning] if spot_warning else []) + (["Vencimentos ativos sem fechamento válido: " + ", ".join(missing)] if missing else [])
    return {
        "date":iso_date, "tag":tag, "display":iso_to_display(iso_date),
        "spot_close":rounded(spot_close, 2), "spot_source":spot_source,
        "oi_call":rounded(calls), "oi_put":rounded(puts), "oi_total":rounded(calls) + rounded(puts) if total_oi is not None else None,
        "oi_pcr":rounded(puts / calls, 2) if calls is not None and calls > 0 and puts is not None else None,
        "partial_oi_call":rounded(partial_calls), "partial_oi_put":rounded(partial_puts),
        "vol_call":rounded(call_vol), "vol_put":rounded(put_vol),
        "vol_total":rounded(call_vol) + rounded(put_vol) if call_vol is not None and put_vol is not None else None,
        "vol_pcr":rounded(put_vol / call_vol, 2) if call_vol is not None and call_vol > 0 and put_vol is not None else None,
        "iv_atm":rounded(iv_atm, 4), "iv_rank":None, "iv_percentile":None,
        "max_pain":rounded(mp, 2), "gex_net":rounded(gex_net), "dex_net":rounded(dex_net),
        **exposure_metadata, "missing_expiries":missing, "expired_expiries":expired, "warnings":warnings,
    }


def enrich_iv_metrics(series: List[dict]) -> None:
    """IV anual comparável; percentil usa somente observações anteriores."""
    past = []
    for item in sorted(series, key=lambda row: row['date']):
        item['iv_rank'] = item['iv_percentile'] = None
        value = item.get('iv_atm')
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            continue
        date = datetime.fromisoformat(item['date']).date()
        previous = [iv for prior_date, iv in past if 0 < (date - prior_date).days <= 365]
        if len(previous) >= 20:
            lo, hi = min(previous + [value]), max(previous + [value])
            if hi > lo:
                item['iv_rank'] = round((value - lo) / (hi - lo) * 100, 2)
                item['iv_percentile'] = round(sum(iv < value for iv in previous) / len(previous) * 100, 2)
        past.append((date, value))


def build_html(data_series: List[dict], ref_date: str) -> str:
    payload = json.dumps(data_series, ensure_ascii=False)
    latest = data_series[-1] if data_series else None
    latest_date = latest["display"] if latest else "N/A"
    latest_spot = f"R$ {latest['spot_close']:.2f}" if latest and latest.get("spot_close") is not None else "N/A"

    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>BOVA11 — Histórico de Mercado</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="shared.js"></script>
  <style>
    :root {
      --bg:#f3f5f7; --surface:#ffffff; --surface2:#f7f9fb; --border:#dce2e8;
      --text:#111827; --muted:#5f6b7a; --soft:#7a8696;
      --blue:#2563eb; --green:#148a63; --red:#b33530; --amber:#b8720a; --purple:#8250df;
      --font:'Instrument Sans',system-ui,sans-serif; --mono:'JetBrains Mono',monospace;
    }
    [data-theme="dark"] {
      --bg:#0f141b; --surface:#141b23; --surface2:#19212b; --border:#273240;
      --text:#e5ebf3; --muted:#a3afbf; --soft:#7d8a9b;
      --blue:#6ea8ff; --green:#3fb950; --red:#f87171; --amber:#f6c768; --purple:#b388ff;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{font-family:var(--font);background:var(--bg);color:var(--text);padding:22px}
    #theme-toggle{position:fixed;top:16px;right:16px;z-index:9;border:1px solid var(--border);background:var(--surface);color:var(--text);border-radius:10px;padding:8px 10px;cursor:pointer}
    .page{max-width:1280px;margin:0 auto}
    .head{display:flex;justify-content:space-between;gap:12px;align-items:flex-end;margin-bottom:16px}
    .head h1{font-size:1.75rem;letter-spacing:-.03em}
    .sub{color:var(--muted);font-size:.92rem;line-height:1.4}
    .toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}
    .btn{border:1px solid var(--border);background:var(--surface);color:var(--muted);padding:8px 12px;border-radius:10px;cursor:pointer;font-weight:600}
    .btn.active{border-color:var(--blue);color:var(--blue);background:color-mix(in srgb, var(--blue) 10%, transparent)}
    .grid{display:grid;gap:12px;grid-template-columns:repeat(2,minmax(0,1fr));margin-bottom:14px}
    .card{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:14px}
    .card h3{font-size:.86rem;color:var(--soft);text-transform:uppercase;letter-spacing:.06em;margin-bottom:8px}
    .card .v{font:700 1.3rem var(--mono)}
    .chart{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:12px}
    .chart + .chart{margin-top:12px}
    .chart h2{font-size:.95rem;margin-bottom:8px}
    canvas{max-height:260px}
    .table-wrap{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:12px;margin-top:12px;overflow:auto}
    table{width:100%;border-collapse:collapse;min-width:980px}
    th,td{padding:8px 9px;border-bottom:1px solid var(--border);font-size:.82rem;text-align:right}
    th{position:sticky;top:0;background:var(--surface2);color:var(--soft);font:.72rem var(--mono);text-transform:uppercase;letter-spacing:.06em}
    th:first-child,td:first-child{text-align:left}
    @media(max-width:900px){.grid{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <button id="theme-toggle" onclick="toggleTheme()">Tema</button>
  <div class="page">
    <div class="head">
      <div>
        <h1>Histórico de Mercado</h1>
        <div class="sub">Base diária consolidada de OI, volume, IV, Max Pain, GEX e DEX. Referência atual: __LATEST_DATE__ · Spot __LATEST_SPOT__.</div>
      </div>
      <div class="sub mono">Ref date: __REF_DATE__</div>
    </div>
    <div class="toolbar" id="range-buttons">
      <button class="btn" data-range="5D">5D</button>
      <button class="btn active" data-range="1M">1M</button>
      <button class="btn" data-range="3M">3M</button>
      <button class="btn" data-range="YTD">YTD</button>
      <button class="btn" data-range="MAX">Max</button>
    </div>
    <div class="grid" id="cards"></div>
    <p id="coverage-status" role="status"></p>
    <div class="chart"><h2>Open Interest</h2><canvas id="c1"></canvas></div>
    <div class="chart"><h2>Option Volume</h2><canvas id="c2"></canvas></div>
    <div class="chart"><h2>IV ATM & IV Rank</h2><canvas id="c3"></canvas></div>
    <div class="chart"><h2>Max Pain, Spot, GEX e DEX</h2><canvas id="c4"></canvas></div>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Date</th><th>Close</th><th>Vol Total</th><th>Vol PCR</th><th>OI Total</th><th>OI PCR</th>
            <th>IV ATM</th><th>IV Rank</th><th>Max Pain</th><th>GEX Net</th><th>DEX Net</th><th>Cobertura e avisos</th>
          </tr>
        </thead>
        <tbody id="rows"></tbody>
      </table>
    </div>
  </div>
<script>
const FULL = __PAYLOAD__;
let charts = [];
function hasNum(v){ return typeof v === 'number' && Number.isFinite(v); }
function fmtInt(n){ return hasNum(n) ? Math.round(n).toLocaleString('pt-BR') : 'N/A'; }
function fmtPrice(v){ return hasNum(v) ? `R$ ${v.toFixed(2)}` : 'N/A'; }
function fmtRatio(v){ return hasNum(v) ? v.toFixed(2) : 'N/A'; }
function fmtPct(v){ return hasNum(v) ? `${v.toFixed(2)}%` : 'N/A'; }
function fmtScaledPct(v, scale){ return hasNum(v) ? `${(v*scale).toFixed(2)}%` : 'N/A'; }
function fmtB(v){
  if (!hasNum(v)) return 'N/A';
  const abs = Math.abs(v);
  if (abs >= 1e9) return (v/1e9).toFixed(2)+'B';
  if (abs >= 1e6) return (v/1e6).toFixed(1)+'M';
  return Math.round(v).toLocaleString('pt-BR');
}
function cutByRange(arr, range){
  if (!arr.length) return arr;
  if (range === 'MAX') return arr;
  if (range === '5D') return arr.slice(-5);
  if (range === '1M') return arr.slice(-21);
  if (range === '3M') return arr.slice(-63);
  if (range === 'YTD') {
    const year = arr[arr.length-1].date.slice(0,4);
    return arr.filter(x => x.date.startsWith(year));
  }
  return arr;
}
function coverageText(row){
  if (!row) return 'N/A';
  const coverage = [['Vencimentos',row.expiry_coverage], ['Gamma',row.gamma_coverage], ['Delta',row.delta_coverage]]
    .map(([name,value]) => name + ': ' + (hasNum(value) ? (100*value).toFixed(1)+'%' : 'N/A'));
  return coverage.concat(row.warnings || []).join(' · ');
}
function destroyCharts(){ charts.forEach(c=>c.destroy()); charts=[]; }
function render(range){
  const data = cutByRange(FULL, range);
  const labels = data.map(x=>x.display);
  const latest = data[data.length-1] || null;
  const cards = document.getElementById('cards');
  cards.innerHTML = latest ? `
    <div class="card"><h3>Data</h3><div class="v">${latest.display}</div></div>
    <div class="card"><h3>Spot</h3><div class="v">${fmtPrice(latest.spot_close)}</div></div>
    <div class="card"><h3>OI Put/Call</h3><div class="v">${fmtRatio(latest.oi_pcr)}</div></div>
    <div class="card"><h3>Vol Put/Call</h3><div class="v">${fmtRatio(latest.vol_pcr)}</div></div>
    <div class="card"><h3>IV ATM</h3><div class="v">${fmtScaledPct(latest.iv_atm, 100)}</div></div>
    <div class="card"><h3>Max Pain</h3><div class="v">${fmtPrice(latest.max_pain)}</div></div>` : '';
  document.getElementById('coverage-status').textContent = coverageText(latest);
  document.querySelectorAll('#range-buttons .btn').forEach(btn => btn.classList.toggle('active', btn.dataset.range === range));
  destroyCharts();
  const common = {responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false}};
  charts.push(new Chart(document.getElementById('c1'), {
    type:'line', data:{labels, datasets:[
      {label:'OI Put-Call Ratio', data:data.map(x=>x.oi_pcr), borderColor:'#2E6BBF', yAxisID:'y'},
      {label:'Call OI', data:data.map(x=>x.oi_call), borderColor:'#148A63', yAxisID:'y1'},
      {label:'Put OI', data:data.map(x=>x.oi_put), borderColor:'#B33530', yAxisID:'y1'},
    ]}, options:{...common, scales:{ y:{position:'left'}, y1:{position:'right', grid:{drawOnChartArea:false}} }}
  }));
  charts.push(new Chart(document.getElementById('c2'), {
    type:'line', data:{labels, datasets:[
      {label:'Volume Put-Call Ratio', data:data.map(x=>x.vol_pcr), borderColor:'#2563eb', yAxisID:'y'},
      {label:'Call Volume', data:data.map(x=>x.vol_call), borderColor:'#15803d', yAxisID:'y1'},
      {label:'Put Volume', data:data.map(x=>x.vol_put), borderColor:'#c2413b', yAxisID:'y1'},
    ]}, options:{...common, scales:{ y:{position:'left'}, y1:{position:'right', grid:{drawOnChartArea:false}} }}
  }));
  charts.push(new Chart(document.getElementById('c3'), {
    type:'line', data:{labels, datasets:[
      {label:'IV ATM', data:data.map(x=>hasNum(x.iv_atm) ? x.iv_atm*100 : null), borderColor:'#d946ef', yAxisID:'y'},
      {label:'IV Rank', data:data.map(x=>x.iv_rank), borderColor:'#7c3aed', yAxisID:'y1'},
    ]}, options:{...common, scales:{ y:{position:'left'}, y1:{position:'right', min:0, max:100, grid:{drawOnChartArea:false}} }}
  }));
  charts.push(new Chart(document.getElementById('c4'), {
    data:{labels, datasets:[
      {type:'line', label:'Spot', data:data.map(x=>x.spot_close), borderColor:'#111827', yAxisID:'price'},
      {type:'line', label:'Max Pain', data:data.map(x=>x.max_pain), borderColor:'#b8720a', yAxisID:'price'},
      {type:'bar', label:'GEX Net', data:data.map(x=>x.gex_net), backgroundColor:'rgba(37,99,235,.35)', yAxisID:'flow'},
      {type:'bar', label:'DEX Net', data:data.map(x=>x.dex_net), backgroundColor:'rgba(148,163,184,.35)', yAxisID:'flow'},
    ]}, options:{...common, scales:{ price:{position:'left'}, flow:{position:'right', grid:{drawOnChartArea:false}} }}
  }));
  const rows = document.getElementById('rows');
  rows.innerHTML = data.slice().reverse().map(x => `
    <tr><td>${x.date}</td><td>${fmtPrice(x.spot_close)}</td><td>${fmtInt(x.vol_total)}</td><td>${fmtRatio(x.vol_pcr)}</td>
    <td>${fmtInt(x.oi_total)}</td><td>${fmtRatio(x.oi_pcr)}</td><td>${fmtScaledPct(x.iv_atm, 100)}</td><td>${fmtPct(x.iv_rank)}</td>
    <td>${hasNum(x.max_pain) ? x.max_pain.toFixed(2) : 'N/A'}</td><td>${fmtB(x.gex_net)}</td><td>${fmtB(x.dex_net)}</td><td>${coverageText(x)}</td></tr>`).join('');
}
document.querySelectorAll('#range-buttons .btn').forEach(btn => btn.addEventListener('click', () => render(btn.dataset.range)));
render('1M');
</script>
</body>
</html>"""
    return (
        html.replace("__PAYLOAD__", payload)
        .replace("__LATEST_DATE__", latest_date)
        .replace("__LATEST_SPOT__", latest_spot)
        .replace("__REF_DATE__", ref_date)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="BOVA11 Historical Data")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref-date", required=True)
    parser.add_argument("--ref-tag", required=True)
    parser.add_argument("--history-file", default=os.path.join(os.path.dirname(__file__), "..", "history", "bova11_market_history.json"))
    parser.add_argument("--spot-history-file", default=os.path.join(os.path.dirname(__file__), "..", "history", "bova11_spot_history.json"))
    args = parser.parse_args()

    year = datetime.now().year
    tags = discover_tags(args.data_dir)
    if not tags:
        raise SystemExit("Nenhuma tag válida encontrada em data-dir")

    iso_dates = [tag_to_iso(t, year) for t in tags]
    spot_map = fetch_spot_close_map(iso_dates)

    hist = load_json(args.history_file, {})
    if not isinstance(hist, dict):
        hist = {}

    computed = []
    spot_warnings = []
    for t in tags:
        snap = build_snapshot(
            args.data_dir,
            t,
            year,
            spot_map,
            args.spot_history_file,
            hist,
        )
        if snap:
            computed.append(snap)
            if snap.get("warnings"):
                spot_warnings.extend(snap["warnings"])

    computed.sort(key=lambda x: x["date"])
    enrich_iv_metrics(computed)

    for s in computed:
        hist[s["date"]] = {**hist.get(s["date"], {}), **s}

    ordered = [hist[k] for k in sorted(hist.keys())]
    enrich_iv_metrics(ordered)

    os.makedirs(os.path.dirname(args.history_file), exist_ok=True)
    with open(args.history_file, "w", encoding="utf-8") as f:
        json.dump({item["date"]: item for item in ordered}, f, ensure_ascii=False, indent=2)

    html = build_html(ordered, args.ref_date)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✅ Histórico consolidado: {args.output}")
    print(f"✅ Persistido em: {args.history_file}")
    if spot_warnings:
        print(f"⚠️ {len(spot_warnings)} aviso(s) de spot durante a consolidação histórica")


if __name__ == "__main__":
    main()
