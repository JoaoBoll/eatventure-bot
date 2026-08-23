# Pasta IA

Esta pasta reúne os scripts de treinamento e execução da IA do EatVenture.

## Conteúdo

- `train_ai.py` — treina o modelo a partir do dataset em `../dataset`.
- `bot_ai.py` — carrega o modelo treinado e mostra ou executa a decisão da IA sobre a tela.
- `model.joblib` — modelo treinado salvo em disco.
- `model.joblib.meta.json` — metadados do modelo (labels e distribuição das classes).

## Treinamento

No diretório raiz do projeto, rode:

```bash
python IA/train_ai.py
```

Ele lê o dataset em:

- `dataset/samples.jsonl`
- `dataset/images/`

E salva o modelo em:

- `IA/model.joblib`

Você também pode controlar a parte do dataset usada para treino e o backend de execução:

```bash
python IA/train_ai.py --train-ratio 0.8 --device auto
```

Durante a execução, o script mostra o progresso em porcentagem de 0 a 100%:

- carregando imagens
- separando treino/teste
- treinando classificador
- avaliando modelo
- salvando modelo

- `--train-ratio` define a fração do dataset usada para treino (ex.: 0.8 = 80% treino / 20% teste).
- `--device auto` tenta usar CUDA quando o ambiente tiver `cuml` + GPU. Se não houver suporte, cai para CPU.

## Execução em demonstração

```bash
python IA/bot_ai.py --demo --demo-limit 5 --headless
```

Esse modo lê imagens do dataset e imprime a previsão da IA no console.

## Execução com janela de visão

```bash
python IA/bot_ai.py
```

Isso abre a janela `AI bot decision` e mostra a previsão da IA sobre a tela do device. Para deixar a janela menor ou maior, ajuste os valores no topo de `bot_ai.py`:

```python
AI_WINDOW_WIDTH = 480
AI_WINDOW_HEIGHT = 720
```

## Execução com aprovação manual

```bash
python IA/bot_ai.py --approval-mode --confidence-threshold 0.70
```

Nesse modo, a IA mostra a previsão e oferece botões de `APPROVE` / `REJECT` na tela. O usuário precisa aprovar antes da ação ser executada. Também aceita `A` para aprovar e `R` para recusar.

## Execução autônoma

```bash
python IA/bot_ai.py --auto --confidence-threshold 0.70
```

Nesse modo, quando a confiança da IA passar do threshold, ela executa a ação prevista automaticamente.

## Observação

O modelo depende do dataset estar consistente: cada linha de `samples.jsonl` precisa apontar para uma imagem válida em `dataset/images`, e isso é o que o treinamento usa como base.

Em termos de hardware, o RandomForest usado no projeto roda bem em CPU. GPU só é interessante se você trocar para backends específicos como `cuml` em ambientes com CUDA; para o stack atual, CPU é a opção mais estável e prática.
