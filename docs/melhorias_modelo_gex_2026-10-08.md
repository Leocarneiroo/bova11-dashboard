# Correções implementadas no modelo GEX — 08/10/2026

As correções de cálculo, cobertura e diagnóstico propostas na auditoria foram implementadas. A implementação não estabelece vantagem preditiva nem calibra probabilidades.

## Mudanças

- **GEX e unidades:** fórmula compartilhada `gamma × OI × multiplicador × spot² × 0,01`, em R$ por movimento de 1%. Multiplicador explícito 1 para as unidades atuais dos CSVs. Removidos fatores 100 de TEX/VEX; os limiares existentes do 4D foram convertidos para 5M/2M, preservando seu significado anterior. Vega e Vanna continuam grandezas diferentes e estão identificadas na interface.
- **Flip:** raiz da soma dos perfis recalculados no mesmo eixo de spot. Múltiplas raízes preservadas; zeros sem troca de sinal e ausência de raiz não fabricam flip. Regime modelado depende do GEX no spot atual e da cobertura integral de IV.
- **Dados ausentes:** `None`/`NaN` chegam aos HTMLs como `null`/`N/A`. Gamma numérico zero permanece zero. Gregas ausentes em posições com OI zero não contaminam a exposição; arquivo vazio ou vencimento ativo ausente invalida o agregado modelado. Cobertura por OI e avisos de universo incompleto ficam visíveis.
- **Universo:** Dashboard usa todos os strikes. Vencimentos expirados, inclusive T=0, são excluídos das exposições atuais. Snapshot com gamma publicado permanece separado do modelo por IV. Limites de strike em Market Gamma afetam somente a visualização.
- **Identidade:** exportações duplicadas são deduplicadas por contrato. Séries distintas no mesmo strike são somadas economicamente; gregas consolidadas são ponderadas pelo OI de cada lado. Dashboard compara D/D-1 pela identidade e conta volume uma vez por strike.
- **Theta/Charm:** sinais corrigidos e verificados por diferenças finitas independentes do preço/delta. Tempo do modelo e CEX diário usam dias corridos/365.
- **Skew:** preços ou alvos ausentes não viram acertos; neutros são excluídos do diagnóstico direcional. Calendário conservador de dias úteis preserva lacunas. Agressão indisponível deixa fluxos nulos; pesos ativos são renormalizados. GEX/DEX são contexto, sem peso direcional não validado. ΔOI é invalidado quando o universo de vencimentos muda ou está incompleto. Sigmoid é rotulado score transformado, não probabilidade calibrada. Histórico novo usa `*.skew-change-v3.json`, inclusive quando o caminho legado é passado explicitamente.
- **Demand Flow:** compara contratos idênticos D/D-1; usa mudança de skew de IV na região de delta disponível. Retirados proxies de Charm/Vanna. Z-score limitado a ±3; pesos e cobertura ativos explícitos. Agregado considera todos os vencimentos e bloqueia sinal quando o OI total é desconhecido. Registros `@demand-change-v4` preservam os resultados legados e a comparabilidade do histórico.
- **4D:** gregas incompletas, coleta parcial, cadeia vazia ou exposição neutra impedem cenário e sugestões operacionais.

## Verificação

`rtk proxy python3 -m unittest discover -s tests`: **106 testes passaram**. Compilação dos 9 scripts alterados passou. Verificação JavaScript de 8 HTMLs passou; inspeção no navegador conferiu GEX, Rankings, perfis, Market Gamma, Histórico, 4D, Skew e Demand Flow. A inspeção encontrou e corrigiu um erro de inicialização JavaScript que impedia tabela/gráficos do Skew de carregar.

Foram gerados previews usando 07/10–08/10 e spots 201,25/203,08, em diretório temporário. SHA-256 confirmou que nenhum arquivo existente em `history/` ou `output/` mudou; nenhum arquivo novo foi criado nessas pastas. O runner e as alterações locais preexistentes foram preservados. Sem commit ou publicação.

Previews desta verificação: `/var/folders/j9/5vhh7rkx61b66smvdr9yyl500000gn/T/bova11-gex-final-vutwuon0/`.

## Resultado para 08/10

O snapshot dos quatro fechamentos disponíveis soma aproximadamente **+57,60M de GEX**, sob a convenção call+/put−. Isso não certifica inventário dos dealers nem inclui o fechamento mensal ausente de 16/10. As IVs disponíveis não cobrem as posições; portanto, flip e regime modelados são indisponíveis. Skew mostra **SEM DADOS**; 4D e agregado Demand Flow não fornecem cenário/sinal.

Com calendário e universos conservadores, o diagnóstico Skew ficou em **40% de acerto, n=15 sinais direcionais válidos**, contra **66,7% de baseline majoritário** nessa mesma amostra. Existem 36 sessões com alvo válido; o restante é excluído do diagnóstico. A cauda cronológica tem apenas 5 sinais: 60% de acerto e 60% de baseline. A amostra é pequena; esses números não demonstram desempenho fora da amostra nem rentabilidade.

## Trabalho que depende de dados e pesquisa

1. Recuperar IVs/fechamentos completos, especialmente o mensal de 16/10, e certificar origem/timestamp das gregas e unidades de cada série.
2. Adotar calendário B3 verificado. O fallback de dias úteis invalida alvos próximos de feriados em vez de presumir o próximo pregão.
3. Validar pesos/limiares cronologicamente, com custos, baseline, intervalos de confiança e calibração. Os pesos atuais permanecem heurísticos.
4. Só depois testar choques conjuntos spot/IV, decomposição de ΔGEX e sensibilidade a hipóteses alternativas de inventário.

O runner atual não executa Skew/Demand Flow; esses módulos continuam disponíveis individualmente. Os HTMLs publicados continuam os anteriores até uma execução/publicação autorizada.
