# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What This Project Does

BOVA11 Options Analysis — a Python system that reads daily B3 (Brazilian stock exchange) options data from CSV files and generates interactive HTML dashboards. Produces **20 types of analysis** across a unified responsive sidebar dashboard (20 modules, 1/20–20/20). All pages support **dark/light mode toggle** (button fixed top-right, persisted via `localStorage('bova11-theme')`).

## Running the System

**Full run (recommended):**
```bash
cd /Users/leonardocarneiro/Desktop/Dashboad\ BOVA11
python3 bova11_runner.py
```

The runner auto-detects dates from CSVs in `data/`, then prompts for:
1. Spot price D-1
2. Spot price D
3. Strike range for Skew History (default **140–180**, press Enter to accept)

The runner persists the manual spots to `history/bova11_spot_history.json` and passes them to other modules via CLI flags when applicable.

Modules 10–20 receive all inputs via CLI args (`--data-dir`, `--output`, `--ref-date`, `--ref-tag`, etc.) and require no stdin.

**Individual module execution:**
```bash
python3 scripts/bova11_auto.py                    # Module 1:  Dashboard (OI, GEX, MaxPain)
python3 scripts/bova11_insights_auto.py           # Module 2:  Narrative insights
python3 scripts/bova11_skew_history.py            # Module 3:  IV/Skew history
python3 scripts/bova11_convexity.py               # Module 4:  Vanna/Gamma/Charm decomposition
python3 scripts/bova11_trade_score_james.py       # Module 5:  Trade Score & Bias
python3 scripts/bova11_quant_institucional.py     # Module 6:  Live Quant Metrics (RevL/MoM/RS)
python3 scripts/bova11_tex_dex_vex.py             # Module 7:  TEX/DEX/VEX (Greek Exposure)
python3 scripts/bova11_flow_history.py            # Module 8:  OI Flow History
python3 scripts/bova11_skew_prediction.py         # Module 9:  Skew Prediction signal
python3 scripts/bova11_oi_stats_light.py          # Module 10: OI Stats — Statistical Distribution
python3 scripts/bova11_max_pain.py                # Module 11: Max Pain Relevance Indicator
python3 scripts/bova11_gravity.py                 # Module 12: Gravitational Map (μOI/μGEX/μDEX/Convergence)
python3 scripts/bova11_hunter_walls.py            # Module 13: Hunter Walls — Volume por Strike
python3 scripts/bova11_gex_history.py             # Module 14: GEX Histórico — Evolução do Gamma Exposure
python3 scripts/bova11_historical_data.py         # Module 15: Histórico de Mercado — OI/Volume/IV/MaxPain/GEX/DEX
python3 scripts/bova11_market_gamma.py            # Module 16: Market Gamma — Curva líquida agregada e por vencimento
python3 scripts/bova11_diagnostico_4d.py          # Module 17: Diagnóstico 4D — Cenário GEX/DEX/VEX/TEX
python3 scripts/bova11_bandas_vol.py              # Module 18: Bandas de Volatilidade — GARCH + IV
python3 scripts/bova11_demand_flow.py             # Module 19: Demand Flow v4 — ADI/PDI/IDS/DFP
python3 scripts/bova11_arquivos.py                # Module 20: Arquivos — Relatório Markdown + Histórico para IA
```

**Dependencies:**
- **Modules 1-5, 7-9:** Python 3 stdlib only (`os`, `sys`, `re`, `glob`, `json`, `math`, `subprocess`, `datetime`)
- **Modules 6, 9:** Require `yfinance`, `pandas`, `numpy` — install via: `pip3 install yfinance pandas numpy`
- **Modules 10-12:** Use `--data-dir`, `--output`, `--ref-date`, `--ref-tag` CLI args (argparse); Python 3 stdlib only
- **Module 13:** Same as 10-12, plus `--spot` (float)
- **Module 14:** Uses `--data-dir`, `--output`, `--ref-date`; stdlib only; optional `--spot`/`--spot-history-file` for consistency without network
- **Module 15:** Uses `--data-dir`, `--output`, `--ref-date`, `--ref-tag`, `--history-file`, `--spot-history-file`; stdlib only; spot-dependent fields become `N/A` if spot cannot be resolved
- **Module 16:** Uses `--data-dir`, `--output`, `--ref-date`, `--ref-tag`, `--spot`, `--spot-history-file`; stdlib only; `yfinance` optional fallback (no synthetic spot)
- **Module 17:** Uses `--data-dir`, `--output`, `--ref-date`, `--ref-tag`, `--spot-d`, `--spot-d1`; stdlib only
- **Module 18:** Same as 10-12 plus `--spot`; requires `yfinance`, `pandas`, `numpy`; `arch` optional (fallback to EWMA)
- **Module 19:** Uses `--data-dir`, `--output`, `--ref-date`, `--ref-tag`, `--spot-d`, `--spot-d1`; stdlib only
- **Module 20:** Uses `--output-dir`, `--output`, `--ref-date`, `--spot-d`, `--spot-d1`; stdlib only

## Architecture

```
Dashboad BOVA11/
├── bova11_runner.py        ← Main orchestrator — single entry point (20 modules, 1/20–20/20)
├── scripts/                ← 20 independent analysis modules + shared helpers
│   ├── bova11_auto.py
│   ├── bova11_insights_auto.py
│   ├── bova11_skew_history.py
│   ├── bova11_convexity.py
│   ├── bova11_trade_score_james.py
│   ├── bova11_quant_institucional.py   ← Live metrics via yfinance
│   ├── bova11_tex_dex_vex.py           ← Greek exposure by strike
│   ├── bova11_flow_history.py          ← OI entry/exit history
│   ├── bova11_skew_prediction.py       ← Directional signal from ΔSkew
│   ├── bova11_historical_data.py       ← Historical consolidated market metrics
│   ├── bova11_market_gamma.py          ← Net gamma curve by strike (spot-aware)
│   ├── bova11_oi_stats_light.py        ← OI statistical distribution (argparse)
│   ├── bova11_max_pain.py              ← Max Pain relevance indicator (argparse)
│   ├── bova11_gravity.py               ← Gravitational map μOI/μGEX/μDEX (argparse)
│   ├── bova11_hunter_walls.py          ← Volume por strike, butterfly chart, PCR (argparse)
│   ├── bova11_gex_history.py           ← GEX histórico, gamma flip, regime changes (argparse)
│   ├── bova11_diagnostico_4d.py        ← Diagnóstico 4D: 16 cenários GEX/DEX/VEX/TEX (argparse)
│   ├── bova11_bandas_vol.py            ← Bandas de vol GARCH+IV, IV Rank, Expected Move (argparse)
│   ├── bova11_demand_flow.py           ← Demand Flow v4: changes by contract (argparse)
│   └── bova11_arquivos.py              ← Extração de dados, geração de MD para IA e frontend IA viewer
├── data/                   ← Input: B3 CSV files (place here daily)
├── output/                 ← Generated HTML reports + index.html dashboard
├── arquivos/               ← Histórico diário em Markdown formatado pesado para contexto de IA
└── history/                ← Cumulative JSON (DO NOT DELETE)
    ├── bova11_spot_history.json        ← Manual spot (runner truth source)
    ├── bova11_skew_history.json
    ├── bova11_flow_history.json
    ├── bova11_skew_prediction.json
    └── bova11_demand_flow.json
```

**Data flow:**
1. Place CSV files for D-1 and D into `data/`
2. Run `bova11_runner.py` — it discovers dates automatically by file modification time
3. HTMLs are written to `output/`; `output/index.html` is a unified responsive sidebar dashboard

**Date auto-discovery:** Scripts scan `data/` for files matching `venc * fechamento (*).csv`, extract date tags (e.g. `5mar`, `6mar`, `25posmar`), sort by file modification time, and use the two most recent as D-1 and D. Tags with `pos`/`pre` suffix (e.g. `25posmar`) are normalized by stripping the suffix before date conversion.

**Resilience against empty data:**
If an expiration is discovered but its CSV file contains no valid options data (0 strikes), the processing modules gracefully detect the empty `DataFrame` and skip the calculation for that specific expiration, preventing execution crashes (e.g. `KeyError: 'strike'`).

**Resilience against chart disorder (strike axis):**
Modules 11 (`bova11_max_pain.py`) and 12 (`bova11_gravity.py`) now explicitly sort datasets by numeric `strike` before serializing chart payloads. This prevents mixed/non-monotonic X-axis order in Chart.js when source CSV rows arrive unsorted.

**Resilience against mixed maturities inside the same CSV:**
Some B3 closing CSVs may contain rows from multiple expirations in the same file (for example, `17 abr Mensal` in `13abr` included `W4`, `W5` and `W2` tickers in addition to the monthly chain). This previously caused:
- duplicated strikes in GEX/DEX charts;
- contamination of `net_gex`, `net_dex`, `μGEX`, `μDEX` and gamma flip;
- silent distortion of OI statistics for `OI Stats`.

The fix now uses `exp_date` as the source of truth to derive the expected ticker family:
- **Monthly expirations:** same month code, no `W1..W5` suffix;
- **Weekly expirations:** same month code + weekly suffix derived from `ceil(day/7)` of the actual expiration date.

Modules 10 (`bova11_oi_stats_light.py`), 11 (`bova11_max_pain.py`) and 12 (`bova11_gravity.py`) now:
- preserve `call_ticker` / `put_ticker` when parsing the close CSV;
- filter rows to the correct expiration family using `scripts/bova11_expiry_family.py`;
- consolidate repeated rows by numeric `strike` before computing metrics and serializing charts.

This is important because the **filename label is not always trustworthy**. Example: `venc 24 abr W2 fechamento (13abr).csv` actually maps to the `D/W4` ticker family when derived from the real expiration date `2026-04-24`.

**Volume CSV column alignment fix (Module 11):**
`bova11_max_pain.py` now reads `strike` from column **5** of the B3 Volume CSV (instead of column 4, which is `call_ask`). Before this correction, merged volume often collapsed to zero and distorted the volume-activity score.

## Key Configuration Points

Each script has a `VENC_DATES` dict that maps expiry labels to calendar dates — **update this when new expirations are added**:
```python
VENC_DATES = {
    "27 mar W4":     "2026-03-27",
    "2 abr W1":      "2026-04-02",
    "10 abr W2":     "2026-04-10",
    "17 abr Mensal": "2026-04-17",
    "15 mai Mensal": "2026-05-15",
}
```

Also in `scripts/bova11_auto.py`:
```python
ANO = "2026"           # Current year
GEX_STRIKE_MIN = 160   # GEX analysis lower bound
GEX_STRIKE_MAX = 200   # GEX analysis upper bound
```

Strike range for Skew History (in `scripts/bova11_skew_history.py` and `bova11_runner.py`):
```python
STRIKE_MIN_DEFAULT = 140
STRIKE_MAX_DEFAULT = 180
```

## CSV Input Format

Files must follow this naming pattern:
```
venc 27 mar W4 fechamento (25mar).csv
venc 27 mar W4 fechamento (25mar Volume).csv
venc 27 mar W4 fechamento (25posmar).csv        ← pós-mercado suffix supported
venc 27 mar W4 fechamento (25posmar Volume).csv
```
Where `25mar` / `25posmar` is the date tag and "Volume" files contain volume data alongside the OI files.

**About filename warnings (`Nome fora do padrão esperado`):**
- If you copy/duplicate files (e.g. `... copy.csv`) or add extra suffixes outside the pattern above, some modules will still try to parse them, but date discovery/backtests may treat them as out-of-spec and emit warnings.
- Best practice: keep filenames strictly in the pattern above so `ref-tag` extraction, D-1/D selection, and historical alignment remain consistent.

**Important parser caveat:**
- Some B3 CSVs can contain tickers that do not "look like" the filename label (e.g. weekly code `W4` appearing inside a file labeled `W2`). Filtering is conservative: it enforces the correct month family and (for `Mensal`) excludes weekly tickers.
- Volume CSVs use the strike in column **5** (`[5]=strike`), not column 4.

## Dark/Light Mode

All generated HTML pages support a dark/light mode toggle:
- **Default:** light mode (`:root` palette: `--bg:#FAFAF8`, `--t1:#1A1A18`, etc.)
- **Dark mode:** `[data-theme="dark"]` on `<html>` (palette: `--bg:#0d1117`, `--t1:#c9d1d9`, etc.)
- **Toggle button:** fixed top-right (`#theme-toggle`), emoji 🌙/☀️
- **Persistence:** `localStorage` key `bova11-theme`
- **CSS pattern:** `:root { }` then `[data-theme="dark"] { }` — Chart.js colors are hardcoded in JS and intentionally not affected by theme

## Output

After a successful run, `output/` contains:
- `index.html` — **responsive** unified dashboard with 20-tab sidebar navigation
  - Desktop: fixed sidebar on the left (collapsible)
  - Mobile (≤768px): hamburger button (☰) in topbar, sidebar slides in as overlay
  - Mobile UX hardening: sidebar supports touch momentum scroll (`-webkit-overflow-scrolling: touch`), uses `100dvh` viewport sizing, includes safe-area bottom padding, and ignores persisted desktop `sidebarCollapsed` state on small screens (prevents hidden final tabs)
- `bova11_rankings_pro_<D1>_vs_<D>.html` + `bova11_gex_pro_<D1>_vs_<D>.html` — Options OI/GEX/MaxPain
- `bova11_insights_<D1>_vs_<D>.html` — Narrative analysis
- `bova11_skew_history.html` — IV/Skew history (Chart.js); data table shows last 7 days newest-first, expandable
- `bova11_flow_history.html` — OI entry/exit flow history with calendar filter
- `bova11_convexity_<D1>_vs_<D>.html` — Greeks decomposition
  - The decomposition sanity-check identity `ΔDelta ≈ gamma + vanna + charm + residual` should close to ~0 within floating-point tolerance.
  - Seeing a residual error around `1e-17` is normal IEEE-754 noise (effectively zero), not a mathematical inconsistency.
- `bova11_trade_score_<D1>_vs_<D>.html` — Trade scoring & bias
- `bova11_quant_institucional.html` — Live quantitative metrics (3 years of BOVA11 history)
  - 6 metric cards (Close, RevL, MoM Z-Score, Força Relativa, Trend, Vol Regime, Mode)
  - 4 interactive synchronized charts; zoom-enabled; downloads via `yfinance` (BOVA11.SA + IBOV)
- `bova11_tex_dex_vex_<D1>_vs_<D>.html` — TEX/DEX/VEX Greek exposure by strike
  - Charts scaled in **millions (M/B)** on Y-axis
  - Bars: bright green (calls) / red (puts) with borders; Net line in gold (#f0c040)
  - Per-expiry tab filter + aggregated view
- `bova11_skew_prediction.html` — Directional signal from ΔSkew = ΔPut − ΔCall
  - Direction uses `sinal_ajustado` vs thresholds: `BULLISH` if `signal_adjusted <= bull_threshold`, `BEARISH` if `>= bear_threshold`, else `NEUTRO`.
  - Strength uses only the Z-Score magnitude (`FORTE/MODERADO/FRACO`), so `NEUTRO (FORTE)` is expected when there is strong statistical deviation but still no directional trigger.
  - Historical accuracy tracked; data table newest-first, expandable (default 7 rows)
  - History persisted in `history/bova11_skew_prediction.json`
- `bova11_oi_stats_light.html` — OI statistical distribution
  - Before computing statistics, rows are filtered to the correct expiration family and consolidated by numeric `strike`
- `bova11_max_pain.html` — Max Pain relevance indicator
  - Strike-series used in chart payloads are sorted ascending by numeric `strike`
  - GEX payloads are filtered to the correct expiration family and aggregated by `strike` before rendering
  - Volume merge now uses the correct strike column from the Volume CSV, avoiding false-zero activity
- `bova11_gravity_map.html` — Gravitational map μOI/μGEX/μDEX/Convergence
  - OI/GEX/DEX/max-pain chart payloads are sorted by numeric `strike` before rendering
  - GEX/DEX series are filtered to the correct expiration family and aggregated by `strike`, preventing repeated strikes and contaminated centers
- `bova11_hunter_walls.html` — Volume por strike, butterfly chart calls vs puts (horizontal bars, `indexAxis:'y'`), PCR, Top-5
  - Chart tooltip uses `interaction: { mode: 'y' }` (not `'index'`) for horizontal bars
  - Volume table bars use `.vol-bar-track` fixed-width (90px) wrapper to prevent overflow
- `bova11_gex_history.html` — Evolução do GEX nos últimos 5 dias úteis, gamma flip, regime changes
- `bova11_historical_data.html` — Histórico de Mercado consolidado (spot-dependent fields become `N/A` if spot cannot be resolved)
- `bova11_market_gamma.html` — Curva de gamma líquido (spot-aware; uses manual spot when provided)
- `bova11_diagnostico_4d.html` — Diagnóstico 4D: 4 sinais binários → 16 cenários → estratégia
- `bova11_bandas_vol.html` — Bandas de volatilidade IV + GARCH, IV Rank/Percentil, Expected Move calculator
- `bova11_demand_flow.html` — Demand Flow v4 (Module 19)
  - Five change components, robust score, contract/session coverage and per-expiry ranking
  - History: `history/bova11_demand_flow.json`; legacy records remain preserved
- `bova11_arquivos.html` — Arquivos & IA Viewer (Module 18)
  - Exibe o histórico de arquivos Markdown gerados dentro de `arquivos/`
  - Contém todo o extrato JSON truncado das abas, mais uma seção de conceitos e matemáticas para a IA.
  - Botão de Copy to Clipboard integrado para colar direto no ChatGPT/Codex e botão de download.

The `output/` directory is a separate git repo pointing to GitHub Pages at `https://Leocarneiroo.github.io/bova11-dashboard/`. The runner offers to push after a successful run.

## Skew Prediction — Accuracy Reference (as of 25/mar/2026)

| Sinal | Acertos | Erros | Total | % Acerto |
|-------|---------|-------|-------|----------|
| ALTA  | 5       | 1     | 6     | ~83%     |
| QUEDA | 5       | 4     | 9     | ~56%     |
| **Combinado** | **10** | **5** | **15** | **~67%** |

Sinais de ALTA acertam mais e com movimentos menores (+0.8% médio). Sinais de QUEDA acertam menos mas com movimentos maiores quando acertam (-2.2% médio).

## Module 19: Demand Flow v4

Current engine: `demand-change-v4`, replacing legacy ADI/PDI/IDS/DFP formulas.
It compares identical contracts in D/D-1: changes in put-minus-call IV skew,
average IV, premium-notional imbalance, OI imbalance, and negative net GEX.
Skew/IV require |delta| between 0.10 and 0.90 in both sessions. Notional is
mid × CSV volume. GEX is gamma × OI × spot² / 100 (call+/put−). Charm/Vanna
proxies are excluded. Weights: skew 0.15/0.82, IV 0.15/0.82, premium 0.20/0.82,
OI 0.20/0.82, GEX 0.12/0.82. Robust z-scores are clipped to ±3. Economic weight:
exp(-10 × mean absolute moneyness) × usable OI. Active weights are renormalized
by coverage; below 50% the score is unavailable. Missing values stay null.
Descriptive heuristic, not certified institutional/dealer positioning.
Legacy history remains preserved and is not directly comparable with v4.

## Calculation integrity

- Unknown OI/IV/Greeks stay null; observed zero is valid. Complete totals require
  full contract/expiration coverage; partial contributions have explicit labels.
- Gamma flip is a sign-changing root of GEX(spot), not a strike crossing.
  Supplied static GEX and modeled regimes are displayed separately.
- IV Rank uses comparable ATM IV over 365 days and ≥20 prior sessions.
  Constant/insufficient series are unavailable. Prices stop at ref_date.
- Tape quantity units are uncertified: no presumed ×100 conversion or delta
  in underlying units. Raw price × quantity is an uncertified proxy.
- Repair: scripts/bova11_repair_history.py uses verified timestamp backups and
  atomic writes; records without sufficient local sources remain preserved.
- Bandas business-day horizons count Monday–Friday; B3 holidays unavailable
  (documented shortcut). Expired contracts must not roll to the next year.
