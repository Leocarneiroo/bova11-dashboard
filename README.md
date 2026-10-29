# BOVA11 Dashboard (site publicado)

Site estático com o painel quantitativo de opções do **BOVA11**, publicado no GitHub Pages:

👉 **https://leocarneiroo.github.io/bova11-dashboard/**

Este repositório guarda **apenas os arquivos HTML gerados**. O código-fonte que gera as páginas está em **[bova11-dashboard-code](https://github.com/Leocarneiroo/bova11-dashboard-code)**.

## O que tem no site

A página inicial (`index.html`) é o painel unificado do último pregão processado. As demais páginas são módulos de análise, muitas com histórico diário (comparação "dia anterior vs dia atual"):

- **Insights** (`bova11_insights_*.html`): leitura narrativa do dia.
- **Trade score** (`bova11_trade_score_*.html`) e **rankings** (`bova11_rankings_pro_*.html`).
- **GEX** (`bova11_gex_pro_*.html`, `bova11_gex_history.html`) e **Max Pain** (`bova11_max_pain.html`).
- **Perfis TEX / DEX / VEX** (`bova11_tex_dex_vex_*.html`).
- **Convexidade** (Gamma, Vanna, Charm): `bova11_convexity_*.html`.
- **Skew e volatilidade:** `bova11_skew_history.html`, `bova11_skew_prediction.html` e `bova11_bandas_vol.html`.
- **Fluxo e estatísticas de OI:** `bova11_demand_flow.html`, `bova11_flow_history.html`, `bova11_oi_stats.html`, `bova11_chain_oi_changes.html` e `bova11_tape_flow.html`.
- **Outros:** `bova11_diagnostico_4d.html`, `bova11_gravity_map.html`, `bova11_hunter_walls.html`, `bova11_market_gamma.html` e `bova11_quant_institucional.html`.
- `bova11_arquivos.html`: índice dos arquivos.

Arquivos de apoio: `shared.css`, `shared.js`, `bova11_skew_history_data.json`, a pasta `arquivos/` e o `.nojekyll`, que serve o site sem processamento do Jekyll.

## Como é atualizado

As páginas são geradas localmente pelo `bova11_runner.py` do repositório de código, a partir dos CSVs diários de opções da B3, e enviadas para cá. Este repositório não contém dados brutos nem scripts.

## Aviso

Conteúdo para estudo e análise. **Não é recomendação de investimento.**
