#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare option open interest per contract across two B3 snapshots."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from datetime import date
from html import escape

from bova11_expiry_family import matches_expiry_label
from bova11_shared import (
    expiry_label_to_iso,
    expiry_label_to_iso_nearest,
    parse_b3_close_csv,
    parse_br_number,
    tag_to_iso_nearest,
)


SPACE_RE = re.compile(r"^venc\s+(.+?)\s+fechamento\s+\(([^)]+)\)\.csv$", re.I)
UNDER_RE = re.compile(r"^venc_(.+?)_fechamento__([^_]+)(_Volume)?_\.csv$", re.I)


def _label_key(label):
    return re.sub(r"\s+", " ", label.replace("_", " ").strip().lower())


def discover_expirations(data_dir, tag):
    """Pair exact-tag close and Volume files by expiry label."""
    found = {}
    wanted = str(tag).strip().lower()
    for name in os.listdir(data_dir):
        match = SPACE_RE.fullmatch(name)
        if match:
            label, token = match.group(1).strip(), match.group(2).strip().lower()
            is_volume = token.endswith(" volume")
            token = token[:-7].strip() if is_volume else token
        else:
            match = UNDER_RE.fullmatch(name)
            if not match:
                continue
            label, token, is_volume = match.group(1).replace("_", " ").strip(), match.group(2).lower(), bool(match.group(3))
        if token != wanted:
            continue
        item = found.setdefault(_label_key(label), {"label": label, "close": None, "volume": None})
        item["close" if not is_volume else "volume"] = os.path.join(data_dir, name)
    return sorted((item for item in found.values() if item["close"]), key=lambda x: x["label"].lower())


def _read_volume(path):
    by_ticker = {}
    if not path or not os.path.isfile(path):
        return by_ticker
    try:
        with open(path, encoding="latin-1", newline="") as source:
            for cols in csv.reader(source, delimiter=";"):
                if len(cols) < 11 or cols[5].strip().lower() == "strike":
                    continue
                for side, ticker_col, oi_col, vol_col in (("call", 0, 2, 1), ("put", 10, 8, 9)):
                    ticker = cols[ticker_col].strip()
                    if ticker:
                        by_ticker[ticker] = {
                            "oi": parse_br_number(cols[oi_col], none_on_blank=True),
                            "volume": parse_br_number(cols[vol_col], none_on_blank=True),
                        }
    except OSError:
        return {}
    return by_ticker


def read_contracts(close_path, label, volume_path, ref_date):
    """Read one record for each valid call/put contract in a close snapshot."""
    vol = _read_volume(volume_path)
    same_year_expiry = expiry_label_to_iso(label, date.fromisoformat(ref_date).year)
    if same_year_expiry and same_year_expiry < ref_date:
        expiry = ""
    else:
        expiry = expiry_label_to_iso_nearest(label, ref_date) or ""
    try:
        dte = (date.fromisoformat(expiry) - date.fromisoformat(ref_date)).days if expiry else None
    except ValueError:
        dte = None
    records = {}
    for row in parse_b3_close_csv(close_path, merge_volume=False):
        if not matches_expiry_label(row.get("call_ticker", ""), row.get("put_ticker", ""), label):
            continue
        for side in ("call", "put"):
            ticker = (row.get(f"{side}_ticker") or "").strip()
            if not ticker:
                continue
            market = vol.get(ticker, {})
            oi = market.get("oi")
            if oi is None:
                oi = row.get(f"{side}_oi")
            record = {
                "underlying": "BOVA11", "ticker": ticker, "side": side.title(), "expiry": label,
                "expiry_date": expiry, "strike": row.get("strike"),
                "oi": oi, "volume": market.get("volume"),
                "bid": row.get(f"{side}_bid"), "ask": row.get(f"{side}_ask"),
                "iv": row.get(f"{side}_iv"), "delta": row.get(f"{side}_delta"),
                "gamma": row.get(f"{side}_gamma"), "theta": row.get(f"{side}_theta"),
                "vega": row.get(f"{side}_vega"), "rho": row.get(f"{side}_rho"),
                "trades": row.get(f"{side}_trades"), "dte": dte,
            }
            records[ticker] = record
    return list(records.values())


def compare_contracts(current, prior, ref_date):
    previous = {row["ticker"]: row for row in prior}
    result = []
    for now in current:
        old = previous.get(now["ticker"], {})
        old_oi, new_oi = old.get("oi"), now.get("oi")
        change = new_oi - old_oi if old_oi is not None and new_oi is not None else None
        pct = change / old_oi * 100 if change is not None and old_oi > 0 else None
        result.append({**now, "ref_date": ref_date, "oi_d1": old_oi, "oi_d": new_oi,
                       "oi_change": change, "oi_change_abs": abs(change) if change is not None else None,
                       "oi_change_pct": pct,
                       "volume_d1": old.get("volume"), "volume_d": now.get("volume")})
    return sorted(result, key=lambda row: (row["oi_change"] is None, -abs(row["oi_change"] or 0), row["ticker"]))


COLUMNS = [
    ("underlying", "Ativo"), ("ticker", "Ticker"), ("side", "Lado"), ("expiry", "Vencimento"),
    ("strike", "Strike"), ("oi_d1", "OI D-1"), ("oi_d", "OI D"), ("oi_change", "Δ OI"),
    ("oi_change_pct", "Δ OI %"), ("volume_d1", "Volume D-1"), ("volume_d", "Volume D"),
    ("bid", "Bid D"), ("ask", "Ask D"), ("iv", "IV D"), ("delta", "Delta D"),
    ("gamma", "Gamma D"), ("theta", "Theta D"), ("vega", "Vega D"), ("rho", "Rho D"),
    ("trades", "Negócios D"), ("dte", "DTE"),
]


def render_html(rows, dates, output_path):
    data = json.dumps(rows, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    headers = "".join(f'<th data-key="{escape(key)}">{escape(title)}</th>' for key, title in COLUMNS)
    cols_js = json.dumps([key for key, _ in COLUMNS])
    title_dates = escape(f"D-1: {dates.get('previous') or '—'} · D: {dates.get('current') or '—'}")
    html = f'''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Chain OI Changes</title>
<style>
:root{{--bg:#fafaf8;--fg:#1a1a18;--muted:#686862;--panel:#fff;--line:#ddd;--accent:#177d67}}[data-theme=dark]{{--bg:#0d1117;--fg:#c9d1d9;--muted:#9aa4af;--panel:#161b22;--line:#30363d;--accent:#53c6a7}}
*{{box-sizing:border-box}}body{{margin:0;padding:20px;background:var(--bg);color:var(--fg);font:14px system-ui,sans-serif}}header{{display:flex;justify-content:space-between;align-items:center;gap:14px;flex-wrap:wrap}}h1{{font-size:22px;margin:0}}.dates{{color:var(--muted);margin-top:5px}}.controls{{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0}}input,select,button{{background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:8px 10px}}button{{cursor:pointer}}button:hover{{border-color:var(--accent)}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:8px;background:var(--panel);max-height:calc(100vh - 150px)}}table{{border-collapse:collapse;width:100%;white-space:nowrap}}th,td{{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right}}th{{position:sticky;top:0;background:var(--panel);cursor:pointer;z-index:1}}td:nth-child(-n+4),th:nth-child(-n+4){{text-align:left}}tbody tr:hover{{background:color-mix(in srgb,var(--accent) 8%,var(--panel))}}.count{{color:var(--muted);margin:8px 2px}}@media(max-width:700px){{body{{padding:12px}}h1{{font-size:19px}}}}
</style></head><body><header><div><h1>Chain OI Changes</h1><div class="dates">{title_dates}</div></div><button id="theme-toggle" type="button">◐ Tema</button></header>
<div class="controls"><select id="expiry"><option value="">Todos vencimentos</option></select><select id="side"><option value="">Calls e puts</option><option>Call</option><option>Put</option></select><input id="search" type="search" placeholder="Buscar ticker ou vencimento"><button id="download">Baixar CSV filtrado</button></div><div class="count" id="count"></div><div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody id="body"></tbody></table></div>
<script>
const rows={data}, cols={cols_js}; let sortKey='oi_change_abs', sortDesc=true;
const body=document.getElementById('body'), expiry=document.getElementById('expiry'), side=document.getElementById('side'), search=document.getElementById('search');
const fmt=v=>v===null||v===undefined?'':typeof v==='number'?new Intl.NumberFormat('pt-BR',{{maximumFractionDigits:4}}).format(v):String(v);
for(const x of [...new Set(rows.map(r=>r.expiry))].sort()){{const o=document.createElement('option');o.value=x;o.textContent=x;expiry.append(o)}}
function filtered(){{const q=search.value.toLowerCase();return rows.filter(r=>(!expiry.value||r.expiry===expiry.value)&&(!side.value||r.side.toLowerCase()===side.value.toLowerCase())&&(!q||r.ticker.toLowerCase().includes(q)||r.expiry.toLowerCase().includes(q))).sort((a,b)=>{{const x=a[sortKey],y=b[sortKey];if(x==null)return y==null?0:1;if(y==null)return -1;const c=typeof x==='number'&&typeof y==='number'?x-y:String(x).localeCompare(String(y));return sortDesc?-c:c}})}}
function draw(){{const list=filtered();body.replaceChildren(...list.map(r=>{{const tr=document.createElement('tr');for(const k of cols){{const td=document.createElement('td');td.textContent=fmt(r[k]);tr.append(td)}}return tr}}));document.getElementById('count').textContent=list.length+' contratos'}}
document.querySelectorAll('th').forEach(th=>th.onclick=()=>{{if(sortKey===th.dataset.key)sortDesc=!sortDesc;else{{sortKey=th.dataset.key;sortDesc=false}}draw()}});[expiry,side,search].forEach(x=>x.addEventListener('input',draw));draw();
document.getElementById('download').onclick=()=>{{const list=filtered(), q=v=>'"'+String(v??'').replaceAll('"','""')+'"';const csv=[{json.dumps([title for _,title in COLUMNS])}.join(';'),...list.map(r=>cols.map(k=>q(r[k])).join(';'))].join('\\r\\n');const a=document.createElement('a');a.href=URL.createObjectURL(new Blob(['\\ufeff'+csv],{{type:'text/csv;charset=utf-8'}}));a.download='bova11_chain_oi_changes.csv';a.click();URL.revokeObjectURL(a.href)}};
function setTheme(dark){{document.documentElement.toggleAttribute('data-theme',!!dark);if(dark)document.documentElement.setAttribute('data-theme','dark');try{{localStorage.setItem('bova11-theme',dark?'dark':'light')}}catch(e){{}}}}
document.getElementById('theme-toggle').onclick=()=>{{setTheme(document.documentElement.getAttribute('data-theme')!=='dark');parent.postMessage({{type:'bova11-theme',dark:document.documentElement.getAttribute('data-theme')==='dark'}},'*')}};
try{{setTheme(localStorage.getItem('bova11-theme')==='dark')}}catch(e){{}}
addEventListener('message',e=>{{if(e.data?.type==='bova11-theme')setTheme(!!e.data.dark)}});
</script></body></html>'''
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as target:
        target.write(html)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--ref-date", required=True)
    parser.add_argument("--ref-tag", required=True)
    parser.add_argument("--previous-tag", default="")
    args = parser.parse_args()
    current_files = discover_expirations(args.data_dir, args.ref_tag)
    if not current_files:
        print(f"Erro: nenhum CSV de fechamento encontrado para {args.ref_tag} em {args.data_dir}", file=sys.stderr)
        return 2
    previous_files = { _label_key(item["label"]): item for item in discover_expirations(args.data_dir, args.previous_tag) } if args.previous_tag else {}
    current, prior = [], []
    for item in current_files:
        if not os.path.isfile(item["close"]):
            continue
        current.extend(read_contracts(item["close"], item["label"], item["volume"], args.ref_date))
        old = previous_files.get(_label_key(item["label"]))
        if old and old["close"] and os.path.isfile(old["close"]):
            prior.extend(read_contracts(old["close"], old["label"], old["volume"], args.ref_date))
    rows = compare_contracts(current, prior, args.ref_date)
    render_html(rows, {"current": args.ref_date, "previous": tag_to_iso_nearest(args.previous_tag, args.ref_date) if args.previous_tag else ""}, args.output)
    print(f"Chain OI Changes: {len(rows)} contratos → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
