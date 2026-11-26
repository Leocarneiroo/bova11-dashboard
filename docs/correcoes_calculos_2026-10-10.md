# Correções dos cálculos BOVA11 — 10/10/2026

Implementação local do plano aprovado, cobrindo os 14 achados da auditoria de 09/10. As alterações locais anteriores foram preservadas. Nenhuma página foi publicada, nenhuma dependência foi adicionada e o motor de gregas não foi reescrito.

## Alterações

| Achados | Correção |
|---|---|
| 1 — Flow | Chaves e rankings preservam strikes decimais. Datas sem fonte suficiente são mantidas. OI e volume desconhecidos permanecem nulos. |
| 2 — Regime | O regime estático usa o sinal do GEX fornecido; o modelado usa GEX(S) no spot atual. Ausência de raiz não implica Long Gamma. |
| 3 — IV ausente | Removidos os fallbacks de IV de 20%, rank 50 e volatilidade zero para dados desconhecidos. ATM ponderado exige IV válida em todos os lados com OI positivo; zero observado continua válido. |
| 4 — IV Rank/Percentile | Série histórica de IV reconstruída de CSVs e spots manuais, com a mesma regra ATM e ponderação inversa por DTE. Uma observação por sessão, priorizando pós-mercado; janela de 365 dias, mínimo de 20 observações anteriores e amplitude positiva. |
| 5 — Referência temporal | Consulta e filtro dos preços limitados à data de referência, inclusive, antes de SMA, HV e GARCH/EWMA. |
| 6/7 — Cobertura | OI, IV e gregas ausentes não viram zero. OI observado zero permite exposição zero sem grega. OI desconhecido invalida o agregado completo. Inventário inclui Volume e vencimentos ativos da sessão anterior; contribuições parciais e cobertura são identificadas. |
| 8 — Duplicatas | Deduplicação por identidade do contrato antes de somar OI e exposições. Séries diferentes no mesmo strike continuam contribuindo. |
| 9 — Gamma flip | Curvas por spot usam grade comum e detector compartilhado de raízes com troca efetiva de sinal. Exibidas a raiz mais próxima, a lista de raízes e as situações “Indisponível”/“Sem cruzamento”. |
| 10 — Insights | Eliminado o corte 160–200 no cálculo. IV positiva é aumento, negativa é queda, ausência é indisponibilidade. Removida a atribuição automática a theta crush. |
| 11 — Estatística | Removidos a pseudoamostra, o p-valor KS e a conclusão formal de normalidade. Momentos, concentração e ajuste gaussiano permanecem descritivos. |
| 12 — Vencimentos | Vencimentos expirados são excluídos com o resolvedor comum; não são deslocados automaticamente para o próximo ano. Horizontes das Bandas são identificados em dias úteis. |
| 13 — PCR | Calls positivas permitem divisão normal. Somente puts recebem rótulo explícito; nenhum volume produz N/A. Sem infinito no JSON. |
| 14 — Tape | Removida a conversão presumida por 100. Delta equivalente é nulo em Python, JSON, totais e HTML enquanto a unidade não estiver certificada. Quantidade bruta e preço × quantidade recebem rótulos de unidade não certificada. |

Conceitos exportados para IA e documentação do Demand Flow v4 foram atualizados. O runner mantém os módulos atualmente habilitados e passa o histórico manual de spot às Bandas quando esse módulo é executado. O cartão de probabilidades fixas de reversão foi removido.

## Históricos e integridade

Backup inicial dos sete JSONs, verificado por SHA-256 antes da escrita:

`history/backups/calculation_fix_20261010_105818/manifest.json`

O reparador escreve primeiro em arquivo temporário, valida JSON sem NaN/infinito e substitui atomicamente. Cada arquivo alterado também recebe backup individual com timestamp.

- Flow: 543 registros de data/vencimento recalculados; 52 registros sem fonte suficiente preservados integralmente. O histórico resultante contém 119 datas.
- Histórico de Mercado: 119 sessões recalculadas com CSV e referência de spot disponíveis, incluindo sessões novas; nenhuma data existente removida.
- Controle de 25/mai, vencimento 17/jul: **165 strikes e OI total de 50.812.939**.
- Spot manual, Demand Flow, Skew, Skew Prediction e Tape permanecem byte a byte iguais ao backup inicial.
- A repetição do reparo produz os mesmos hashes e nenhuma nova substituição dos históricos.
- Resultados antigos do Tape não foram recalculados sem os workbooks originais; seus deltas são tratados como não certificados na leitura/exibição.

O relatório [reparo_historicos_calculos.json](reparo_historicos_calculos.json) lista registros recalculados/preservados, motivos, cobertura insuficiente e hashes de verificação. Executar novamente com:

```bash
rtk proxy python3 scripts/bova11_repair_history.py
```

## Validação

Os testes de regressão reproduzem perda de strikes, ausência versus zero, gregas incompletas, identidade de contratos, vencimentos sem fechamento, raízes sem troca de sinal, grade comum, IV parcial/constante/insuficiente, expiração/virada do ano, preços futuros, PCR indefinido e propagação de deltas nulos.

```bash
rtk proxy python3 -m unittest discover -s tests -v
```

**192 testes aprovados**, incluindo os existentes de gregas e Max Pain. Os módulos alterados também passaram por `py_compile`. O diff foi verificado com suporte a CRLF para preservar os finais de linha existentes.

Previews gerados em diretório temporário, fora de `output/`: Flow, Insights, Max Pain, Gravidade, OI Stats, OI Stats incompleto, Hunter Walls, Histórico de Mercado, Bandas, perfis de gregas, Tape e Premium. Os 24 scripts inline passaram pela checagem de sintaxe do Node; a conversão dos dados dos gráficos preserva nulos como lacunas e zero observado como zero. A inspeção no navegador confirmou rankings 79/79,5 distintos, N/A, cobertura parcial, regimes separados e curvas sem valores fabricados; sem erros de console nas páginas inspecionadas.

As Bandas foram executadas também na `.venv` existente com download real de preços: EWMA disponível mesmo sem IV atual; bandas e sinais permaneceram indisponíveis. O filtro contra preços posteriores à referência foi validado por teste controlado.

## Limites

- `arch` não está instalado; o ajuste GARCH real não foi executado. EWMA e o fallback por falha de GARCH foram verificados sem adicionar dependências.
- Dias úteis das Bandas significam segunda a sexta; não incluem calendário de feriados da B3.
- A unidade de “Quantidade” do Tape continua não certificada.
- Cobertura de campos OI não estima o peso econômico dos valores desconhecidos.
- Correção matemática e dos dados não certifica capacidade preditiva fora da amostra nem posições observadas dos dealers.
