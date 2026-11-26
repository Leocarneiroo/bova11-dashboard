# Auditoria dos cálculos BOVA11 — 09/10/2026

O sistema lê fechamentos, OI, volume e negócios de opções e produz dashboards. A revisão considera uma execução diária por uma pessoa e o código atual do workspace, incluindo alterações locais anteriores a esta sessão.

**Veredito: não é possível confirmar que todos os cálculos estão corretos. O núcleo de gregas e de Max Pain passou nas verificações independentes; ainda há erros reproduzíveis na preparação dos dados, nas unidades e na interpretação de resultados.** Nenhuma fórmula, histórico ou página gerada foi alterada nesta auditoria.

## O que foi verificado

- Mapeamento dos 25 scripts Python, do runner e dos 12 arquivos de testes. Leitura dos caminhos de cálculo, parsers relevantes, agregações e consumidores dos indicadores. Não foi uma revisão linha a linha de todo o HTML/CSS.
- `python3 -m unittest discover -s tests -v`: **106 testes passaram**, sem falhas.
- **240 casos de gregas**: 120 conjuntos de parâmetros, cada um com call e put, comparados com diferenças finitas independentes do preço/delta Merton. Usados spot 80–230, strikes entre 85% e 115% do spot, IV 15%–50%, prazo de 7 dias a 1 ano, juros 0%–18% e carry −3%–8%. Seed: 927.
- **80 cadeias sintéticas de Max Pain**, de 3 a 12 strikes: as implementações de `shared`, `max_pain`, `gravity` e `oi_stats_light` coincidiram com uma soma independente de payoffs.
- Controle independente de ATR14, RevL e MoM Z-Score no Quant, usando 420 observações sintéticas. Foi necessário fornecer pandas/numpy aos globais do módulo porque yfinance não está instalado no interpretador usado; não houve download.
- Controle de estatística ponderada de OI: strikes `[90,95,100,105,110]`, OI `[10,20,40,20,10]` → média 100, desvio 5,477225575, HHI 0,26, Gini 0,28 e entropia 2,121928095 bits. Fórmulas descritivas coerentes.
- Reprodução em memória com os CSVs de 08/10 e spot manual 203,08; inspeção de strikes fracionários nos CSVs históricos. Havia 1.184 CSVs no diretório. Foram encontrados strikes fracionários aceitos pelo parser do Flow em 141 arquivos de fechamento.

Erros absolutos máximos no controle de gregas:

| Grega | Erro absoluto máximo |
|---|---:|
| Delta | 1,46 × 10⁻⁷ |
| Gamma | 1,56 × 10⁻⁷ |
| Vanna | 2,98 × 10⁻⁹ |
| Theta por dia corrido | 7,06 × 10⁻¹¹ |
| Charm por ano de tempo de calendário | 1,63 × 10⁻⁹ |

Esses resultados validam a implementação matemática sob as hipóteses do modelo e os parâmetros testados. Não certificam a qualidade das gregas exportadas, o estilo de exercício das séries nem o poder de previsão dos indicadores.

## Corrigir primeiro

### 1. Flow History perde OI ao truncar strikes fracionários

**Onde:** `scripts/bova11_flow_history.py:300`, em `compute_strike_flows`.

**O que faz:** transforma o strike em chave do histórico usando `str(int(s))`.

**Problema confirmado:** 79 e 79,5 passam a ter a mesma chave; a última linha sobrescreve a anterior. No arquivo `venc 17 jul Mensal fechamento (25mai).csv`, 165 strikes viraram 144 chaves. A soma de OI caiu de **50.812.939 para 49.818.039**, perdendo **994.900** no resultado em memória.

**Correção mínima:** preservar o valor decimal na chave e ajustar os consumidores que assumem inteiro. Regerar apenas os registros afetados após verificar os CSVs originais.

**Consequência:** totais, entradas e saídas de OI ficam incorretos. A auditoria não modificou o JSON existente.

### 2. Insights informa Long Gamma com GEX negativo

**Onde:** `scripts/bova11_insights_auto.py:352`, `:576` e `:1168`.

**O que faz:** procura um cruzamento de exposição por strike e decide o regime pela posição do spot em relação a esse nível; se não encontra nível, assume Long Gamma.

**Problema confirmado:** em 08/10, a semanal de 23/10 apresenta GEX estático de **−10.608.513,73**, flip ausente e regime **Long Gamma**. A de 30/10 apresenta **−7.399.966,38**, flip por strike 194,8 e também Long Gamma. Os dois totais negativos foram conferidos por soma independente dos gammas e OIs fornecidos.

**Correção mínima:** usar a definição comum de regime pelo sinal do agregado no spot e a curva comum `GEX(S)` para flip; manter separados resultado estático e resultado modelado. Dado insuficiente deve produzir indisponibilidade.

**Consequência:** a narrativa pode recomendar uma leitura de amortecimento quando a própria exposição calculada tem sinal oposto.

### 3. Bandas transforma IV ausente em volatilidade zero ou 20%

**Onde:** `scripts/bova11_bandas_vol.py:41`, `:148`, `:276`, `:344` e `:446`.

**O que faz:** calcula IV ATM, histórico, GARCH/EWMA e bandas.

**Problema confirmado:** os quatro fechamentos disponíveis de 08/10 estão sem IV nas linhas aceitas. O módulo retorna IV ATM **0%**. Com a fonte externa indisponível, todas as bandas ficam em **203,08**, enquanto IV Rank e Percentile aparecem como **50**, GARCH como zero e probabilidade de reversão como **55%**. Sem CSV algum, a rotina inventa IV de **20%**. Com apenas IV call 20%, IV put ausente e OI igual nos dois lados, publica ATM **10%**. O mesmo problema de dividir a IV disponível por dois aparece em `bova11_historical_data.py:400`.

**Correção mínima:** preservar ausência, calcular cobertura e não publicar estimativas com aparência de observação. Para IV parcial, explicitar qual lado está disponível ou bloquear a média completa. Sem preços históricos, HV e GARCH devem ficar indisponíveis.

**Consequência:** ausência de dados pode parecer ausência de risco ou regime mediano conhecido.

### 4. IV Rank e IV Percentile das Bandas usam a série errada

**Onde:** `scripts/bova11_bandas_vol.py:370`–`:387`.

**O que faz:** compara a IV atual com mínimo, máximo e distribuição da volatilidade **realizada** de 30 dias calculada dos preços.

**Problema confirmado:** isso mede IV contra HV, não IV Rank/Percentile. No controle sintético, IV de fallback 20% contra uma série de HV baixa produziu **IV Rank 21.578,5**. O problema é a série de referência, não apenas a falta de limitar o número a 100. A definição de IVR/IVP usa histórico de IV, conforme [OIC — métricas de volatilidade implícita](https://www.optionseducation.org/news/april-webinar-key-takeaways-understanding-volatility-and-options-skew).

**Correção mínima:** usar histórico de IV comparável; na ausência dele, renomear a comparação para IV vs HV e não derivar risco de um suposto IV Rank.

**Consequência:** classificação de opções caras/baratas e nível de risco têm significado diferente do rótulo publicado.

### 5. Bandas de uma data passada usam preços posteriores

**Onde:** `scripts/bova11_bandas_vol.py:358`.

**O que faz:** solicita `history(period="1y")` sem limitar o período à data de referência.

**Problema confirmado:** no controle de fonte histórica, `ref_date=2026-07-01` utilizou observações até **2026-09-17**. SMA, HV, GARCH/EWMA e referências do painel podem incorporar o futuro em uma execução histórica.

**Correção mínima:** limitar a consulta e filtrar o índice até `ref_date` antes de todos os cálculos.

**Consequência:** uma reconstrução histórica fica contaminada por informações que ainda não existiam na data indicada.

### 6. O perfil Merton considera OI ausente como posição zero

**Onde:** `scripts/bova11_tex_dex_vex.py:362`–`:363` e acumulações de `build_unified_spot_profile`.

**O que faz:** calcula a cobertura e as exposições usando `float(oi or 0.0)`.

**Problema confirmado:** uma call com `c_oi=None`, put com OI 1.000 e IV 25% nos dois lados gerou **cobertura 100%** e regime **Short Gamma**, ignorando a posição desconhecida da call. O parser também converte OI em branco em zero antes de chegar ao motor.

**Correção mínima:** distinguir OI observado zero de OI ausente no parser; invalidar a cobertura completa e o agregado quando houver inventário desconhecido.

**Consequência:** um perfil incompleto pode receber regime e flip como se toda a posição estivesse conhecida.

### 7. A cobertura de dados não chegou a todas as abas estáticas

**Onde:** `scripts/bova11_max_pain.py:77`, `:185`; `scripts/bova11_gravity.py:55`, `:132`; `scripts/bova11_historical_data.py:173`, `:282`.

**O que faz:** calcula exposição e histórico com parsers que convertem gregas em branco em zero; agregações pandas também podem somar contribuições ausentes como zero.

**Problema confirmado:** com OI call 1.000 e gamma desconhecido, Max Pain e Gravidade retornaram GEX líquido **zero** no teste em memória. Além disso, o Histórico de Mercado de 08/10 omite o fechamento mensal de 16/10 que está faltando, embora exista seu Volume, e devolve `warnings=[]` com totais aparentemente completos. A indisponibilidade da IV, sozinha, não invalida um gamma explicitamente fornecido: o erro aqui é tratar a própria grega ou um vencimento ausente como contribuição conhecida.

**Correção mínima:** transportar validade/cobertura desde a leitura e usar o inventário de vencimentos observados nos arquivos de fechamento e Volume. Totais parciais precisam ser identificados.

**Consequência:** abas podem divergir porque algumas bloqueiam o agregado incompleto e outras o publicam sem aviso.

### 8. Max Pain e Gravidade duplicam exposição de contratos repetidos

**Onde:** `scripts/bova11_max_pain.py:484`; `scripts/bova11_gravity.py:568`.

**O que faz:** calcula OI/Max Pain após deduplicar contratos no helper, mas passa `df_raw` sem deduplicação ao cálculo de exposição.

**Problema confirmado:** duas linhas idênticas do mesmo contrato, OI 1.000 e gamma 0,01, com spot 100, produzem OI publicado **1.000** e GEX **2.000**. A exposição coerente com esse OI é **1.000**.

**Correção mínima:** deduplicar por identidade do contrato antes dos dois caminhos. Preservar séries diferentes e somar suas exposições após o cálculo por contrato.

**Consequência:** a duplicação de uma linha de exportação altera GEX sem alterar o OI exibido.

### 9. Gravidade ainda chama cruzamento por strike de gamma flip

**Onde:** `scripts/bova11_gravity.py:155` e `:302`.

**O que faz:** encontra um zero/cruzamento na distribuição de GEX por strike.

**Problema confirmado:** strikes `[99,100,101]` com exposições `[1,0,1]` retornam flip **100**, sem qualquer troca de sinal. Mesmo havendo troca por strike, ela não determina a raiz da soma de exposições quando o spot varia.

**Correção mínima:** reutilizar a curva agregada `GEX(S)` e a detecção comum de raízes; se quiser conservar o cruzamento por strike, dar outro nome.

**Consequência:** níveis chamados de flip em abas diferentes não representam a mesma grandeza.

### 10. Insights descreve aumento de IV como queda

**Onde:** `scripts/bova11_insights_auto.py:629`–`:634`.

**O que faz:** seleciona a intensidade pela variação absoluta, mas sempre escreve “queda”.

**Problema confirmado:** variação de **+2,00 pp** em call e put gerou “queda acentuada de IV” e “theta crush de fim de semana”.

**Correção mínima:** usar o sinal da variação para a direção e seu módulo apenas para intensidade; não atribuir causa de calendário sem verificá-la.

**Consequência:** o texto comunica o contrário do número calculado.

### 11. O teste KS publica uma rejeição quando nem consegue calcular

**Onde:** `scripts/bova11_oi_stats_light.py:261`–`:278` e `:528`–`:533`.

**O que faz:** inventa uma amostra repetindo strikes conforme `round(OI/min_OI).clip(1)`, executa KS e ignora exceções.

**Problema confirmado no ambiente:** SciPy 1.18.0 lançou `TypeError: ndtr() takes from 1 to 2 positional arguments but 3 were given` na chamada com `args=(mu,sigma)`. A exceção é engolida: o resultado mantém p-valor **0** e o HTML afirma “Rejeita H₀ de normalidade”. Isso não é o resultado de um teste.

**Problema adicional de método:** o `clip(1)` dá uma observação a strikes com OI zero. Isolando o erro da biblioteca com uma CDF explícita, acrescentar um strike de OI zero mudou D de **0,2 para 0,2272727** e p de **0,7487190 para 0,5468848**, sem alterar a distribuição econômica. A amostra é discreta, tem tamanho artificial e estima os parâmetros nos mesmos dados; o p-valor usual não certifica normalidade. [SciPy documenta que o KS usual vale para distribuições contínuas](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.kstest.html).

**Correção mínima:** retirar a conclusão formal/p-valor até haver um teste apropriado. Falha deve aparecer como indisponibilidade; manter momentos e ajuste descritivo que já existem.

**Consequência:** uma falha de execução ou uma pseudoamostra vira evidência estatística aparente.

### 12. Bandas dá quase um ano de vida a uma opção recém-vencida

**Onde:** `scripts/bova11_bandas_vol.py:203`–`:224`.

**O que faz:** se a data de vencimento é anterior à referência, avança o ano automaticamente.

**Problema confirmado:** `dte_from_label("9 out W2", date(2026,10,10))` retorna **364 dias**. Um arquivo vencido de outubro pode entrar como vencimento de outubro de 2027.

**Correção mínima:** resolver o ano com o contexto da sessão, seguindo o helper existente, e excluir séries expiradas. Diferenciar vencimento próximo de janeiro de vencimento recém-expirado.

**Consequência:** a ponderação inversa por DTE e o universo de vencimentos ficam errados.

### 13. Hunter Walls informa PCR zero quando só há puts

**Onde:** `scripts/bova11_hunter_walls.py:195`; apresentação semelhante em `scripts/bova11_max_pain.py:557`.

**O que faz:** usa zero quando o denominador de put/call ratio é zero.

**Problema confirmado:** volume call zero e put 1.000 retornam PCR **0,0**. Zero corresponderia a ausência de puts com calls presentes; não representa esse caso.

**Correção mínima:** representar ausência de denominador explicitamente; exibir “somente puts” ou indisponível, distinguindo também o caso sem volume nos dois lados.

**Consequência:** uma concentração exclusiva em puts parece concentração em calls.

## Conferir unidades antes de certificar o Tape

### 14. Tape usa multiplicadores incompatíveis para quantidade e delta

**Onde:** `scripts/bova11_options_tape_flow.py:506` e `:689`–`:691`.

**O que faz:** financeiro = preço × quantidade; delta equivalente = agressão líquida × delta × **100**.

**Caso reproduzido:** compra agressora de 1.000 unidades a 2,50, delta 0,5, gera financeiro **2.500** e delta equivalente **50.000**. Se a quantidade é em unidades do ativo, o delta equivalente é **500**. Se ela fosse em lotes de 100, o financeiro também precisaria incorporar esse lote para preço por unidade.

**Correção mínima:** confirmar a unidade do exportador e da série e usar o mesmo fator econômico nos dois caminhos. O material [B3 sobre tamanho dos contratos](https://clientes.b3.com.br/c/document_library/get_file?groupId=20119&uuid=c033eb61-b13c-e911-72dc-448132245e66) apresenta opção de ETF Ibovespa 1:1; a [especificação de opções sobre ETF](https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/criptoativos/opcoes-sobre-etf.htm) exige observar quantidade e fator de cotação. Não há fundamento para presumir 100 apenas pelo lote padrão.

**Limite da conclusão:** os cálculos usam fatores diferentes, confirmado; não certifiquei a unidade dos workbooks originais. A distorção de 100 vezes depende dessa unidade.

## Limites do modelo e da documentação

- GEX com calls positivas e puts negativas é uma **convenção de posição**, não posição observada de dealers. A fórmula monetária `gamma × OI × spot² × 0,01` é coerente para movimento de 1% se OI e unidade da grega forem compatíveis. OI sozinho não identifica comprador, vendedor ou hedge efetivo.
- Na aba unificada, DEX é em unidades equivalentes, VEX representa **vanna** e TEX representa a perspectiva do vendedor. No 4D, DEX é monetário, VEX usa **vega** e TEX usa theta fornecido. Não comparar diretamente números com os mesmos rótulos sem conferir definição e unidade.
- Demand Flow atual é `demand-change-v4`: cinco componentes de mudanças por contrato, normalização robusta com clipping, cobertura mínima e pesos heurísticos. Não corresponde às quatro fórmulas antigas ADI/PDI/IDS/DFP descritas no AGENTS e em `bova11_arquivos.py:49`–`:61`. O texto exportado para IA precisa acompanhar o motor atual.
- O Skew atual preserva alvos ausentes, separa neutros e chama a sigmoid de score transformado. Esses avanços estão cobertos pelos testes. A calibração preditiva e o calendário oficial de pregões continuam pendentes; não foi medido novo desempenho fora da amostra nesta auditoria.
- As probabilidades de reversão das Bandas são constantes por faixa (40/55/65/70), não probabilidades estimadas ou calibradas. Os scores de Trade, Gravidade, Max Pain e cenários 4D são heurísticos; suas fórmulas, por si sós, não demonstram capacidade de previsão.
- As diferenças finitas validam gregas Merton/Black–Scholes. Séries americanas, convenções da IV, calendário 252/365, carry implícito e timestamp dos quotes exigem validação da fonte. A B3 admite estilo americano ou europeu em opções de ETF.

## Prioridade e limites da execução

Priorizar Flow com strikes fracionários, regime/direção de Insights e ausência de dados. Depois uniformizar cobertura, identidade dos contratos, definições de flip e unidades. Corrigir Bandas e retirar conclusões estatísticas/probabilidades não suportadas antes de usar esses números como confirmação operacional.

Não executei o runner nem publiquei páginas; não alterei scripts, CSVs ou históricos. As consultas financeiras externas foram substituídas por controles em memória nas reproduções. No Python usado, yfinance e arch estão ausentes; não houve validação de download nem ajuste GARCH real. Não foi feito backtest operacional, certificação das gregas na fonte, auditoria de todos os negócios intradiários ou validação visual completa do JavaScript no navegador.
