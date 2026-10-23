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

O cache é invalidado sozinho quando o que ele guarda deixa de ser comparável:
mudar `IA/features.py` (tamanho do patch, margem, bins do HSV) ou o número de
`--negatives` gera uma assinatura nova e os chunks daquela resolução são
descartados e refeitos.

## Comandos

### Coletar

```bash
python src/main.py --ai-collect
```

Grava em `dataset/data/<resolucao>/`. Sem a flag, usa `DATASET_SAVE` do
`src/core/config.py`.

Com `AI_AUTO_TRAIN = True`, a cada 10 mil registros novos de uma mesma
resolução inicia um treino automático independente. No painel web, o treinador
verifica o dataset ao iniciar qualquer bot com treino automático habilitado,
inclusive para processar lotes acumulados em execuções anteriores. O modelo e o
ponteiro ficam em `IA/models/<RESOLUÇÃO>/` e são versionáveis no Git. Durante o
treino no painel, os bots em execução são encerrados e iniciados novamente,
inclusive quando o treino falha.

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

Cada execução gera seu próprio JSON em `IA/training_reports/`, com contagens
do lote atual, dados reaproveitados e métricas de validação separados.

Após salvar o modelo e o relatório, remove os registros e imagens
aproveitados. O cache acumulado permanece. Use `--keep-data` para preservar
os dados brutos no treino manual. Dados novos e não aproveitados permanecem.

| flag | padrão | o que faz |
|---|---|---|
| `--kind category\|action` | `category` | `category`: recorte → categoria (é o que o bot usa). `action`: tela inteira → ação, só para comparação |
| `--dataset-root PASTA` | `dataset/` | raiz da coleta; todas as resoluções dentro dela |
| `--resolution LARGURAxALTURA` | todas | limita o treino à resolução indicada; `legacy` seleciona amostras sem resolução identificável |
| `--model-out ARQUIVO` | `IA/models/<RESOLUÇÃO>/category.joblib` | referência do modelo da resolução selecionada |
| `--combine-resolutions` | desligado | opta por treinar todas as resoluções juntas |
| `--split phash\|session` | `phash` | agrupamento do split. `phash`: quadros visualmente iguais ficam do mesmo lado. `session`: partida inteira de um lado só |
| `--test-ratio FRACAO` | `0.2` | fração de **grupos** para teste |
| `--negatives N` | `2` | recortes de fundo por frame. Muda a assinatura do cache |
| `--cache-root PASTA` | `<dataset>/cache` | onde fica o cache de features |
| `--no-cache` | desligado | extrai tudo do zero, sem ler nem gravar cache e sem limpar os dados brutos |
| `--keep-data` | desligado | preserva registros e imagens após o treino manual |
| `--outcome R [R...]` | todos | filtra amostras: `changed`, `unchanged`, `unknown`, `negative` |
| `--trees N` | `200` | árvores da floresta |
| `--jobs N` | `-1` | processos no fit (`-1` = todos) |
| `--limit N` | todas | corta o número de amostras |
| `--seed N` | `42` | semente do split e do modelo |

Na coleta analisada, 56% das amostras repetiam `phash`. O split é
**por grupo, não por amostra**:
dividir por amostra deixa o mesmo quadro nos dois lados — a acurácia sai
inflada por decorar em vez de generalizar.

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

Com o cache formado, as imagens de uma resolução já processada podem sair —
o treino segue funcionando pelo cache. O que **não** pode ser apagado é
`dataset/cache/`, que passa a ser o dataset de verdade, nem os
`samples.jsonl`, que são a fonte dos rótulos.

## O que o modelo decide

Só a **categoria** de cada recorte. A ação continua vindo das regras de
`src/core/state_machine.py` — o modelo substitui o reconhecimento, não a
decisão.
