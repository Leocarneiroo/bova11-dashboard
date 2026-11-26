# Auditoria do modelo de análise BOVA11 — 08/10/2026

O sistema transforma CSVs de opções em mapas de exposição, rankings, cenários e sinais. A carga considerada é uma pessoa executando o runner diariamente. A revisão usa o código atual do workspace, incluindo alterações locais ainda não commitadas.

**Diagnóstico:** o motor Merton por spot já fornece uma boa base de evolução. A prioridade é corrigir dados ausentes, definições de gamma flip, unidades e validação. O desempenho preditivo ainda não está demonstrado.

## Evidências reproduzidas

- `python3 -m unittest discover -s tests -v`: **24 testes passaram**.
- Leitura dos CSVs de 07/10 e 08/10 e chamadas diretas às funções, sem executar o runner nem sobrescrever históricos ou dashboards.
- Reexecução em memória do Skew atual: **116 sessões**, entre 03/03 e 08/10/2026; delta flow e vega flow foram zero em todas elas.
- Backtest atual do Skew: **46,36% em 110 observações**. Destas, **27 não têm fechamento atual ou futuro válido**. Excluindo apenas esses rótulos inválidos: **50,60% em 83 observações**; prever queda em todas essas 83 observações acertaria **61,45%**. Isso é uma verificação diagnóstica, não um backtest corrigido completo nem uma validação isolada do GEX.
- Referência para os cálculos de 08/10: spot manual **203,08**. Os quatro arquivos de fechamento encontrados estão sem IV nas linhas aceitas pelos parsers. O fechamento mensal de 16/10 está ausente, embora seu arquivo de Volume exista.

## Correções necessárias

### 1. Dado ausente vira exposição zero

**Onde:** `scripts/bova11_tex_dex_vex.py:350`, `scripts/bova11_auto.py:315`, `scripts/bova11_demand_flow.py:310`.

**O que faz:** calcula exposições e scores a partir das gregas ou da IV.

**Problema:** o perfil começa com zeros e só acumula quando há IV. Em 08/10, todos os quatro perfis retornam GEX zero, embora três CSVs tenham gamma e OI disponíveis. Na semanal de 09/10, o CSV informa gamma numérico zero e não oferece IV; seu OI soma cerca de 15,24 milhões. A rotina estática preserva o zero publicado, cuja origem econômica não foi certificada. A leitura inicial da auditoria confundiu esse zero informado com ausência de gamma; essa distinção foi corrigida durante a implementação. Ausência de dado e exposição nula são situações diferentes.

**Melhoria mínima:** preservar `None`/`NaN`; publicar cobertura de IV/gamma ponderada pelo OI e vencimentos ausentes. Se não houver cobertura suficiente, mostrar “indisponível”. O GEX estático pode continuar disponível quando houver gamma fornecido, sem simular uma curva com IV inexistente. Recuperar primeiro os CSVs completos; estimar IV por preço somente com quotes válidos e modelo compatível com a série.

**Se mantiver:** sinais e curvas podem parecer neutros quando a exposição é desconhecida. Comparar D e D-1 com universos diferentes também mistura mudança de mercado com falta de arquivo.

### 2. “Gamma flip” representa medidas diferentes

**Onde:** `scripts/bova11_auto.py:324`, `scripts/bova11_market_gamma.py:155`, `scripts/bova11_gex_history.py:300`, `scripts/bova11_diagnostico_4d.py:311`, `scripts/bova11_skew_prediction.py:637`.

**O que faz:** identifica um preço associado a troca de regime.

**Problema:** Dashboard e Market Gamma procuram cruzamento do GEX por strike; Histórico usa soma cumulativa por strike; 4D usa essa soma e devolve o último strike mesmo sem cruzamento. O Skew faz uma média de flips por vencimento e substitui flips ausentes por ATM. Esses resultados não equivalem à raiz do GEX total em função do spot.

**Exemplo:** para os arquivos disponíveis de 08/10: Dashboard sem flip; Market Gamma 201,00; Histórico aproximadamente 214,43; 4D 215,00. O valor de Market Gamma pode surgir de um strike com zero, sem troca efetiva de sinal. Em um caso sintético com todos os GEX positivos, o 4D também devolve um flip.

**Melhoria mínima:** reaproveitar `build_unified_spot_profile` e `aggregate_spot_profiles`, após corrigir cobertura e convenções. Buscar raízes na curva agregada `GEX(S)`, devolver ausente quando não houver raiz e preservar múltiplas raízes. Manter o cumulativo como outra medida, com nome próprio. Definir o regime pelo sinal agregado no spot atual, sem assumir Long Gamma quando o flip for ausente.

**Se mantiver:** abas contradizem o mesmo cenário e níveis operacionais parecem mais precisos do que o cálculo permite.

### 3. A janela 160–200 omite a região acima do spot

**Onde:** `scripts/bova11_auto.py:29`, `scripts/bova11_auto.py:581`.

**O que faz:** limita o gráfico e a procura do dominante/flip, mas mantém o total agregado calculado em toda a cadeia disponível.

**Problema:** em 08/10, spot 203,08 fica fora da janela. O GEX líquido disponível é aproximadamente **+57,60 milhões**, enquanto os strikes visíveis somam **−20,82 milhões**. A janela contém apenas **31,39%** da exposição bruta calculada, definida aqui como soma de `abs(call_gex) + abs(put_gex)`. Essa cobertura usa os gammas publicados, inclusive zeros; não certifica sua qualidade econômica.

**Melhoria mínima:** usar toda a cadeia para cálculo e permitir zoom visual. Exibir explicitamente o universo usado pelo total, cobertura e região ATM. Uma janela operacional pode seguir moneyness ou movimento esperado, sem truncar o agregado.

**Se mantiver:** o gráfico pode sugerir um regime diferente do total e esconder a concentração mais próxima do preço.

### 4. GEX muda de escala entre módulos

**Onde:** `scripts/bova11_shared.py:335`, `scripts/bova11_skew_prediction.py:489`, `scripts/bova11_demand_flow.py:288`.

**O que faz:** converte gamma e OI em exposição.

**Problema:** o helper usa `gamma × OI × S² / 100`; Skew e Demand Flow usam `gamma × OI × S²`. Mesmos inputs sintéticos resultaram em **200** e **20.000**. Uma normalização por z-score pode cancelar um fator constante, mas valores publicados e comparações entre abas permanecem incompatíveis. TEX/VEX ainda carregam fatores 100, enquanto a aba de perfis usa VEX para vanna, não vega.

**Melhoria mínima:** explicitar multiplicador do instrumento, unidade de OI e unidade de cada grega. Centralizar a fórmula canônica e distinguir exposição monetária, cotas equivalentes, vega e vanna. Não aplicar automaticamente o multiplicador 100 de opções americanas aos dados de BOVA11: a especificação e o fator de cotação da série precisam ser conferidos.

**Se mantiver:** grandezas com unidades diferentes parecem comparáveis e alterações de motor podem quebrar a continuidade histórica.

### 5. Há erros reproduzíveis em Charm e Theta

**Onde:** `scripts/bova11_shared.py:461`, `scripts/bova11_shared.py:553`, `scripts/bova11_tex_dex_vex.py:385`.

**O que faz:** decompõe mudanças de delta e calcula exposição à passagem do tempo.

**Problema:** a decomposição multiplica charm definido como passagem do tempo pela variação negativa do tempo restante. Em uma call com spot 100, strike 105, IV 25% e 30 dias restantes, envelhecer um dia produziu Δdelta **−0,0037931**, mas a contribuição de charm ficou **+0,0037927**. O residual absorveu o erro. Para Theta da call com `q != 0`, o termo de carry usa o sinal errado; uma derivada numérica independente do preço deu **−0,0439688/dia**, contra **−0,0530906/dia** na função, para `S=K=100`, `IV=20%`, `T=0,1`, `r=10%`, `q=3%`.

**Melhoria mínima:** definir derivadas por tempo de calendário e tempo restante; corrigir os sinais e testar contra diferenças finitas do preço/delta. Definir também a conversão 365/252: o motor calcula T em dias corridos/365, enquanto CEX divide por 252. Retirar opções vencidas em vez de dar a elas um dia artificial de vida.

**Se mantiver:** a análise pode atribuir a mudança de delta ao driver errado. A identidade de fechamento da decomposição não valida as gregas, pois o residual é calculado por diferença. Um teste existente de Theta repete o mesmo sinal incorreto e, por isso, passa.

### 6. O backtest cria resultados para datas sem preço

**Onde:** `scripts/bova11_skew_prediction.py:733`, `scripts/bova11_skew_prediction.py:765`.

**O que faz:** classifica o retorno futuro como queda ou não queda e mede acerto.

**Problema:** uma comparação com `NaN` retorna falso e vira `target_down = 0`; o `dropna()` posterior não elimina esse rótulo. No histórico atual, isso incluiu 27 observações inválidas no total de 110. Um teste sintético também confirmou inclusão da última sessão, sem retorno futuro conhecido.

**Melhoria mínima:** gerar alvo somente com preços atual e futuro válidos. Definir horizonte pelo calendário de pregões, pois a próxima linha com CSV pode estar vários pregões adiante. Separar neutros, quedas, altas e retornos abaixo do custo operacional. Comparar com baseline e fazer avaliação cronológica fora da amostra.

**Se mantiver:** percentual de acerto não mede apenas resultados observados. Os números antigos de acurácia em AGENTS.md não validam este motor atual.

### 7. O componente de fluxo direcional está inativo

**Onde:** `scripts/bova11_skew_prediction.py:426`, `scripts/bova11_skew_prediction.py:457`, `scripts/bova11_skew_prediction.py:705`.

**O que faz:** calcula delta flow e vega flow com classificação de agressão.

**Problema:** `classify_aggression()` sempre retorna zero. Os dois fluxos foram zero nas 116 sessões. Os pesos continuam presentes no score, embora não forneçam informação direcional.

**Melhoria mínima:** tratar esses componentes como indisponíveis e explicitar/ajustar os pesos ativos. Só calcular agressão com negócios e quotes sincronizados; o último preço de um CSV diário contra bid/ask de fechamento não identifica a agressão de todo o volume do dia.

**Se mantiver:** o painel sugere uma confirmação de fluxo que o código não calcula.

### 8. Demand Flow confunde estrutura de preços com demanda

**Onde:** `scripts/bova11_demand_flow.py:91`, `scripts/bova11_demand_flow.py:301`, `scripts/bova11_demand_flow.py:711`.

**O que faz:** combina sete componentes padronizados entre strikes do mesmo dia.

**Problema:** o “skew” usa `put_mid − call_mid`, que contém principalmente moneyness/carry; não é skew de IV. O score usa níveis do dia, e `spot_d1` não participa da comparação do motor. IV ausente vira zero; z-scores não têm limite e podem explodir quando a dispersão mediana é pequena. Em 08/10, a semanal de 30/10 deu score **−26,20**, com componente premium **−186,47**, apesar do limiar Bull de apenas −0,25. Todos os quatro vencimentos foram classificados Bull, sem IV disponível.

**Melhoria mínima:** distinguir score de distribuição de um indicador de mudança de demanda. Para demanda, usar diferenças de contratos comparáveis D/D-1, skew por delta e medidas de fluxo disponíveis. Preservar componentes ausentes, renormalizar pesos, limitar outliers e publicar cobertura. Theta absoluto e `vega × abs(delta)` são proxies; não devem confirmar Charm/Vanna como se fossem derivadas calculadas.

**Se mantiver:** a composição da cadeia e poucos strikes extremos podem dominar o sinal.

### 9. Consolidação de strikes pode distorcer exposição

**Onde:** `scripts/bova11_expiry_family.py:169`, `scripts/bova11_auto.py:221`, `scripts/bova11_auto.py:321`.

**O que faz:** reúne linhas por strike antes dos cálculos.

**Problema:** o helper soma OI e tira média simples de gamma. Para duas linhas com `(gamma, OI) = (0,01; 1.000)` e `(0,10; 1)`, a soma correta de `gamma × OI` é **10,1**, mas a consolidação gera **55,055**. O Dashboard descarta a segunda linha do mesmo strike, enquanto outras rotinas somam linhas. Isso cria diferenças quando existem séries distintas no mesmo strike ou duplicações de exportação.

**Melhoria mínima:** distinguir duplicata do mesmo contrato de séries diferentes. Deduplicar por identidade do contrato; calcular exposições por contrato e só então somar por strike. Se for necessário agregar gregas, ponderar pelo OI correspondente de cada lado.

**Se mantiver:** a exposição depende da ordem de linhas e da aba escolhida.

## Melhorias de interpretação e validação

### 10. A posição dos dealers é uma hipótese, e GEX não fornece direção sozinho

**Onde:** `scripts/bova11_shared.py:337`, `scripts/bova11_skew_prediction.py:712`, `scripts/bova11_tex_dex_vex.py:382`.

**O que faz:** atribui gamma positivo às calls e negativo às puts; também transforma DEX “retail” no oposto para representar FM.

**Problema:** OI não identifica quem está comprado/vendido. Comprar uma put também é estar long gamma. O Skew adiciona GEX padronizado positivo a um score bearish, impondo uma relação direcional sem estimativa ou validação específica.

**Melhoria mínima:** rotular “GEX estimado sob convenção de posição”, manter a hipótese explícita e medir sensibilidade a convenções alternativas. Usar GEX como contexto de amortecimento/amplificação de movimentos; estimar direção separadamente e validar interações empiricamente.

**Se mantiver:** concentração em opções pode ser interpretada como posição institucional observada ou previsão de alta/queda.

### 11. A probabilidade publicada não está calibrada

**Onde:** `scripts/bova11_skew_prediction.py:731`, `scripts/bova11_skew_prediction.py:739`.

**O que faz:** aplica sigmoid ao score para produzir `prob_bear`.

**Problema:** não há ajuste estatístico de parâmetros ou calibração contra frequências observadas. Score 2 gera 88,08%, mas a fórmula não demonstra 88,08% de chance de queda.

**Melhoria mínima:** chamar de score transformado até haver calibração cronológica fora da amostra. Depois medir Brier score, curva de calibração e intervalos de confiança; definir pesos e limiares apenas em dados anteriores à avaliação.

**Se mantiver:** porcentagens transmitem uma precisão probabilística que ainda não foi medida.

## Ordem recomendada

1. Corrigir cobertura de dados, zeros artificiais e alvos inválidos do backtest.
2. Padronizar unidades, identidade dos contratos, Theta/Charm e definições de flip usando os helpers existentes.
3. Usar um motor de exposição comum para todas as abas e validar o snapshot de gamma fornecido contra o modelo no spot atual, dentro de tolerâncias explícitas.
4. Testar o valor de GEX para prever amplitude/volatilidade e reversão/continuação antes de combiná-lo com previsão direcional.
5. Acrescentar apenas métricas que demonstrarem ganho fora da amostra.

## Evoluções úteis depois das correções

- **Cenários conjuntos de spot e IV:** grade de preços e choques de volatilidade, com hipótese de smile explícita e sensibilidade por tempo até o vencimento. Reportar raiz e intervalo de estabilidade do flip, não um número exato sem sensibilidade.
- **Horizontes por vencimento:** curto prazo, semanal e mensal. Somar exposições econômicas; para sinais compostos, evitar média igual entre vencimentos de tamanhos muito diferentes.
- **Decomposição de ΔGEX:** separar efeito de OI, spot, IV, passagem do tempo, rolagem e mudança de cobertura. Assim uma redução por desaparecimento de arquivo não parece fechamento de posições.
- **Exposição frente à liquidez:** comparar a necessidade hipotética de hedge com liquidez do BOVA11 e instrumentos relacionados, mantendo hipóteses de hedge explícitas. A cadeia do ETF não representa, sozinha, toda a exposição do Ibovespa.
- **Validação por hipótese:** GEX para amplitude/volatilidade; skew/fluxo para direção; paredes/Max Pain para reação a níveis. Medir benefício adicional de cada bloco e remover confirmação redundante.

## Fontes externas

- [SpotGamma — metodologia GEX e Zero Gamma](https://spotgamma.com/gamma-exposure-gex/): convenções de inventário, distinção entre contexto de volatilidade e direção, unidades e recalculo de gamma por spot.
- [CME — Gamma](https://www.cmegroup.com/education/courses/option-greeks/options-gamma-the-greeks): gamma de opções e relação entre posição comprada/vendida e sinal.
- [B3 — Opções sobre ETF](https://www.b3.com.br/pt_br/produtos-e-servicos/negociacao/criptoativos/opcoes-sobre-etf.htm): tamanho/fator de cotação, estilos de exercício e vencimentos.

## Limites da revisão

Revisão aprofundada de GEX, perfis por spot, helpers, parsers de família, Skew, Demand Flow e cálculos ligados a Convexidade/Trade Score/4D. Inspeção das integrações do runner, Max Pain e Gravity. Não houve auditoria integral de UI, segurança, performance, Tape Flow, Quant, Bandas de Volatilidade ou exportação para IA; não houve publicação, operação de mercado ou backtest de rentabilidade. A origem e o timestamp econômico das gregas fornecidas nos CSVs não foram certificados.

Este relatório registra o estado anterior à implementação. As correções aprovadas e sua verificação estão em [Melhorias implementadas](melhorias_modelo_gex_2026-10-08.md). Históricos financeiros e dashboards publicados foram preservados.
