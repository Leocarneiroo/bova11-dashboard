#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BOVA11 Runner — Orquestrador de todos os scripts
=================================================
Roda os módulos ativos na sequência correta.

Pede os dados de spot UMA única vez e repassa para todos os scripts.

Uso:
  python3 bova11_runner.py
"""

import os
import sys
import re
import json
import html
import subprocess
import importlib.util

DIR         = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(DIR, 'scripts')
DATA_DIR    = os.path.join(DIR, 'data')
OUTPUT_DIR  = os.path.join(DIR, 'output')
HISTORY_DIR = os.path.join(DIR, 'history')
SPOT_HISTORY_FILE = os.path.join(HISTORY_DIR, 'bova11_spot_history.json')

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from bova11_shared import discover_date_tags, tag_to_iso_nearest, upsert_spot_history
from bova11_prune_site import prune_site

# ═══════════════════════════════════════
# LISTA DE SCRIPTS (em ordem de execução)
# ═══════════════════════════════════════
SCRIPTS = [
    ("1/12",  "bova11_auto.py",                "Dashboard Principal (OI, GEX, MaxPain)"),
    ("2/12",  "bova11_insights_auto.py",       "Insights Narrativos por Vencimento"),
    ("3/12",  "bova11_skew_history.py",        "Histórico de IV / Skew"),
    ("4/12",  "bova11_trade_score_james.py",   "Trade Score & Institutional Bias"),
    ("5/12",  "bova11_quant_institucional.py", "Quant Institucional (RevL / MoM / Força Relativa)"),
    ("6/12",  "bova11_tex_dex_vex.py",         "GEX / DEX / VEX / TEX / CEX — Perfis por Spot"),
    ("7/12",  "bova11_oi_stats_light.py",      "OI Stats — Distribuição Estatística"),
    ("8/12",  "bova11_max_pain.py",            "Max Pain Relevance Indicator"),
    ("9/12",  "bova11_gravity.py",             "Mapa Gravitacional — μOI / μGEX / μDEX / Convergência"),
    ("10/12", "bova11_market_gamma.py",        "Market Gamma — Curva Líquida Agregada e por Vencimento"),
    ("11/12", "bova11_diagnostico_4d.py",      "Diagnóstico 4D — Cenário GEX/DEX/VEX/TEX"),
    ("12/12", "bova11_chain_oi_changes.py",    "Chain OI Changes — Variação de OI por Contrato"),
]

# ═══════════════════════════════════════
# AUTO-DISCOVERY DE DATAS (igual aos outros scripts)
# ═══════════════════════════════════════
def tag_to_iso_date(tag, reference_date=None):
    return tag_to_iso_nearest(tag, reference_date)

def find_missing_deps():
    required = {
        "numpy": [
            "bova11_oi_stats_light.py",
            "bova11_gravity.py",
            "bova11_quant_institucional.py",
        ],
        "pandas": [
            "bova11_oi_stats_light.py",
            "bova11_max_pain.py",
            "bova11_gravity.py",
            "bova11_quant_institucional.py",
        ],
        "scipy": [
            "bova11_oi_stats_light.py",
            "bova11_gravity.py",
        ],
        "yfinance": [
            "bova11_quant_institucional.py",
        ],
    }
    missing = {}
    for pkg, scripts in required.items():
        if importlib.util.find_spec(pkg) is None:
            missing[pkg] = scripts
    return missing

def format_date_label(tag):
    meses = {
        'jan': 'Jan', 'fev': 'Fev', 'mar': 'Mar', 'abr': 'Abr',
        'mai': 'Mai', 'jun': 'Jun', 'jul': 'Jul', 'ago': 'Ago',
        'set': 'Set', 'out': 'Out', 'nov': 'Nov', 'dez': 'Dez'
    }
    normalized = re.sub(r'(pos|pre)([a-z]{3})$', r'\2', tag.lower())
    m = re.match(r'(\d{1,2})([a-z]{3})$', normalized)
    if m:
        dia = m.group(1)
        mes = meses.get(m.group(2), m.group(2).capitalize())
        return f"{dia}/{mes}"
    return tag

def discover_dates():
    return discover_date_tags(DATA_DIR)

# ═══════════════════════════════════════
# INPUT DE SPOT
# ═══════════════════════════════════════
def ask_spot(prompt):
    while True:
        raw = input(f"  {prompt}: ").strip().replace(',', '.')
        try:
            val = float(raw)
            if val > 0:
                return val
            print("  ❌ O spot deve ser positivo.")
        except ValueError:
            print("  ❌ Valor inválido. Use formato: 188.50")

def ask_strike_range():
    """Para o skew_history: pede range de strikes (Enter = padrão)."""
    default_min, default_max = 140, 180
    print(f"\n  Range de strikes para o Skew History (padrão: {default_min} a {default_max})")
    try:
        min_str = input(f"    Strike mínimo [{default_min}]: ").strip()
        max_str = input(f"    Strike máximo [{default_max}]: ").strip()
        min_val = int(min_str) if min_str else default_min
        max_val = int(max_str) if max_str else default_max
        return min_val, max_val
    except Exception:
        print("  ⚠️ Entrada inválida, usando padrão.")
        return default_min, default_max

# ═══════════════════════════════════════
# EXECUÇÃO DE SCRIPT
# ═══════════════════════════════════════
def run_script(script_path, stdin_input):
    """Executa um script Python passando stdin_input como entrada."""
    result = subprocess.run(
        [sys.executable, script_path],
        input=stdin_input,
        cwd=DIR,
        text=True,
        capture_output=False,  # mostra output diretamente no terminal
    )
    return result.returncode

def run_script_with_args(script_path, args):
    """Executa um script Python passando argumentos CLI (argparse)."""
    result = subprocess.run(
        [sys.executable, script_path] + args,
        cwd=DIR,
        text=True,
        capture_output=False,
    )
    return result.returncode

def publish_output_dashboard(now_str=None):
    """Poda e publica o conteúdo de output/ na branch gh-pages."""
    if now_str is None:
        from datetime import datetime

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    removed = prune_site(OUTPUT_DIR)
    if removed:
        print(f"  🧹 {len(removed)} arquivo(s) antigos removidos do site.")

    subprocess.check_call(
        ["git", "add", "-A"],
        cwd=OUTPUT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=OUTPUT_DIR,
    ).returncode == 0:
        print("  ✓ Nada novo para publicar.")
        return

    subprocess.check_call(
        ["git", "commit", "-m", f"Auto-update: {now_str}"],
        cwd=OUTPUT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.check_call(
        ["git", "push", "origin", "gh-pages"],
        cwd=OUTPUT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

def finalize_dashboard_run(errors):
    """Mostra resumo final e oferece publicação, mesmo em atualização parcial."""
    print()
    print("=" * 65)
    if not errors:
        print(" ✅ TODOS OS SCRIPTS CONCLUÍDOS COM SUCESSO!")
        print()
        print(" 🌟 DASHBOARD UNIFICADO GERADO!")
        prompt = " 🌐 Deseja atualizar o Dashboard na internet para a galera ver? (s/n): "
    else:
        print(f" ⚠️ CONCLUÍDO COM ERROS NOS SCRIPTS: {', '.join(errors)}")
        print(" 🌗 DASHBOARD UNIFICADO GERADO COM ATUALIZAÇÃO PARCIAL!")
        prompt = " 🌐 Deseja publicar mesmo com atualização parcial? (s/n): "

    print(" ➡️ Abra o arquivo local: ./output/index.html")
    print("=" * 65)
    print()

    resp = input(prompt)
    if resp.strip().lower() not in ("s", "sim", "y", "yes"):
        return

    print(" Enviando para o GitHub Pages...")
    try:
        publish_output_dashboard()
        print(" ✅ Publicado com sucesso!")
        print(" 🌐 Link: https://Leocarneiroo.github.io/bova11-dashboard/")
        print(" (A atualização pode levar ~1 minuto para aparecer)")
    except subprocess.CalledProcessError as exc:
        print(f" ❌ Erro ao publicar (código {exc.returncode}).")
    except Exception as exc:
        print(f" ❌ Erro ao publicar: {exc}")

# ═══════════════════════════════════════
# GERADOR DO DASHBOARD UNIFICADO (index.html)
# ═══════════════════════════════════════
def build_dashboard_modules(tag_d1, tag_d, data_d1, data_d):
    """Retorna a lista unica de HTMLs expostos no dashboard."""
    str_d1 = data_d1.replace('/', '')
    str_d  = data_d.replace('/', '')

    modules = [
        {
            "title": "Rankings & Overview",
            "file": f"bova11_rankings_pro_{tag_d1}_vs_{tag_d}.html",
            "group": "Hoje",
            "priority": 1,
            "description": "Panorama agregado de OI, IV e volume por vencimento.",
            "quick": True,
        },
        {
            "title": "GEX & MaxPain",
            "file": f"bova11_gex_pro_{tag_d1}_vs_{tag_d}.html",
            "group": "Hoje",
            "priority": 2,
            "description": "Regime gamma, flip e strikes dominantes do dia.",
            "quick": True,
        },
        {
            "title": "Insights Analíticos",
            "file": f"bova11_insights_{str_d1}_vs_{str_d}.html",
            "group": "Hoje",
            "priority": 3,
            "description": "Leitura operacional por vencimento com foco no que mudou.",
            "quick": True,
        },
        {
            "title": "Trade Score & Bias",
            "file": f"bova11_trade_score_{tag_d1}_vs_{tag_d}.html",
            "group": "Hoje",
            "priority": 4,
            "description": "Score por strike e viés institucional estimado.",
            "quick": False,
        },
        {
            "title": "OI Stats",
            "file": "bova11_oi_stats_light.html",
            "group": "Estrutura",
            "priority": 1,
            "description": "Distribuição estatística do OI e concentração por vencimento.",
            "quick": False,
        },
        {
            "title": "GEX DEX VEX TEX CEX",
            "file": f"bova11_tex_dex_vex_{tag_d1}_vs_{tag_d}.html",
            "group": "Estrutura",
            "priority": 2,
            "description": "Perfis por spot para GEX, DEX, VEX, TEX e CEX com Merton + forward implícito via put-call parity.",
            "quick": False,
        },
        {
            "title": "Max Pain Relevance",
            "file": "bova11_max_pain.html",
            "group": "Estrutura",
            "priority": 3,
            "description": "Força relativa do pinning considerando prazo e OI.",
            "quick": False,
        },
        {
            "title": "Mapa Gravitacional",
            "file": "bova11_gravity_map.html",
            "group": "Estrutura",
            "priority": 4,
            "description": "Centros de massa de OI, GEX e DEX no mesmo painel.",
            "quick": False,
        },
        {
            "title": "Market Gamma",
            "file": "bova11_market_gamma.html",
            "group": "Estrutura",
            "priority": 5,
            "description": "Curva gamma líquida agregada e por vencimento.",
            "quick": False,
        },
        {
            "title": "Skew & IV History",
            "file": "bova11_skew_history.html",
            "group": "Histórico",
            "priority": 1,
            "description": "Evolução histórica de skew e IV média.",
            "quick": False,
        },
        {
            "title": "Quant Institucional",
            "file": "bova11_quant_institucional.html",
            "group": "Histórico",
            "priority": 2,
            "description": "Força relativa, momentum e leitura institucional.",
            "quick": False,
        },
        {
            "title": "Diagnóstico 4D",
            "file": "bova11_diagnostico_4d.html",
            "group": "Laboratório",
            "priority": 1,
            "description": "Leitura cruzada entre GEX, DEX, VEX e TEX.",
            "quick": False,
        },
        {
            "title": "Chain OI Changes",
            "file": "bova11_chain_oi_changes.html",
            "group": "Variações",
            "priority": 1,
            "description": "Variação diária de OI por contrato e vencimento.",
            "quick": False,
        },
    ]
    return modules


def gerar_index_html(tag_d1, tag_d, data_d1, data_d, update_label):
    """Cria o index.html com o menu lateral para agrupar todos os reports."""
    modules = build_dashboard_modules(tag_d1, tag_d, data_d1, data_d)

    group_order = ["Hoje", "Estrutura", "Histórico", "Laboratório", "Variações"]
    first_module = modules[0]
    first_file = first_module["file"]
    first_title = html.escape(first_module["title"])
    first_desc = html.escape(first_module["description"])

    menu_sections = []
    for group in group_order:
        group_id = re.sub(r'[^a-z0-9]+', '-', group.lower()).strip('-')
        group_modules = sorted(
            [m for m in modules if m["group"] == group],
            key=lambda item: item["priority"],
        )
        expanded = group in ("Hoje", "Estrutura", "Variações")
        items_html = []
        for module in group_modules:
            active_cls = " active" if module["file"] == first_file else ""
            items_html.append(
                f'''
                <button class="menu-item{active_cls}" data-file="{module["file"]}" onclick="loadPage(this.dataset.file)">
                    <span class="mi-title">{html.escape(module["title"])}</span>
                    <span class="mi-desc">{html.escape(module["description"])}</span>
                </button>'''
            )
        menu_sections.append(
            f'''
            <section class="menu-group">
                <button class="group-toggle{" expanded" if expanded else ""}" data-group="{group_id}" onclick="toggleGroup('{group_id}')" aria-expanded="{"true" if expanded else "false"}">
                    <span>{html.escape(group)}</span>
                    <span class="group-count">{len(group_modules)}</span>
                </button>
                <div class="group-items{" expanded" if expanded else ""}" id="group-{group_id}">
                    {"".join(items_html)}
                </div>
            </section>'''
        )

    quick_links = []
    for module in modules:
        if not module["quick"]:
            continue
        active_cls = " active" if module["file"] == first_file else ""
        quick_links.append(
            f'<button class="quick-link{active_cls}" data-file="{module["file"]}" onclick="loadPage(this.dataset.file)">{html.escape(module["title"])}</button>'
        )

    modules_json = json.dumps(modules, ensure_ascii=False)

    html_content = f'''<!DOCTYPE html>
<html lang="pt-BR">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>BOVA11 Dashboard - {data_d1} vs {data_d}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Instrument+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #f3f5f7;
            --surface: #ffffff;
            --surface-2: #f7f9fb;
            --surface-3: #eef3f8;
            --border: #dce2e8;
            --border-strong: #c9d3dd;
            --text: #111827;
            --muted: #5f6b7a;
            --soft: #7a8696;
            --accent: #2563eb;
            --accent-soft: rgba(37, 99, 235, 0.08);
            --danger: #c33c31;
            --shadow: 0 18px 45px rgba(17, 24, 39, 0.06);
            --sidebar-w: 330px;
            --topbar-h: 56px;
            --font: 'Instrument Sans', system-ui, sans-serif;
            --mono: 'JetBrains Mono', monospace;
        }}
        [data-theme="dark"] {{
            --bg: #0f141b;
            --surface: #141b23;
            --surface-2: #19212b;
            --surface-3: #1f2933;
            --border: #273240;
            --border-strong: #334155;
            --text: #e5ebf3;
            --muted: #a3afbf;
            --soft: #7d8a9b;
            --accent: #6ea8ff;
            --accent-soft: rgba(110, 168, 255, 0.12);
            --danger: #f87171;
            --shadow: 0 22px 50px rgba(0, 0, 0, 0.28);
        }}
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html, body {{ height: 100%; }}
        body {{
            font-family: var(--font);
            background: var(--bg);
            color: var(--text);
            display: flex;
            min-height: 100vh;
            height: 100dvh;
            overflow: hidden;
        }}
        button {{ font: inherit; }}
        .mono {{ font-family: var(--mono); }}
        .topbar {{
            display: none;
            align-items: center;
            gap: 12px;
            min-height: var(--topbar-h);
            padding: 12px 16px;
            background: var(--surface);
            border-bottom: 1px solid var(--border);
            position: relative;
            z-index: 180;
        }}
        .hamburger, .theme-toggle {{
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--text);
            border-radius: 999px;
            padding: 9px 12px;
            cursor: pointer;
            transition: border-color 0.2s ease, color 0.2s ease, background 0.2s ease;
        }}
        .hamburger:hover, .theme-toggle:hover {{
            border-color: var(--accent);
            color: var(--accent);
        }}
        .topbar-title {{
            min-width: 0;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }}
        .topbar-title strong {{
            font-size: 0.98rem;
            line-height: 1.2;
        }}
        .topbar-title span {{
            font-size: 0.72rem;
            color: var(--muted);
            line-height: 1.2;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }}
        .overlay {{
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(15, 23, 42, 0.4);
            backdrop-filter: blur(2px);
            z-index: 190;
        }}
        .overlay.open {{ display: block; }}
        .sidebar {{
            width: var(--sidebar-w);
            background: var(--surface);
            border-right: 1px solid var(--border);
            display: flex;
            flex-direction: column;
            flex-shrink: 0;
            min-height: 100%;
            box-shadow: inset -1px 0 0 rgba(255,255,255,0.02);
        }}
        @media (min-width: 981px) {{
            body.sidebar-collapsed .sidebar {{ display: none; }}
        }}
        .brand {{
            padding: 28px 24px 20px;
            border-bottom: 1px solid var(--border);
        }}
        .brand-kicker {{
            font-size: 0.7rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--soft);
            margin-bottom: 10px;
            font-family: var(--mono);
        }}
        .brand h1 {{
            font-size: 1.18rem;
            letter-spacing: -0.03em;
            margin-bottom: 6px;
        }}
        .brand p {{
            font-size: 0.88rem;
            color: var(--muted);
            line-height: 1.5;
            max-width: 28ch;
        }}
        .menu {{
            padding: 18px 16px 22px;
            overflow-y: auto;
            flex: 1;
            min-height: 0;
        }}
        .menu-group + .menu-group {{
            margin-top: 10px;
        }}
        .group-toggle {{
            width: 100%;
            border: none;
            background: transparent;
            color: var(--text);
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 10px 10px 10px 12px;
            border-radius: 12px;
            cursor: pointer;
            font-weight: 600;
            letter-spacing: -0.01em;
        }}
        .group-toggle:hover {{
            background: var(--surface-2);
        }}
        .group-toggle::after {{
            content: '+';
            color: var(--muted);
            font-weight: 500;
        }}
        .group-toggle.expanded::after {{
            content: '−';
        }}
        .group-count {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-width: 28px;
            height: 22px;
            padding: 0 8px;
            border-radius: 999px;
            border: 1px solid var(--border);
            color: var(--muted);
            font-size: 0.74rem;
            font-family: var(--mono);
        }}
        .group-items {{
            display: none;
            gap: 6px;
            padding: 6px 0 4px;
        }}
        .group-items.expanded {{
            display: grid;
        }}
        .menu-item {{
            border: 1px solid transparent;
            background: transparent;
            width: 100%;
            text-align: left;
            padding: 12px 14px;
            border-radius: 14px;
            cursor: pointer;
            display: grid;
            gap: 4px;
            color: var(--text);
            transition: background 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
        }}
        .menu-item:hover {{
            background: var(--surface-2);
            border-color: var(--border);
            transform: translateY(-1px);
        }}
        .menu-item.active {{
            background: var(--accent-soft);
            border-color: rgba(37, 99, 235, 0.2);
        }}
        .mi-title {{
            font-size: 0.92rem;
            font-weight: 600;
            letter-spacing: -0.01em;
        }}
        .mi-desc {{
            font-size: 0.76rem;
            line-height: 1.45;
            color: var(--muted);
        }}
        .content {{
            flex: 1;
            min-width: 0;
            display: flex;
            flex-direction: column;
            overflow: hidden;
        }}
        .content-shell {{
            display: flex;
            flex-direction: column;
            gap: 16px;
            padding: 18px 20px 20px;
            height: 100%;
            min-height: 0;
        }}
        .context-bar {{
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 20px;
            padding: 18px 20px;
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 18px;
            box-shadow: var(--shadow);
        }}
        .sidebar-toggle {{
            flex-shrink: 0;
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--text);
            border-radius: 999px;
            padding: 9px 12px;
            cursor: pointer;
        }}
        .sidebar-toggle:hover {{
            border-color: var(--accent);
            color: var(--accent);
        }}
        .context-meta {{
            min-width: 0;
            display: grid;
            gap: 8px;
        }}
        .context-kicker {{
            font-size: 0.72rem;
            font-family: var(--mono);
            letter-spacing: 0.1em;
            text-transform: uppercase;
            color: var(--soft);
        }}
        .context-head {{
            display: flex;
            flex-wrap: wrap;
            align-items: baseline;
            gap: 12px;
        }}
        .context-head h2 {{
            font-size: 1.38rem;
            letter-spacing: -0.04em;
            line-height: 1.1;
        }}
        .context-desc {{
            font-size: 0.92rem;
            color: var(--muted);
            line-height: 1.5;
            max-width: 68ch;
        }}
        .context-time {{
            font-size: 0.76rem;
            color: var(--soft);
            white-space: nowrap;
        }}
        .context-side {{
            display: flex;
            flex-direction: column;
            align-items: flex-end;
            gap: 10px;
            flex-shrink: 0;
        }}
        .update-badge {{
            font-size: 0.72rem;
            color: var(--soft);
            padding: 7px 10px;
            border-radius: 999px;
            background: var(--surface-2);
            border: 1px solid var(--border);
            white-space: nowrap;
        }}
        .quick-switch {{
            display: flex;
            gap: 8px;
            overflow-x: auto;
            padding-bottom: 2px;
        }}
        .quick-link {{
            flex: 0 0 auto;
            border: 1px solid var(--border);
            background: var(--surface);
            color: var(--muted);
            border-radius: 999px;
            padding: 10px 14px;
            cursor: pointer;
            transition: border-color 0.2s ease, color 0.2s ease, background 0.2s ease;
        }}
        .quick-link:hover {{
            border-color: var(--accent);
            color: var(--text);
        }}
        .quick-link.active {{
            background: var(--accent-soft);
            border-color: rgba(37, 99, 235, 0.2);
            color: var(--accent);
        }}
        .frame-shell {{
            flex: 1;
            min-height: 0;
            background: var(--surface);
            border: 1px solid var(--border);
            border-radius: 24px;
            overflow: hidden;
            box-shadow: var(--shadow);
            display: flex;
            flex-direction: column;
        }}
        .frame-shell iframe {{
            border: none;
            width: 100%;
            height: 100%;
            flex: 1;
            background: var(--surface);
        }}
        .missing-file {{
            display: none;
            height: 100%;
            padding: 48px 28px;
            color: var(--danger);
            text-align: center;
            align-items: center;
            justify-content: center;
            flex-direction: column;
            gap: 10px;
        }}
        .missing-file h2 {{
            font-size: 1.1rem;
        }}
        .missing-file p {{
            max-width: 42ch;
            color: var(--muted);
            line-height: 1.5;
        }}
        @media (max-width: 980px) {{
            body {{
                flex-direction: column;
            }}
            .topbar {{
                display: flex;
            }}
            .sidebar-toggle {{ display: none; }}
            .sidebar {{
                position: fixed;
                top: 0;
                left: 0;
                bottom: 0;
                transform: translateX(-100%);
                transition: transform 0.24s ease;
                z-index: 200;
                width: min(92vw, var(--sidebar-w));
                box-shadow: var(--shadow);
            }}
            .sidebar.open {{
                transform: translateX(0);
            }}
            .content {{
                height: calc(100dvh - var(--topbar-h));
            }}
            .content-shell {{
                padding: 14px 14px 16px;
            }}
            .frame-shell {{
                flex: 1;
            }}
            body.mobile-header-hidden .topbar,
            body.mobile-header-hidden .context-bar,
            body.mobile-header-hidden .quick-switch {{ display: none; }}
            body.mobile-header-hidden .content {{ height: 100dvh; }}
            .context-bar {{
                flex-direction: column;
                align-items: stretch;
            }}
            .context-side {{
                align-items: stretch;
            }}
            .update-badge {{
                white-space: normal;
            }}
        }}
        @media (max-width: 720px) {{
            .context-head h2 {{
                font-size: 1.12rem;
            }}
            .quick-link {{
                padding: 9px 12px;
                font-size: 0.84rem;
            }}
        }}
    </style>
</head>
<body>
    <div class="topbar">
        <button class="hamburger" onclick="toggleSidebar()" aria-label="Abrir navegação">Menu</button>
        <div class="topbar-title">
            <strong id="topbar-title">{first_title}</strong>
            <span id="topbar-desc">{first_desc}</span>
        </div>
        <button class="theme-toggle" onclick="toggleTheme()" title="Alternar tema">Tema</button>
    </div>

    <div class="overlay" id="overlay" onclick="closeSidebar()"></div>

    <aside class="sidebar" id="sidebar">
        <div class="brand">
            <div class="brand-kicker">BOVA11 Analytics</div>
            <h1>Painel quantitativo unificado</h1>
            <p>Leitura rápida do dia, estrutura por vencimento e módulos de apoio organizados por tarefa.</p>
        </div>
        <div class="menu">
            {"".join(menu_sections)}
        </div>
    </aside>

    <main class="content">
        <div class="content-shell">
            <section class="context-bar">
                <button class="sidebar-toggle" id="sidebar-toggle" onclick="toggleDesktopSidebar()" aria-controls="sidebar" aria-expanded="true" aria-label="Ocultar menu" title="Ocultar menu">☰</button>
                <div class="context-meta">
                    <div class="context-kicker" id="context-group">Hoje</div>
                    <div class="context-head">
                        <h2 id="context-title">{first_title}</h2>
                        <span class="context-time mono">Atualizado em {update_label}</span>
                    </div>
                    <p class="context-desc" id="context-desc">{first_desc}</p>
                </div>
                <div class="context-side">
                    <div class="update-badge mono" title="Momento em que os dados do dashboard foram gerados">{update_label}</div>
                    <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" title="Alternar tema">Tema</button>
                </div>
            </section>

            <div class="quick-switch">
                {"".join(quick_links)}
            </div>

            <section class="frame-shell">
                <iframe id="main-frame" src="{first_file}?v={html.escape(update_label)}" onload="checkIframe()"></iframe>
                <div id="error-msg" class="missing-file">
                    <h2>Arquivo não encontrado</h2>
                    <p>Este módulo pode não ter sido processado ainda ou o nome do arquivo gerado está incorreto.</p>
                </div>
            </section>
        </div>
    </main>

    <script>
        const MODULES = {modules_json};
        const DASHBOARD_VERSION = {json.dumps(update_label)};
        const MODULE_MAP = Object.fromEntries(MODULES.map(module => [module.file, module]));

        function setActiveModule(url) {{
            document.querySelectorAll('.menu-item, .quick-link').forEach(el => {{
                el.classList.toggle('active', el.dataset.file === url);
            }});
        }}

        function updateContext(url) {{
            const module = MODULE_MAP[url];
            if (!module) return;
            document.title = `BOVA11 - ${{module.title}}`;
            document.getElementById('context-group').textContent = module.group;
            document.getElementById('context-title').textContent = module.title;
            document.getElementById('context-desc').textContent = module.description;
            document.getElementById('topbar-title').textContent = module.title;
            document.getElementById('topbar-desc').textContent = module.description;
        }}

        function loadPage(url) {{
            const frame = document.getElementById('main-frame');
            document.body.classList.remove('mobile-header-hidden');
            document.getElementById('error-msg').style.display = 'none';
            frame.style.display = 'block';
            frame.src = `${{url}}?v=${{encodeURIComponent(DASHBOARD_VERSION)}}`;
            setActiveModule(url);
            updateContext(url);
            closeSidebar();
        }}

        function toggleGroup(groupId) {{
            const toggle = document.querySelector(`.group-toggle[data-group="${{groupId}}"]`);
            const group = document.getElementById(`group-${{groupId}}`);
            if (!toggle || !group) return;
            const expanded = group.classList.toggle('expanded');
            toggle.classList.toggle('expanded', expanded);
            toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        }}

        function toggleSidebar() {{
            const sidebar = document.getElementById('sidebar');
            const open = sidebar.classList.toggle('open');
            document.getElementById('overlay').classList.toggle('open', open);
        }}

        function closeSidebar() {{
            document.getElementById('sidebar').classList.remove('open');
            document.getElementById('overlay').classList.remove('open');
        }}

        function toggleDesktopSidebar() {{
            const collapsed = document.body.classList.toggle('sidebar-collapsed');
            const button = document.getElementById('sidebar-toggle');
            button.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
            button.setAttribute('aria-label', collapsed ? 'Mostrar menu' : 'Ocultar menu');
            button.title = collapsed ? 'Mostrar menu' : 'Ocultar menu';
        }}

        function watchMobileHeaderScroll(doc) {{
            const view = doc.defaultView;
            let previousY = view.scrollY;
            view.addEventListener('scroll', () => {{
                if (!window.matchMedia('(max-width: 980px)').matches) {{
                    document.body.classList.remove('mobile-header-hidden');
                    return;
                }}
                const currentY = view.scrollY;
                if (currentY <= 0) document.body.classList.remove('mobile-header-hidden');
                else if (currentY > previousY + 4) document.body.classList.add('mobile-header-hidden');
                else if (currentY < previousY - 4) document.body.classList.remove('mobile-header-hidden');
                if (Math.abs(currentY - previousY) > 4) previousY = currentY;
            }}, {{ passive: true }});
        }}

        function checkIframe() {{
            const iframe = document.getElementById('main-frame');
            try {{
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                if (!doc || !doc.body || doc.body.innerHTML.trim() === '') {{
                    iframe.style.display = 'none';
                    document.getElementById('error-msg').style.display = 'flex';
                    return;
                }}
                doc.querySelectorAll('#theme-toggle, #theme-btn, button.theme-toggle, button.theme-btn').forEach(btn => {{
                    btn.style.display = 'none';
                }});
                watchMobileHeaderScroll(doc);
                syncThemeToIframe();
                requestAnimationFrame(syncThemeToIframe);
            }} catch(e) {{
                // Ignorar quando o contexto não permitir inspeção do iframe.
            }}
        }}

        function refreshCurrentFrame() {{
            const iframe = document.getElementById('main-frame');
            if (!iframe || iframe.style.display === 'none') return;
            const currentSrc = iframe.getAttribute('src');
            if (!currentSrc) return;
            iframe.setAttribute('src', currentSrc);
        }}

        function syncThemeToIframe() {{
            try {{
                const iframe = document.getElementById('main-frame');
                if (!iframe || !iframe.contentWindow) return;
                const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
                iframe.contentWindow.postMessage({{ type: 'bova11-theme', dark: isDark }}, '*');
            }} catch(e) {{}}
        }}

        (function applySavedTheme() {{
            try {{
                const saved = localStorage.getItem('bova11-theme');
                if (saved === 'dark') {{
                    document.documentElement.setAttribute('data-theme', 'dark');
                }} else {{
                    document.documentElement.removeAttribute('data-theme');
                }}
            }} catch(e) {{}}
        }})();

        function toggleTheme() {{
            const html = document.documentElement;
            const next = html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
            if (next === 'light') html.removeAttribute('data-theme');
            else html.setAttribute('data-theme', 'dark');
            try {{ localStorage.setItem('bova11-theme', next); }} catch(e) {{}}
            syncThemeToIframe();
        }}
    </script>
</body>
</html>'''

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    index_path = os.path.join(OUTPUT_DIR, 'index.html')
    with open(index_path, 'w', encoding='utf-8') as f:
        f.write(html_content)
    return index_path

# ═══════════════════════════════════════
# MAIN
# ═══════════════════════════════════════
def main():
    print()
    print("=" * 65)
    print("  BOVA11 RUNNER — Orquestrador de Scripts")
    print("=" * 65)

    # 1) Auto-detect de datas
    print("\n  🔍 Detectando datas nos arquivos CSV...")
    TAG_D, TAG_D1 = discover_dates()

    if not TAG_D:
        print("  ❌ Nenhum arquivo de fechamento encontrado no diretório.")
        sys.exit(1)

    DATA_D  = format_date_label(TAG_D)
    DATA_D1 = format_date_label(TAG_D1) if TAG_D1 else "N/A"

    print(f"  📅 D-1 : {TAG_D1 or 'N/A'} ({DATA_D1})")
    print(f"  📅 D   : {TAG_D} ({DATA_D})")

    if not TAG_D1:
        print("\n  ⚠️  Apenas uma data encontrada. São necessários D-1 e D.")
        sys.exit(1)

    missing_deps = find_missing_deps()
    if missing_deps:
        print()
        print("  ⚠️  Dependências Python ausentes neste interpretador:")
        for pkg, scripts in sorted(missing_deps.items()):
            print(f"     - {pkg}: {', '.join(scripts)}")
        print("  💡 Instale no mesmo Python do runner:")
        print(f"     {sys.executable} -m pip install numpy pandas scipy yfinance")

    # 2) Coletar todos os inputs de uma vez
    print()
    print("=" * 65)
    print("  CONFIGURAÇÃO DE SPOT")
    print("=" * 65)
    SPOT_D1 = ask_spot(f"Spot de D-1 ({DATA_D1})")
    SPOT_D  = ask_spot(f"Spot de D   ({DATA_D})")

    strike_min, strike_max = ask_strike_range()

    print()
    print("=" * 65)
    print(f"  Configuração confirmada:")
    print(f"    D-1 : {DATA_D1}  |  Spot D-1 : {SPOT_D1:.2f}")
    print(f"    D   : {DATA_D}   |  Spot D   : {SPOT_D:.2f}")
    print(f"    Strikes Skew   : {strike_min} – {strike_max}")
    print("=" * 65)

    upsert_spot_history(SPOT_HISTORY_FILE, ref_date=tag_to_iso_date(TAG_D1), ref_tag=TAG_D1, spot=SPOT_D1)
    upsert_spot_history(SPOT_HISTORY_FILE, ref_date=tag_to_iso_date(TAG_D), ref_tag=TAG_D, spot=SPOT_D)
    print(f"    Spot history   : {SPOT_HISTORY_FILE}")
    print("=" * 65)

    # 3) Definir stdin de cada script
    #
    # bova11_auto.py          → pede: spot D (único valor)
    # bova11_insights_auto.py → pede: spot D (único valor)
    # bova11_skew_history.py  → pede: strike min (Enter), strike max (Enter)
    # bova11_trade_score_james.py → pede: spot D-1, depois spot D
    #
    stdin_map = {
        "bova11_auto.py":                   f"{SPOT_D}\n",
        "bova11_insights_auto.py":          f"{SPOT_D}\n",
        "bova11_skew_history.py":           f"{strike_min}\n{strike_max}\n",
        "bova11_trade_score_james.py":      f"{SPOT_D1}\n{SPOT_D}\n",
        "bova11_quant_institucional.py":    "",  # autônomo, sem input
        "bova11_tex_dex_vex.py":            f"{SPOT_D}\n{SPOT_D1}\n",
    }

    # Scripts que usam argparse CLI (não stdin)
    ref_date = tag_to_iso_date(TAG_D)
    args_map = {
        "bova11_oi_stats_light.py": [
            f"--data-dir={DATA_DIR}",
            f"--output={os.path.join(OUTPUT_DIR, 'bova11_oi_stats_light.html')}",
            f"--ref-date={ref_date}",
            f"--ref-tag={TAG_D}",
            f"--spot={SPOT_D}",
        ],
        "bova11_max_pain.py": [
            f"--data-dir={DATA_DIR}",
            f"--output={os.path.join(OUTPUT_DIR, 'bova11_max_pain.html')}",
            f"--ref-date={ref_date}",
            f"--ref-tag={TAG_D}",
            f"--spot={SPOT_D}",
        ],
        "bova11_gravity.py": [
            f"--data-dir={DATA_DIR}",
            f"--output={os.path.join(OUTPUT_DIR, 'bova11_gravity_map.html')}",
            f"--ref-date={ref_date}",
            f"--ref-tag={TAG_D}",
            f"--spot={SPOT_D}",
        ],
        "bova11_market_gamma.py": [
            f"--data-dir={DATA_DIR}",
            f"--output={os.path.join(OUTPUT_DIR, 'bova11_market_gamma.html')}",
            f"--ref-date={ref_date}",
            f"--ref-tag={TAG_D}",
            f"--spot={SPOT_D}",
            f"--spot-history-file={SPOT_HISTORY_FILE}",
            f"--strike-min={strike_min}",
            f"--strike-max={strike_max}",
        ],
        "bova11_diagnostico_4d.py": [
            f"--data-dir={DATA_DIR}",
            f"--output={os.path.join(OUTPUT_DIR, 'bova11_diagnostico_4d.html')}",
            f"--ref-date={ref_date}",
            f"--ref-tag={TAG_D}",
            f"--spot-d={SPOT_D}",
            f"--spot-d1={SPOT_D1}",
        ],
        "bova11_chain_oi_changes.py": [
            f"--data-dir={DATA_DIR}",
            f"--output={os.path.join(OUTPUT_DIR, 'bova11_chain_oi_changes.html')}",
            f"--ref-date={ref_date}",
            f"--ref-tag={TAG_D}",
            f"--previous-tag={TAG_D1}",
        ],
    }

    # 4) Rodar cada script em sequência
    errors = []
    for step, script_name, description in SCRIPTS:
        script_path = os.path.join(SCRIPTS_DIR, script_name)

        print()
        print(f"  {'─' * 60}")
        print(f"  🚀 [{step}] {script_name}")
        print(f"       {description}")
        print(f"  {'─' * 60}")

        if not os.path.exists(script_path):
            print(f"  ❌ Arquivo não encontrado: {script_path}")
            errors.append(script_name)
            continue

        if script_name in args_map:
            returncode = run_script_with_args(script_path, args_map[script_name])
        else:
            stdin_input = stdin_map.get(script_name, "")
            returncode = run_script(script_path, stdin_input)

        if returncode == 0:
            print(f"\n  ✅ [{step}] {script_name} — CONCLUÍDO")
        else:
            print(f"\n  ❌ [{step}] {script_name} — ERRO (código {returncode})")
            errors.append(script_name)

    # 5) Resumo final
    from datetime import datetime
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    update_label = generated_at if not errors else f"{generated_at} · atualização parcial"
    index_path = gerar_index_html(TAG_D1, TAG_D, DATA_D1, DATA_D, update_label)
    finalize_dashboard_run(errors)
    return

if __name__ == '__main__':
    main()
