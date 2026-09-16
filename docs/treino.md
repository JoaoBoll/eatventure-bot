# Treino da IA

## Como o dado fica em disco

A coleta separa por resolução. A resolução serve **só para organizar o dado** —
para o treino ela não significa nada, ele junta todas.

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
lido, então dado já coletado treina sem migração.

Nada disso entra no git (`.gitignore`).

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

### Treinar

```bash
python IA/train_ai.py
```

Junta todas as resoluções, usa o cache, grava `IA/model.joblib` e
`IA/model.meta.json`.

| flag | padrão | o que faz |
|---|---|---|
| `--kind category\|action` | `category` | `category`: recorte → categoria (é o que o bot usa). `action`: tela inteira → ação, só para comparação |
| `--dataset-root PASTA` | `dataset/` | raiz da coleta; todas as resoluções dentro dela |
| `--model-out ARQUIVO` | `IA/model.joblib` | onde gravar o modelo |
| `--split phash\|session` | `phash` | agrupamento do split. `phash`: quadros visualmente iguais ficam do mesmo lado. `session`: partida inteira de um lado só |
| `--test-ratio FRACAO` | `0.2` | fração de **grupos** para teste |
| `--negatives N` | `2` | recortes de fundo por frame. Muda a assinatura do cache |
| `--cache-root PASTA` | `<dataset>/cache` | onde fica o cache de features |
| `--no-cache` | desligado | extrai tudo do zero, sem ler nem gravar cache |
| `--outcome R [R...]` | todos | filtra amostras: `changed`, `unchanged`, `unknown`, `negative` |
| `--trees N` | `200` | árvores da floresta |
| `--jobs N` | `-1` | processos no fit (`-1` = todos) |
| `--limit N` | todas | corta o número de amostras |
| `--seed N` | `42` | semente do split e do modelo |

O split é **por grupo, não por amostra**: 56% das amostras repetem `phash`, e
dividir por amostra deixa o mesmo quadro nos dois lados — a acurácia sai
inflada por decorar em vez de generalizar.

### Rodar o bot com o modelo

```bash
python IA/bot_ai.py --model IA/model.joblib
```

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
