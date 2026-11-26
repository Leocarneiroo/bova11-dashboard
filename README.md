# BOVA11 Options Dashboard

Sistema em Python que lê os CSVs diários de opções da B3 e gera o dashboard quantitativo do BOVA11 — OI, GEX, Max Pain, skew, convexidade (Gamma/Vanna/Charm), trade score, fluxo e perfis estruturais por strike.

**Site publicado:** https://leocarneiroo.github.io/bova11-dashboard/

## Estrutura

Este é o único repositório do projeto. Ele tem duas branches:

| Branch | Conteúdo |
|---|---|
| `main` | código (`bova11_runner.py`, `scripts/`, `tests/`, `docs/`), dados de mercado (`data/`) e histórico acumulado (`history/`) |
| `gh-pages` | apenas o site gerado (HTML/CSS/JS), podado para as últimas sessões |

O `output/` local é um worktree da `gh-pages`: o runner gera ali e publica com `git push origin gh-pages`. O site é montado pelo workflow `Publish dashboard`, que valida o limite de 1 GB do GitHub Pages.

```
bova11_runner.py            ponto de entrada
scripts/                    12 módulos de análise + shared/helper + bova11_prune_site.py
tests/                      suíte de testes
data/                       CSVs diários da B3 (entrada)
history/                    JSONs acumulados (não apagar)
docs/                       documentação dos módulos
```

## Como rodar

```bash
python3 bova11_runner.py
```

O runner detecta as datas D-1 e D pelos CSVs em `data/`, pede os spots, roda os módulos em sequência, gera o `index.html` e pergunta se deve publicar no Pages.

Módulo individual:

```bash
python3 scripts/bova11_max_pain.py
```

## Testes

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

## Publicação

Cada execução publicada:

1. poda `output/` (últimas 5 sessões por série, sem `_debug_*`);
2. commita na `gh-pages`;
3. o workflow valida o tamanho e faz o deploy.

## Aviso

Conteúdo para estudo e análise. Não é recomendação de investimento.
