# Correções do modelo GEX — plano de implementação

**Objetivo:** implementar as correções aprovadas na auditoria, preservando CSVs e históricos existentes. O pedido de continuar as melhorias aprova o escopo corretivo já descrito no relatório.

**Arquitetura:** reutilizar `bova11_shared.py` para unidades e matemática e `bova11_tex_dex_vex.py` para perfis por spot. Não criar novo framework ou estimar IV/posição dos dealers sem dados. Não publicar automaticamente.

**Decisões:** GEX monetário por movimento de 1% usa `gamma * OI * multiplier * spot**2 * 0.01`, multiplicador explícito 1 para a unidade dos CSVs atuais. Exposição sem cobertura integral é identificada como parcial; flip/regime modelado indisponíveis sem IV integral dos contratos com OI. Ausência preservada como null. Probabilidade não calibrada será rotulada score transformado. Base temporal das gregas do modelo: dias corridos/365.

## Tarefas

- [x] Matemática e perfis: testes independentes de Theta/Charm por diferenças finitas; corrigir sinais, CEX diário, vencimentos expirados; perfis nulos sem IV; agregação com cobertura; função compartilhada de raízes reais e resumo por spot.
- [x] Dashboard e parsers: usar todos os strikes; deduplicar por contrato; somar exposições; exibir cobertura, hipótese de inventário e unidade; usar resumo modelado para flip, preservando snapshot estático quando gamma estiver disponível.
- [x] Abas GEX Histórico/Market Gamma/4D: integrar o mesmo resumo por spot; retirar flips cumulativos/fabricados; separar GEX por strike do GEX agregado; identificar cobertura parcial e impedir sugestões com gregas insuficientes.
- [x] Skew: alvos ausentes continuam ausentes; horizonte usa calendário/preços e invalida lacunas; excluir neutros da avaliação direcional; publicar baseline e n; fluxo sem agressão permanece indisponível; normalizar pesos ativos; GEX contextual fora do score direcional não validado; unidade canônica; score sigmoid não é probabilidade calibrada.
- [x] Demand Flow e consolidação: preservar valores ausentes; limites de z-score e pesos ativos; skew de IV por delta; mudança D/D-1 com contratos comparáveis; sem proxies silenciosos de Charm/Vanna; agregação de gregas ponderada pelo OI após deduplicação de contratos.
- [x] Revisão de conformidade e qualidade; suíte completa; geração em diretório temporário para 07/10–08/10 e inspeção de HTML/JSON sem alterar históricos; registrar limitações e atualizar contexto.

## Contrato entre tarefas

`bova11_tex_dex_vex.gamma_profile_summary(rows, venc_name, tag_d, spot_ref, rate=DEFAULT_RATE)` recebe o formato existente `c_iv`, `p_iv`, `c_oi`, `p_oi`, bid/ask e strike; retorna `profile`, `flip`, `flips`, `regime`, `iv_coverage`. `flip` é a raiz mais próxima do spot; sem mudança real de sinal ou cobertura integral, `flip=None`, `flips=[]`, `regime='Indisponível'`. `bova11_shared.find_profile_roots(profile)` recebe pontos `spot`/`gex_net` e não inventa cruzamento em zeros/trechos ausentes.

Testes devem reproduzir cada bug antes da correção. Os arquivos-base foram copiados para diretório temporário para permitir revisão apenas das mudanças desta sessão. Não fazer commit das alterações preexistentes nem executar o runner sobre `output/`/`history/`.
