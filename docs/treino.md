# Treino da IA

## Como o dado fica em disco

A coleta e os treinos automáticos são separados por resolução. Cada modelo usa
somente amostras da própria resolução.

```
dataset/
├── data/
│   ├── 1080x2400/
│   │   ├── samples.jsonl
│   │   └── images/<AAAA-MM-DD>/<id>.jpg
│   └── 1088x1742/
│       ├── samples.jsonl
│       └── images/<AAAA-MM-DD>/<id>.jpg
└── cache/
    └── category/<resolucao>/{meta.json,000001.npz,...}
```

Cada pasta de resolução é auto-contida: o campo `image` de cada registro é
relativo à raiz **dela**, então dá para mover, copiar ou apagar uma resolução
sem invalidar as outras.

O layout antigo (`dataset/samples.jsonl` + `dataset/images/`) continua sendo
lido, então dado já coletado treina sem migração. O importador e algumas
ferramentas legadas ainda usam esse layout quando nenhum caminho é informado;
para uma coleta nova, aponte explicitamente para o shard em `dataset/data/`.

`dataset/data/` não entra no git. `dataset/cache/` é mantido no repositório
para compartilhar features já calculadas.

## Cache de features

O treino não re-decodifica as imagens a cada execução. Cada amostra é
convertida uma vez em vetor de features e guardada em `dataset/cache/`; uma
execução nova só processa o que é novo e grava um chunk a mais, sem reescrever
os anteriores. Com o cache formado, **a imagem original não é mais necessária
para treinar**.

O cache tem uma assinatura. Mudar `IA/features.py` (tamanho do patch, margem,
bins do HSV), o número de `--negatives` ou a origem dos rótulos torna os chunks
incompatíveis. Eles **não são apagados automaticamente**: o treino para e pede
outro `--cache-root`, para não perder a memória já coletada por engano.

## Comandos

### Coletar

```bash
python src/main.py --ai-collect
```

Grava em `dataset/data/<resolucao>/`. Sem a flag, usa `DATASET_SAVE` do
`src/core/config.py`.

Com `AI_AUTO_TRAIN = True`, o treinador verifica o dataset ao iniciar e a cada
10 minutos (`AI_TRAIN_CHECK_INTERVAL`). Quando há pelo menos 10 mil registros
novos de uma mesma resolução, usa todos os registros novos disponíveis
daquela resolução. No painel web, o treinador verifica o dataset ao iniciar
qualquer bot com treino automático habilitado, inclusive para processar lotes
acumulados em execuções anteriores. O modelo e o ponteiro ficam em
`IA/models/<RESOLUÇÃO>/` e são versionáveis no Git. Durante o treino no painel,
somente bots que coletam a mesma resolução são pausados e reiniciados. O
snapshot e a limpeza preservam registros e imagens gravados depois do início do
treino. O ajuste da floresta usa todos os núcleos disponíveis (até 64); a
extração das features das imagens continua sequencial.

No terminal, o bot permanece aberto, mas pausa ações e coleta por padrão
durante o treino. Defina `AI_PAUSE_BOT_WHILE_TRAINING = False` para continuar
jogando e coletando em paralelo.

### Treinar

```bash
python IA/train_ai.py
```

Treina manualmente sem esperar 10 mil registros, criando um modelo para cada
resolução com amostras. Para treinar somente uma:

```bash
python IA/train_ai.py --resolution 1080x2400 --model-out IA/models/1080x2400/category.joblib
```

Use `--combine-resolutions` explicitamente para criar um modelo misturando as
resoluções.

Cada execução gera um resumo JSON em `IA/training_reports/`, com imagens usadas,
categorias/classes e contagens, dados reaproveitados, extração, duração e métricas
de validação por classe. O modelo novo é sempre salvo como candidato; o ponteiro
ativo só é atualizado se atingir macro F1 e recall mínimos nas categorias críticas
presentes no holdout. Não lista IDs nem gera tabelas extensas no terminal.

Após publicar e promover o candidato, remove os registros e imagens
aproveitados. O cache acumulado permanece. Use `--keep-data` para preservar
os dados brutos no treino manual. Dados novos e não aproveitados permanecem.

| flag | padrão | o que faz |
|---|---|---|
| `--kind category\|action` | `category` | `category`: recorte → categoria (é o que o bot usa). `action`: tela inteira → ação, só para comparação |
| `--dataset-root PASTA` | `dataset/` | raiz da coleta; todas as resoluções dentro dela |
| `--resolution LARGURAxALTURA` | todas | limita o treino à resolução indicada; `legacy` seleciona amostras sem resolução identificável |
| `--model-out ARQUIVO` | `IA/models/<RESOLUÇÃO>/category.joblib` | referência do modelo da resolução selecionada |
| `--combine-resolutions` | desligado | opta por treinar todas as resoluções juntas |
| `--split phash\|session` | `session` | agrupamento do split. `session`: partida inteira de um lado só; sem sessão, usa `phash`. `phash`: somente quadros idênticos ficam do mesmo lado |
| `--test-ratio FRACAO` | `0.2` | fração de **grupos** para teste |
| `--negatives N` | `2` | recortes de fundo por frame. Muda a assinatura do cache |
| `--label-source confirmed\|all` | `all` | `all` usa todas as caixas do matcher; `confirmed` usa só o alvo acionado com resultado `changed` |
| `--cache-root PASTA` | `<dataset>/cache` | onde fica o cache de features |
| `--no-cache` | desligado | extrai tudo do zero, sem ler nem gravar cache e sem limpar os dados brutos |
| `--keep-data` | desligado | preserva registros e imagens após o treino manual |
| `--outcome R [R...]` | todos | filtra amostras: `changed`, `unchanged`, `unknown`, `negative` |
| `--trees N` | `200` | árvores da floresta |
| `--jobs N` | `-1` | processos no fit (`-1` = todos) |
| `--limit N` | todas | corta o número de amostras |
| `--seed N` | `42` | semente do split e do modelo |
| `--min-macro-f1 F` | `0.55` | macro F1 mínimo para ativar o candidato |
| `--min-critical-recall F` | `0.40` | recall mínimo de `renovate` e `fly`, quando aparecem no holdout |
| `--allow-unsafe-promotion` | desligado | ativa o candidato sem atingir os critérios; use apenas para diagnóstico |

Na coleta analisada, 56% das amostras repetiam `phash`. O split é
**por grupo, não por amostra**:
dividir por amostra deixa o mesmo quadro nos dois lados — a acurácia sai
inflada por decorar em vez de generalizar.

O modelo de visão precisa reconhecer todas as boxes visíveis, não apenas a que
foi pressionada. Por isso o padrão usa todas as caixas fornecidas pelo matcher;
o campo `acted`, o estado e o resultado da ação permanecem no dataset para o
futuro modelo de prioridade. O fundo é amostrado em regiões sem caixas. Use
`--label-source confirmed` para um experimento mais conservador, que aceita só
o alvo de ações com resultado `changed`.

### Rodar o bot com o modelo

```bash
python IA/bot_ai.py --model IA/model.joblib
```

O modo ao vivo carrega as versões publicadas automaticamente, entre os
frames. O bot principal (`src/main.py`) continua usando templates.

| flag | padrão | o que faz |
|---|---|---|
| `--source templates\|proposer` | `templates` | de onde vêm as caixas candidatas |
| `--device SERIAL` | pergunta | device do adb |
| `--auto` | desligado | age de verdade; sem isso só observa |
| `--min-confidence F` | `0.60` | corte da confiança do modelo |
| `--compare` | desligado | compara o modelo com o template matcher ao vivo |
| `--demo` / `--demo-limit N` | `50` | mede acerto por caixa contra o dataset, sem device |
| `--headless` | desligado | sem janela |

### Simular contra o dataset

```bash
python IA/simulate_bot.py --model IA/model.joblib
```

Grava `IA/simulation_report.json`. Aceita `--dataset-root`, `--limit`,
`--show-mismatches`.

## Liberar espaço

Após uma promoção bem-sucedida, a limpeza remove somente registros e imagens do
snapshot que efetivamente entraram no treino. Linhas ou imagens adicionadas
depois do snapshot permanecem. Mantenha `dataset/cache/` enquanto quiser poder
reavaliar ou substituir um modelo sem recolher os dados históricos.

## O que o modelo decide

Só a **categoria** de cada recorte. A ação continua vindo das regras de
`src/core/state_machine.py` — o modelo substitui o reconhecimento, não a
decisão.
