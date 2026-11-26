#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 — Option Premium Ranking por Contrato
===========================================
Renderiza uma aba dedicada para ranking de preço × quantidade bruta por ticker,
usando o histórico já persistido pelo módulo bova11_options_tape_flow.py.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
from typing import Any, Dict, List

from bova11_options_tape_flow import load_options_history


def _safe_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")


def _iso_to_br_date(value: str) -> str:
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", str(value or "").strip())
    if not m:
        return ""
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"


def build_missing_html(output: str, ref_date: str, ref_tag: str, msg: str) -> None:
    doc = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOVA11 Option Premium Ranking</title>
<style>
:root {{ --bg:#f6f8fa; --card:#fff; --text:#0f172a; --muted:#64748b; --border:#dbe2ea; }}
[data-theme="dark"] {{ --bg:#0f141b; --card:#161d26; --text:#dbe5f0; --muted:#93a4b8; --border:#2a3644; }}
body {{ margin:0; font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); }}
.wrap {{ max-width:980px; margin:0 auto; padding:34px 16px; }}
.box {{ background:var(--card); border:1px solid var(--border); border-radius:12px; padding:20px; }}
.small {{ color:var(--muted); }}
</style>
<script src="shared.js"></script>
</head>
<body>
<button id="theme-toggle" class="theme-btn" onclick="toggleTheme()" title="Alternar tema">◐</button>
<main class="wrap"><section class="box"><h1>Option Premium Ranking indisponível</h1><p class="small">{html.escape(msg)}</p></section></main>
</body>
</html>"""
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(doc)


def build_html(date_groups: List[Dict[str, Any]], ref_date: str, ref_tag: str) -> str:
    ref_date_br = _iso_to_br_date(ref_date)
    default_date_index = 0
    for i, group in enumerate(date_groups):
        if group.get("date") == ref_date_br:
            default_date_index = i
            break

    date_tabs = "\n".join(
        f'<button class="tab{" active" if i == default_date_index else ""}" data-date-index="{i}">{html.escape(group.get("date", ""))}</button>'
        for i, group in enumerate(date_groups)
    )

    data_json = _safe_json(date_groups)
    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BOVA11 Option Premium Ranking</title>
<style>
:root {{
  --bg:#f3f6f9; --surface:#ffffff; --surface2:#f7fafc; --text:#0f172a; --muted:#5b6776;
  --border:#d9e2ec; --accent:#1d4ed8; --green:#15803d; --red:#b91c1c; --gold:#a16207;
}}
[data-theme="dark"] {{
  --bg:#0f141b; --surface:#161d26; --surface2:#1c2631; --text:#dbe5f0; --muted:#94a3b8;
  --border:#2a3644; --accent:#60a5fa; --green:#4ade80; --red:#f87171; --gold:#fbbf24;
}}
* {{ box-sizing:border-box; }}
body {{ margin:0; font-family:system-ui,-apple-system,sans-serif; background:var(--bg); color:var(--text); }}
.wrap {{ max-width:1520px; margin:0 auto; padding:26px 14px 38px; }}
.panel {{ background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:14px; }}
.head {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-end; margin-bottom:12px; }}
.h1 {{ font-size:1.5rem; font-weight:800; letter-spacing:-.02em; }}
.sub {{ color:var(--muted); font-size:.9rem; margin-top:4px; }}
.tabs {{ display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 12px; }}
.tab {{ border:1px solid var(--border); background:var(--surface); color:var(--text); border-radius:999px; padding:7px 12px; cursor:pointer; font-weight:700; }}
.tab.active {{ border-color:var(--accent); color:var(--accent); background:var(--surface2); }}
.controls {{ display:flex; flex-wrap:wrap; gap:10px; margin-bottom:10px; }}
.ctrl {{ display:flex; align-items:center; gap:6px; color:var(--muted); font-size:.84rem; }}
.ctrl select, .ctrl input {{ border:1px solid var(--border); background:var(--surface2); color:var(--text); border-radius:8px; padding:7px 8px; }}
.grid {{ display:grid; grid-template-columns:1fr 1fr; gap:12px; }}
.block {{ background:var(--surface2); border:1px solid var(--border); border-radius:12px; padding:10px; }}
.block h3 {{ margin:2px 0 8px; font-size:.98rem; }}
table {{ width:100%; border-collapse:collapse; font-size:.84rem; }}
th,td {{ padding:8px 6px; border-bottom:1px solid var(--border); text-align:right; }}
th:first-child,td:first-child {{ text-align:left; }}
th {{ color:var(--muted); font-size:.72rem; text-transform:uppercase; }}
.badge {{ display:inline-flex; border-radius:999px; padding:3px 8px; font-weight:700; font-size:.74rem; border:1px solid var(--border); }}
.po {{ color:var(--red); border-color:rgba(185,28,28,.35); background:rgba(185,28,28,.10); }}
.gamma {{ color:var(--green); border-color:rgba(21,128,61,.35); background:rgba(21,128,61,.10); }}
.itm {{ color:var(--gold); border-color:rgba(161,98,7,.35); background:rgba(161,98,7,.10); }}
.mid {{ color:var(--muted); }}
@media (max-width:980px) {{ .grid {{ grid-template-columns:1fr; }} }}
</style>
<script src="shared.js"></script>
</head>
<body>
<button id="theme-toggle" class="theme-btn" onclick="toggleTheme()" title="Alternar tema">◐</button>
<main class="wrap">
  <section class="panel">
    <div class="head">
      <div>
        <div class="h1">Option Premium Ranking</div>
        <div class="sub">Preço × quantidade bruta por ticker, separado entre CALLS e PUTS. Quantidade e preço × quantidade: unidades não certificadas, inclusive no histórico; valores não representam financeiro certificado.</div>
      </div>
      <div class="sub">Tag: {html.escape(ref_tag)}</div>
    </div>

    <nav class="tabs" id="date-tabs">{date_tabs}</nav>
    <nav class="tabs" id="expiry-tabs"></nav>

    <div class="controls">
      <label class="ctrl">Ordenação
        <select id="sort-mode">
          <option value="desc">Maior prêmio</option>
          <option value="asc">Menor prêmio</option>
        </select>
      </label>
      <label class="ctrl"><input type="checkbox" id="delta-filter"> Delta entre -0.25 e 0.25</label>
    </div>

    <div class="grid">
      <section class="block">
        <h3>CALLS</h3>
        <div style="overflow:auto;"><table><thead><tr><th>Contrato</th><th>Strike</th><th>Delta</th><th>Preço VWAP</th><th>Qtd bruta</th><th>Preço × qtd</th><th>Classificação</th></tr></thead><tbody id="calls-body"></tbody></table></div>
      </section>
      <section class="block">
        <h3>PUTS</h3>
        <div style="overflow:auto;"><table><thead><tr><th>Contrato</th><th>Strike</th><th>Delta</th><th>Preço VWAP</th><th>Qtd bruta</th><th>Preço × qtd</th><th>Classificação</th></tr></thead><tbody id="puts-body"></tbody></table></div>
      </section>
    </div>
  </section>
</main>

<script>
const DATE_GROUPS = {data_json};
let currentDate = {default_date_index};
let currentExpiry = 0;
const state = {{ sort: 'desc', delta: false }};

function fI(v) {{ if (v == null || Number.isNaN(v)) return 'N/A'; return Math.round(v).toLocaleString('pt-BR'); }}
function fP(v,d=2) {{ if (v == null || Number.isNaN(v)) return 'N/A'; return v.toLocaleString('pt-BR', {{minimumFractionDigits:d, maximumFractionDigits:d}}); }}

function clsBadge(c) {{
  if (c === 'Po') return 'badge po';
  if (c === 'Gamma util') return 'badge gamma';
  if (c === 'Muito ITM / caro') return 'badge itm';
  return 'badge mid';
}}

function applyFilters(rows) {{
  let out = [...(rows || [])];
  if (state.delta) out = out.filter(r => r.delta != null && r.delta >= -0.25 && r.delta <= 0.25);
  out.sort((a,b) => state.sort === 'asc' ? (a.total_financial||0)-(b.total_financial||0) : (b.total_financial||0)-(a.total_financial||0));
  return out;
}}

function renderTable(target, rows) {{
  const body = document.getElementById(target);
  const filtered = applyFilters(rows);
  if (!filtered.length) {{
    body.innerHTML = '<tr><td colspan="7">Sem dados no filtro atual</td></tr>';
    return;
  }}
  body.innerHTML = filtered.map(r => `
    <tr>
      <td>${{r.contract || 'N/A'}}</td>
      <td>${{fP(r.strike,0)}}</td>
      <td>${{r.delta == null ? 'N/A' : fP(r.delta,3)}}</td>
      <td>${{fP(r.vwap,4)}}</td>
      <td>${{fI(r.total_qty)}}</td>
      <td>${{fP(r.total_financial)}}</td>
      <td><span class="${{clsBadge(r.classification)}}">${{r.classification || 'Intermediario'}}</span></td>
    </tr>
  `).join('');
}}

function getCurrentExpiries() {{ return DATE_GROUPS[currentDate]?.expiries || []; }}

function renderExpiryTabs() {{
  const exps = getCurrentExpiries();
  if (currentExpiry >= exps.length) currentExpiry = 0;
  document.getElementById('expiry-tabs').innerHTML = exps.map((e, i) =>
    `<button class="tab${{i===currentExpiry?' active':''}}" data-exp="${{i}}">${{e.label}}</button>`
  ).join('');
  document.querySelectorAll('[data-exp]').forEach(btn => btn.addEventListener('click', () => {{
    currentExpiry = Number(btn.dataset.exp);
    render();
  }}));
}}

function render() {{
  const exp = getCurrentExpiries()[currentExpiry];
  if (!exp) {{
    document.getElementById('calls-body').innerHTML = '<tr><td colspan="7">Sem dados</td></tr>';
    document.getElementById('puts-body').innerHTML = '<tr><td colspan="7">Sem dados</td></tr>';
    return;
  }}
  const ranking = exp.contract_rankings || {{}};
  renderTable('calls-body', ranking.calls_contracts || []);
  renderTable('puts-body', ranking.puts_contracts || []);
  document.querySelectorAll('#expiry-tabs .tab').forEach((b,i)=>b.classList.toggle('active', i===currentExpiry));
}}

document.querySelectorAll('[data-date-index]').forEach(btn => btn.addEventListener('click', () => {{
  currentDate = Number(btn.dataset.dateIndex);
  currentExpiry = 0;
  document.querySelectorAll('[data-date-index]').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderExpiryTabs();
  render();
}}));

document.getElementById('sort-mode').addEventListener('change', (ev) => {{ state.sort = ev.target.value === 'asc' ? 'asc' : 'desc'; render(); }});
document.getElementById('delta-filter').addEventListener('change', (ev) => {{ state.delta = !!ev.target.checked; render(); }});

renderExpiryTabs();
render();
</script>
</body>
</html>"""


def main() -> int:
    parser = argparse.ArgumentParser(description="BOVA11 Option Premium Ranking")
    parser.add_argument("--output", required=True, help="HTML de saída")
    parser.add_argument("--ref-date", required=True, help="Data ISO de referência")
    parser.add_argument("--ref-tag", default="", help="Tag de referência")
    parser.add_argument(
        "--history-file",
        default=os.path.join(os.path.dirname(__file__), "..", "history", "bova11_options_tape_flow.json"),
        help="Histórico do option tape flow",
    )
    args = parser.parse_args()

    date_groups = load_options_history(args.history_file)
    if not date_groups:
        msg = f"Histórico vazio em {args.history_file}. Rode o módulo Option Tape Flow primeiro."
        build_missing_html(args.output, args.ref_date, args.ref_tag, msg)
        print(f"⚠️ {msg}")
        return 0

    html_doc = build_html(date_groups, args.ref_date, args.ref_tag)
    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        fh.write(html_doc)
    print(f"✅ Option Premium Ranking gerado: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
