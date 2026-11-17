# TODO

## Cobertura de resoluções para templates e IA

### Decisão

Usar emuladores configurados nas resoluções-alvo reais. Não escolher uma única
resolução como modelo universal e não redimensionar artificialmente os frames
antes da coleta.

O projeto trata templates e IA de maneiras diferentes:

- Os templates globais usam `1080x2400` como referência e são redimensionados
  pela dimensão curta da tela (`TEMPLATE_SCALE_BASIS = "short_side"`), com
  variações de escala. Começar nessa resolução é adequado.
- Templates específicos aprendidos pelo bot são salvos por resolução e
  complementam ou substituem os globais quando necessário.
- Dataset, cache e modelos de IA são separados pela resolução efetiva da
  captura. Com `AI_MODEL = "auto"`, uma captura `720x1600` procura
  `IA/models/720x1600/category.joblib`; não há fallback automático para o
  modelo de `1080x2400`.

Assim, treinar apenas na mediana ou na maior resolução não cobre as demais no
modo de IA atual.

### Plano de execução

1. Criar e validar o fluxo inicial em `1080x2400`, que já é a resolução de
   referência do detector por templates.
2. Criar emuladores com as resoluções-alvo e confirmar a resolução efetiva
   registrada pelo bot. A captura pode diferir da resolução declarada pelo
   emulador, e é ela que define a pasta do dataset e o modelo usado.
3. Antes de coletar dados para IA, validar o template matcher em cada
   resolução. Deixar o aprendizado automático gerar templates específicos
   quando os templates globais não forem suficientes.
4. Coletar e treinar modelos de IA por resolução, primeiro nos extremos e em
   uma configuração representativa:

   - `720x1600`
   - `1080x2400`
   - `1440x3120`
   - `1200x1920`
   - `2136x3200`

5. Cobrir depois as resoluções intermediárias:

   - `1080x2340`
   - `1220x2712`
   - `800x1340`
   - `1600x2560`

### Observações

- Tablets não devem ser considerados somente celulares ampliados: proporções
  como `4:3` e `16:10` podem alterar o layout do jogo, mesmo que as áreas de
  busca sejam proporcionais.
- Não normalizar ou escalar externamente screenshots para `1080x2400`: isso
  mascararia diferenças de layout e poderia quebrar a relação entre captura e
  coordenadas de toque.
- Um modelo global com resoluções combinadas é uma melhoria futura possível,
  mas ainda não é o comportamento padrão. Até ele ser implementado e validado,
  manter modelos específicos por resolução.
