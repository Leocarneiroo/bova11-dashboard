#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 Market Gamma
-------------------
Gera a curva de gamma líquida por strike no dia de referência,
com visão agregada e por vencimento.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from bova11_shared import calc_gex_components, resolve_spot, find_profile_roots, expiry_label_to_iso_nearest, tag_to_iso_nearest, parse_b3_close_csv
import bova11_tex_dex_vex as gamma_model
from bova11_expiry_family import matches_expiry_label


MESES_NUM = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}


def _p(raw: str) -> float:
    s = str(raw).strip().replace("%", "")
    if s in ("", "-", "--", "None"):
        return 0.0
    mult = 1.0
    if s.endswith("k"):
        s = s[:-1]
        mult = 1_000.0
    elif s.endswith("M"):
        s = s[:-1]
        mult = 1_000_000.0
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s) * mult
    except Exception:
        return 0.0


def normalize_tag(tag: str) -> str:
    return re.sub(r"(pos|pre)([a-z]{3})$", r"\2", tag.lower())


def tag_to_iso(tag: str, year: int) -> str:
    t = normalize_tag(tag)
    m = re.match(r"(\d{1,2})([a-z]{3})$", t)
    if not m:
        return f"{year}-01-01"
    return f"{year}-{MESES_NUM.get(m.group(2), 1):02d}-{int(m.group(1)):02d}"


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


def discover_expiries_for_tag(data_dir: str, ref_tag: str) -> List[dict]:
    out = []
    target = normalize_tag(ref_tag)
    patterns = [
        os.path.join(data_dir, "venc * fechamento (*).csv"),
        os.path.join(data_dir, "venc_*_fechamento__*_.csv"),
    ]
    seen = set()
    for pat in patterns:
        for path in glob.glob(pat):
            fn = os.path.basename(path)
            if not is_primary_close(fn):
                continue
            tag = extract_tag(fn)
            if not tag or normalize_tag(tag) != target:
                continue
            label = extract_label(fn)
            if not label:
                continue
            key = (label.lower(), os.path.abspath(path))
            if key in seen:
                continue
            seen.add(key)
            out.append({"label": label, "path": path})
    out.sort(key=lambda x: x["label"].lower())
    return out


def missing_previous_expiries(data_dir, tag):
    reference = tag_to_iso(tag, datetime.now().year)
    tags = {extract_tag(os.path.basename(path)) for path in glob.glob(os.path.join(data_dir, "*.csv"))
            if is_primary_close(os.path.basename(path))}
    resolved = {value: tag_to_iso_nearest(value, reference) for value in tags if value}
    previous = [value for value, iso in resolved.items() if iso and reference and iso < reference]
    previous_tag = max(previous, key=lambda value: tag_to_iso_nearest(value, reference)) if previous else None
    current = {entry["label"] for entry in discover_expiries_for_tag(data_dir, tag)}
    prior = {entry["label"] for entry in discover_expiries_for_tag(data_dir, previous_tag)} if previous_tag else set()
    missing = [label for label in sorted(prior - current)
               if (expiry_label_to_iso_nearest(label, reference) or reference) > reference]
    return {"previous_tag": previous_tag, "missing_expiries": missing}


def greek_is_valid(raw, greek="gamma"):
    value = gamma_model.parse_num(raw)
    return value is not None and math.isfinite(value) and (value >= 0 if greek in ("gamma", "vega") else True)


def summarize_gamma_profiles(expiries, tag, spot):
    """Reprice every expiry on the same spot grid before finding aggregate roots."""
    rows = [row for _, chain in expiries for row in chain]
    grid = gamma_model.build_spot_grid(rows, spot)
    summaries = [gamma_model.gamma_profile_summary(chain, label, tag, spot, spot_range=grid)
                 for label, chain in expiries]
    active = [summary for summary in summaries if summary.get("regime") != "Expirado"]
    empty = [label for (label, _), summary in zip(expiries, summaries)
             if summary.get("regime") != "Expirado" and summary.get("oi_total") == 0]
    oi_known = all(summary.get("oi_total") is not None for summary in active)
    total_oi = sum(summary.get("oi_total", 0) for summary in active) if oi_known else None
    valid_oi = sum(summary.get("oi_with_iv", 0) for summary in active)
    coverage = valid_oi / total_oi if total_oi else 0.0
    profile = gamma_model.aggregate_spot_profiles([summary["profile"] for summary in active])
    roots = find_profile_roots(profile) if coverage == 1.0 and not empty else []
    current = min(profile, key=lambda row: abs(row["spot"] - spot)) if profile else {}
    value = current.get("gex_net")
    regime = ("Long Gamma" if value > 0 else "Short Gamma" if value < 0 else "Neutro") if coverage == 1 and value is not None and not empty else "Indisponível"
    return {"profile": profile, "flip": min(roots, key=lambda value: abs(value - spot)) if roots else None,
            "flips": roots, "iv_coverage": coverage, "regime": regime,
            "empty_expiries": empty,
            "expired_expiries": [label for (label, _), summary in zip(expiries, summaries)
                                 if summary.get("regime") == "Expirado"]}


def parse_gamma_curve(path: str, label: str, spot: float) -> Dict[float, dict]:
    curve: Dict[float, dict] = {}
    for row in parse_b3_close_csv(path):
        if not matches_expiry_label(row["call_ticker"], row["put_ticker"], label):
            continue
        strike = row["strike"]
        c_oi, p_oi = row["call_oi"], row["put_oi"]
        if strike is None or strike <= 0:
            continue
        c_gamma = row["call_gamma"] if greek_is_valid(row["call_gamma"]) else 0.0
        p_gamma = row["put_gamma"] if greek_is_valid(row["put_gamma"]) else 0.0

        call_g, put_g, net_g = calc_gex_components(
            call_gamma=c_gamma,
            put_gamma=p_gamma,
            call_oi=c_oi,
            put_oi=p_oi,
            spot=spot,
        )

        if strike not in curve:
            curve[strike] = {"call_available": 0.0, "put_available": 0.0, "net_available": 0.0, "oi_total": 0.0, "oi_with_gamma": 0.0, "call_missing": False, "put_missing": False}
        values = curve[strike]
        values["call_available"] += call_g
        values["put_available"] += put_g
        values["net_available"] += net_g
        values["call_missing"] |= c_oi is None or (c_oi > 0 and not greek_is_valid(row["call_gamma"]))
        values["put_missing"] |= p_oi is None or (p_oi > 0 and not greek_is_valid(row["put_gamma"]))
        values["call"] = None if values["call_missing"] else values["call_available"]
        values["put"] = None if values["put_missing"] else values["put_available"]
        values["net"] = None if values["call_missing"] or values["put_missing"] else values["net_available"]
        values["oi_total"] = (values["oi_total"] + c_oi + p_oi
                              if values["oi_total"] is not None and c_oi is not None and p_oi is not None else None)
        curve[strike]["oi_with_gamma"] += ((c_oi or 0) if greek_is_valid(row["call_gamma"]) else 0) + ((p_oi or 0) if greek_is_valid(row["put_gamma"]) else 0)
    return curve


def find_flip(strikes: List[float], nets: List[float]) -> Optional[float]:
    roots = find_profile_roots([{"spot": strike, "gex_net": net} for strike, net in zip(strikes, nets)])
    return roots[0] if roots else None


def fetch_spot_close(ref_iso: str) -> Optional[float]:
    try:
        import yfinance as yf
    except Exception:
        return None
    d = datetime.strptime(ref_iso, "%Y-%m-%d").date()
    start = (d - timedelta(days=7)).isoformat()
    end = (d + timedelta(days=7)).isoformat()
    try:
        hist = yf.Ticker("BOVA11.SA").history(start=start, end=end)
    except Exception:
        return None
    if hist is None or len(hist) == 0:
        return None
    by_day = {}
    for idx, row in hist.iterrows():
        try:
            by_day[idx.date().isoformat()] = float(row["Close"])
        except Exception:
            pass
    if ref_iso in by_day:
        return by_day[ref_iso]
    prev = [k for k in by_day.keys() if k <= ref_iso]
    if prev:
        return by_day[sorted(prev)[-1]]
    return None


def to_curve_payload(curve: Dict[float, dict], spot: float, model=None, strike_min=None, strike_max=None) -> dict:
    strikes = sorted(curve.keys())
    calls = [curve[s]["call"] for s in strikes]
    puts = [curve[s]["put"] for s in strikes]
    nets = [curve[s]["net"] for s in strikes]

    if not strikes:
        return {
            "strikes": [], "call": [], "put": [], "net": [],
            "spot": spot, "flip": None, "gamma_atual": None,
            "gamma_min_neg": None, "gamma_max_pos": None, "gamma_score": None,
            "gamma_coverage": 0.0, "iv_coverage": 0.0, "regime": None, "spot_profile": [],
        }

    gamma_atual = sum(nets) if all(value is not None for value in nets) else None
    gamma_available = sum(curve[strike].get("net_available", curve[strike]["net"] or 0) for strike in strikes)
    oi_known = all(curve[s].get("oi_total", 0) is not None for s in strikes)
    oi_total = sum(curve[s].get("oi_total", 0) for s in strikes) if oi_known else None
    oi_valid = sum(curve[s].get("oi_with_gamma", 0) for s in strikes)
    gamma_coverage = (oi_valid / oi_total if oi_total else 1.0) if oi_known else 0.0
    if gamma_coverage < 1:
        gamma_atual = None
    max_abs = max((abs(v) for v in nets if v is not None), default=1.0)
    gamma_score = (gamma_atual / max_abs if max_abs > 0 else 0.0) if gamma_atual is not None else None

    min_neg = None
    neg_points = [(s, v) for s, v in zip(strikes, nets) if v is not None and v < 0]
    if neg_points:
        min_neg = min(neg_points, key=lambda x: x[1])

    max_pos = None
    pos_points = [(s, v) for s, v in zip(strikes, nets) if v is not None and v > 0]
    if pos_points:
        max_pos = max(pos_points, key=lambda x: x[1])

    visible = [i for i, strike in enumerate(strikes)
               if (strike_min is None or strike >= strike_min) and (strike_max is None or strike <= strike_max)]
    return {
        "strikes": [strikes[i] for i in visible],
        "call": [calls[i] for i in visible],
        "put": [puts[i] for i in visible],
        "net": [nets[i] for i in visible],
        "spot": spot,
        "flip": model["flip"] if model else None,
        "spot_profile": model["profile"] if model else [],
        "iv_coverage": model["iv_coverage"] if model else None,
        "gamma_coverage": gamma_coverage,
        "regime": ("Long Gamma" if gamma_atual > 0 else "Short Gamma" if gamma_atual < 0 else "Neutro") if gamma_coverage == 1 and gamma_atual is not None else None,
        "gamma_atual": gamma_atual,
        "gamma_available": gamma_available,
        "gamma_min_neg": {"strike": min_neg[0], "value": min_neg[1]} if min_neg else None,
        "gamma_max_pos": {"strike": max_pos[0], "value": max_pos[1]} if max_pos else None,
        "gamma_score": gamma_score,
    }


def build_html(payload: dict) -> str:
    js = json.dumps(payload, ensure_ascii=False)
    html = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Market Gamma</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <script src="shared.js"></script>
  <style>
    :root {
      --bg:#f4f6fb; --surface:#ffffff; --surface2:#eef2fa; --text:#111827; --muted:#5b6577;
      --line:#d4ddeb; --blue:#2563eb; --green:#15803d; --red:#b91c1c; --gold:#b45309;
      --font:'Instrument Sans',system-ui,sans-serif; --mono:'JetBrains Mono',monospace;
    }
    [data-theme="dark"] {
      --bg:#0f172a; --surface:#111c31; --surface2:#1a2a45; --text:#e7edf7; --muted:#9fb0c8;
      --line:#314a70; --blue:#2f81f7; --green:#22c55e; --red:#ef4444; --gold:#facc15;
    }
    *{box-sizing:border-box;margin:0;padding:0}
    body{background:var(--bg);color:var(--text);font-family:var(--font);padding:20px}
    #theme-toggle{position:fixed;top:12px;right:12px;border:1px solid var(--line);background:var(--surface);color:var(--text);padding:8px 10px;border-radius:10px;cursor:pointer}
    .page{max-width:1360px;margin:0 auto}
    h1{text-align:center;font-size:3rem;letter-spacing:-.04em;margin:10px 0 14px}
    .bar{display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap;margin-bottom:14px}
    .sel{border:1px solid var(--line);background:var(--surface);color:var(--text);padding:10px 12px;border-radius:10px;font-weight:600}
    .chart-wrap{background:var(--surface);border:1px solid var(--line);border-radius:16px;padding:14px}
    canvas{max-height:480px}
    .cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:10px;margin-top:14px}
    .card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:12px}
    .card .k{font:.72rem var(--mono);color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
    .card .v{margin-top:6px;font:700 1.4rem var(--mono)}
    @media(max-width:1100px){.cards{grid-template-columns:1fr 1fr}}
    @media(max-width:680px){h1{font-size:2.2rem}.cards{grid-template-columns:1fr}}
  </style>
</head>
<body>
  <button id="theme-toggle" onclick="toggleTheme()">Tema</button>
  <div class="page">
    <h1>Market Gamma</h1>
    <div class="bar">
      <select id="mode" class="sel"></select>
      <div id="meta" class="sel"></div>
    </div>
    <p style="color:var(--muted);margin-bottom:10px">GEX por strike no spot atual; flip calculado na curva agregada por spot simulado. Convenção estimada de dealers: calls + / puts −. Unidade: R$ por movimento de 1%.</p>
    <div class="chart-wrap">
      <canvas id="gamma"></canvas>
    </div>
    <div class="cards">
      <div class="card"><div class="k">GEX agregado / maior |GEX strike|</div><div class="v" id="c-score">-</div></div>
      <div class="card"><div class="k">GEX Total no Spot · R$/1%</div><div class="v" id="c-atual">-</div></div>
      <div class="card"><div class="k">Gamma Mínimo Negativo</div><div class="v" id="c-min">-</div></div>
      <div class="card"><div class="k">Flip</div><div class="v" id="c-flip">-</div></div>
      <div class="card"><div class="k">Gamma Máximo Positivo</div><div class="v" id="c-max">-</div></div>
    </div>
  </div>

<script>
const DATA = __PAYLOAD__;
let chart = null;

function fmtNum(v){
  if (v === null || v === undefined) return 'N/A';
  const a = Math.abs(v);
  if (a >= 1e9) return (v/1e9).toFixed(2)+'B';
  if (a >= 1e6) return (v/1e6).toFixed(1)+'M';
  if (a >= 1e3) return (v/1e3).toFixed(1)+'k';
  return v.toFixed(0);
}

function draw(mode){
  const c = DATA.curves[mode];
  if (!c) return;
  document.getElementById('meta').textContent = `${DATA.asset} · ${DATA.ref_display} · Spot R$ ${c.spot.toFixed(2)} · Gamma ${(100*(c.gamma_coverage ?? 0)).toFixed(1)}% · IV ${c.iv_coverage == null ? "N/A" : (100*c.iv_coverage).toFixed(1)+"%"}`;

  const missing = DATA.collection_warning?.missing_expiries || [];
  if (missing.length) document.getElementById('meta').textContent += ' · Coleta parcial: ' + missing.join(', ');
  const empty = DATA.collection_warning?.empty_expiries || [];
  if (empty.length) document.getElementById('meta').textContent += ' · Vencimentos sem OI válido: ' + empty.join(', ');
  if (c.gamma_coverage < 1) document.getElementById('meta').textContent += ' · Gamma parcial';
  document.getElementById('c-score').textContent = c.gamma_score == null ? "N/A" : c.gamma_score.toFixed(2);
  document.getElementById('c-atual').textContent = fmtNum(c.gamma_atual);
  document.getElementById('c-min').textContent = c.gamma_min_neg ? `R$ ${c.gamma_min_neg.strike.toFixed(2)} · ${fmtNum(c.gamma_min_neg.value)}` : '-';
  document.getElementById('c-flip').textContent = c.flip ? `R$ ${c.flip.toFixed(2)}` : '-';
  document.getElementById('c-max').textContent = c.gamma_max_pos ? `R$ ${c.gamma_max_pos.strike.toFixed(2)} · ${fmtNum(c.gamma_max_pos.value)}` : '-';

  const pos = c.net.map(v => v > 0 ? v : null);
  const neg = c.net.map(v => v < 0 ? v : null);

  const spotIdx = c.strikes.length ? c.strikes.reduce((best, s, i) => Math.abs(s-c.spot) < Math.abs(c.strikes[best]-c.spot) ? i : best, 0) : 0;

  const vlinePlugin = {
    id: 'vline',
    afterDatasetsDraw(ch){
      const {ctx, chartArea:{top,bottom}, scales:{x}} = ch;
      ctx.save();
      if (c.strikes.length) {
        const xSpot = x.getPixelForValue(spotIdx);
        ctx.strokeStyle = '#2f81f7'; ctx.lineWidth = 1.5;
        ctx.beginPath(); ctx.moveTo(xSpot, top); ctx.lineTo(xSpot, bottom); ctx.stroke();
      }
      ctx.restore();
    }
  };

  if (chart) chart.destroy();
  chart = new Chart(document.getElementById('gamma'), {
    type: 'line',
    data: {
      labels: c.strikes.map(s => s.toFixed(2)),
      datasets: [
        {label:'Positivo', data:pos, borderColor:'#22c55e', backgroundColor:'rgba(34,197,94,.25)', fill:true, pointRadius:0, tension:.25},
        {label:'Negativo', data:neg, borderColor:'#ef4444', backgroundColor:'rgba(239,68,68,.25)', fill:true, pointRadius:0, tension:.25},
        {label:'GEX líquido por strike (R$/1%)', data:c.net, borderColor:'#facc15', pointRadius:0, tension:.25, fill:false},
      ]
    },
    options: {
      responsive:true,
      maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      plugins:{legend:{labels:{color:getComputedStyle(document.documentElement).getPropertyValue('--text')}}},
      scales:{
        x:{ticks:{color:getComputedStyle(document.documentElement).getPropertyValue('--muted')}},
        y:{ticks:{color:getComputedStyle(document.documentElement).getPropertyValue('--muted')}}
      }
    },
    plugins:[vlinePlugin]
  });
}

const sel = document.getElementById('mode');
Object.keys(DATA.curves).forEach((k, i) => {
  const o = document.createElement('option');
  o.value = k;
  o.textContent = i === 0 ? 'Visão Completa (Agregado)' : k;
  sel.appendChild(o);
});
sel.value = 'AGREGADO';
sel.addEventListener('change', () => draw(sel.value));
draw('AGREGADO');
</script>
</body>
</html>
"""
    return html.replace("__PAYLOAD__", js)


def main() -> None:
    parser = argparse.ArgumentParser(description="BOVA11 Market Gamma")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref-date", required=True)
    parser.add_argument("--ref-tag", required=True)
    parser.add_argument("--spot", type=float, default=None)
    parser.add_argument("--spot-history-file", default=os.path.join(os.path.dirname(__file__), "..", "history", "bova11_spot_history.json"))
    parser.add_argument("--strike-min", type=float, default=None)
    parser.add_argument("--strike-max", type=float, default=None)
    args = parser.parse_args()

    year = datetime.now().year
    ref_iso = args.ref_date
    spot, spot_source, spot_warning = resolve_spot(
        spot=args.spot,
        spot_history_file=args.spot_history_file,
        ref_date=ref_iso,
        ref_tag=args.ref_tag,
        fetcher=fetch_spot_close,
    )

    expiries = discover_expiries_for_tag(args.data_dir, args.ref_tag)
    if not expiries:
        raise SystemExit(f"Nenhum vencimento encontrado para tag {args.ref_tag}")
    if spot is None or spot <= 0:
        raise SystemExit(spot_warning or f"Spot indisponível para {ref_iso}. Informe --spot ou use histórico manual.")

    strike_min = args.strike_min
    strike_max = args.strike_max

    curves = {}
    model_expiries = []
    expired_expiries = []
    agg: Dict[float, dict] = {}

    for e in expiries:
        if gamma_model._resolve_session_and_expiry_t(e["label"], args.ref_tag) == 0:
            expired_expiries.append(e["label"])
            continue
        c = parse_gamma_curve(e["path"], e["label"], float(spot))
        model_rows = gamma_model.parse_csv(e["path"])
        model_expiries.append((e["label"], model_rows))
        model = gamma_model.gamma_profile_summary(model_rows, e["label"], args.ref_tag, float(spot))
        payload = to_curve_payload(c, float(spot), model, strike_min, strike_max)
        if payload["strikes"]:
            curves[e["label"]] = payload
        for s, vals in c.items():
            if s not in agg:
                agg[s] = {key: 0.0 for key in ("call", "put", "net", "call_available", "put_available", "net_available", "oi_total", "oi_with_gamma")}
            for key in ("call", "put", "net"):
                agg[s][key] = None if agg[s][key] is None or vals[key] is None else agg[s][key] + vals[key]
            for key in ("call_available", "put_available", "net_available", "oi_total", "oi_with_gamma"):
                agg[s][key] = agg[s][key] + vals[key] if agg[s][key] is not None and vals[key] is not None else None

    model = summarize_gamma_profiles(model_expiries, args.ref_tag, float(spot))
    curves = {"AGREGADO": to_curve_payload(agg, float(spot), model, strike_min, strike_max), **curves}

    collection_warning = missing_previous_expiries(args.data_dir, args.ref_tag)
    collection_warning["empty_expiries"] = model["empty_expiries"]
    collection_warning["expired_expiries"] = expired_expiries
    payload = {
        "collection_warning": collection_warning,
        "asset": "BOVA11",
        "ref_date": ref_iso,
        "ref_display": datetime.strptime(ref_iso, "%Y-%m-%d").strftime("%d/%m/%Y"),
        "curves": curves,
    }

    if collection_warning["missing_expiries"]:
        curves["AGREGADO"]["flip"] = None
        print("⚠️ Vencimentos ativos ausentes versus " + str(collection_warning["previous_tag"]) + ": " + ", ".join(collection_warning["missing_expiries"]))
    html = build_html(payload)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(html)

    if spot_warning:
        print(f"⚠️ {spot_warning}")
    print(f"✅ Market Gamma: {args.output} | spot={float(spot):.2f} ({spot_source})")


if __name__ == "__main__":
    main()
