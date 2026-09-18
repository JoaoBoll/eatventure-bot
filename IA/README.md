# Pasta IA

Treino e execução do modelo de visão.

Os números de amostras, caixas e acurácia abaixo são uma fotografia de uma
coleta específica; eles não são métricas garantidas para o dataset atual.

| Arquivo | O quê |
|---|---|
| `features.py` | extração de features — **fonte única** para treino e inferência |
| `dataset_io.py` | leitura do índice, split sem vazamento, referências |
| `proposer.py` | proposta de regiões candidatas (**experimental**) |
| `train_ai.py` | treina o modelo |
| `bot_ai.py` | roda o modelo, mostrando ou agindo |
| `model.joblib` | modelo treinado |
| `model.meta.json` | como o modelo foi feito e quanto ele acerta |

Os testes ficam em `../tests/test_ia.py`, fora desta pasta.

---

## Por que a fidelidade estava abaixo de 30%

Não faltava dado: são **29.297 amostras** e **65.730 caixas**.
Eram três coisas, todas medidas no dataset.

### 1. O alvo desaparecia antes de chegar ao modelo

A versão anterior reduzia o frame inteiro de 1080x2400 para
32x32:

| | tamanho |
|---|---|
| alvo mediano no frame | 95 x 94 px |
| **o mesmo alvo a 32x32** | **2.8 x 1.3 px** |

Não existe classificador que resolva 3 pixels. E aumentar a
resolução da tela não resolve — mesmo a 256x256 o alvo teria
22 x 10 px.

O que resolve é recortar o **objeto** e usar 32x32 **nele**.
Mesma resolução, conteúdo diferente: o alvo passa a preencher o
quadro.

### 2. O split era inválido

**56% das amostras têm `phash` repetido** — o bot age ~1x/s
numa tela quase estática. Com `train_test_split` aleatório, o
mesmo quadro caía em treino **e** em teste.

Isso não deixa a acurácia "um pouco otimista": deixa ela sem
significado. Agora o split é por grupo (`--split phash`), ou por
sessão inteira (`--split session`), que é o teste honesto de
generalizar para outra partida.

### 3. Não havia referência para comparar

"30%" sozinho não informa nada. As referências deste dataset:

| Referência | Acerto |
|---|---|
| chutar a classe majoritária (`open_box`) | 16,7% |
| **só olhar o estado da máquina** | **46,2%** |
| estado + categorias na tela | **96,0%** ← teto |

O modelo anterior estava **abaixo de olhar só o estado** — atrás
de uma regra de uma linha. O treino agora imprime essas linhas
sempre, e avisa em voz alta se o modelo não bater a majoritária.

---

## A arquitetura

O teto de 96% é a chave: se saber **o que está na tela** resolve
96% da escolha da ação, então o que precisa ser aprendido é
**detectar categoria** — e a ação sai da tabela de prioridade que
já existe em `src/core/state_machine.py` e já é testada.

```
frame → caixas candidatas → modelo diz a categoria
      → StateMachine escolhe a ação e o alvo
      → ActionManager toca
```

O modelo faz só a parte que é aprendizado. Prioridade,
coordenada, cooldown, espera de efeito e escada de fechamento
vêm do código que já funciona.

---

## Treinar

```bash
# recomendado
python IA/train_ai.py

# ensaio rápido, para ver se o encanamento funciona
python IA/train_ai.py --limit 2000 --trees 60

# só ações que funcionaram: não herda os erros do professor
python IA/train_ai.py --outcome changed negative

# teste mais duro: treina numa partida, avalia em outra
python IA/train_ai.py --split session

# comparação: tela inteira → ação (o jeito antigo, corrigido)
python IA/train_ai.py --kind action
```

O treino imprime acurácia, as referências, relatório por classe,
matriz de confusão e as maiores confusões em pares. A média
**macro** do relatório é o número que expõe classe rara
ignorada: `plane` tem 52 caixas contra 17.642 de `food`, e um
modelo que ignora `plane` ainda acerta 99,9%.

### Todas as flags do treino

Uma linha, um exemplo, o que ela muda de verdade.

| Flag | Exemplo | O que muda |
|---|---|---|
| `--kind` | `--kind category` | `category` (padrão): recorte de caixa → categoria, e a ação sai da tabela de prioridade. `--kind action`: tela inteira → ação, existe só para comparar. **O bot só usa `category`.** |
| `--split` | `--split session` | Define o **grupo** do split. `phash` (padrão): quadros iguais não cruzam treino/teste. `session`: a partida inteira vai para um lado — mede generalizar para outra partida. A queda entre os dois é o quanto o modelo decorou o restaurante. |
| `--test-ratio` | `--test-ratio 0.3` | Fração de **grupos** (não de amostras) para teste. Reduza se o teste sair vazio com `--split session`. A contagem final de amostras não bate exato com a fração, e isso é esperado. |
| `--outcome` | `--outcome changed negative` | Só amostras com esses resultados: `changed`, `unchanged`, `unknown`, `negative`. `changed negative` deixa de fora as ações do professor que **não funcionaram** — o modelo não herda os erros dele. |
| `--negatives` | `--negatives 4` | Recortes de fundo sorteados por frame, rotulados `background`. Sem eles todo pedaço de cenário viraria detecção. `--negatives 0` desliga. |
| `--trees` | `--trees 60` | Árvores da floresta (padrão 200). Menos = treino rápido para ensaio; mais = ganho pequeno e custo linear. |
| `--jobs` | `--jobs 4` | Núcleos. Padrão `-1` (todos). Baixe se a máquina ficar inutilizável durante o treino. |
| `--limit` | `--limit 2000` | Usa só as N primeiras amostras. É para testar o encanamento, **não** para medir: com poucas amostras as classes raras (`plane`, `fly`) desaparecem do treino. |
| `--seed` | `--seed 7` | Semente do split e da floresta. Trocar dá outra divisão — útil para ver se um resultado bom foi sorte. |
| `--dataset-root` | `--dataset-root dataset2` | Outra pasta de dataset. |
| `--model-out` | `--model-out IA/teste.joblib` | Grava em outro lugar, sem sobrescrever o modelo bom. Use sempre que estiver experimentando. |

Exemplos combinando:

```bash
# ensaio: rápido, descartável, sem tocar no modelo bom
python IA/train_ai.py --limit 2000 --trees 60 --model-out IA/ensaio.joblib

# o treino "de verdade", medido do jeito mais honesto
python IA/train_ai.py --split session --outcome changed negative

# o mesmo split com outra semente: o resultado se mantém?
python IA/train_ai.py --split session --seed 7
```

Ele **não instala dependência sozinho** — se faltar algo, ele
diz o comando. Instalar pacote como efeito colateral de treinar
muda o ambiente de quem só queria treinar.

---

## Rodar

```bash
# ver o que o modelo enxerga. NÃO toca no jogo.
python IA/bot_ai.py

# medir o modelo contra o template matching, caixa a caixa
python IA/bot_ai.py --compare

# revisar imagens já gravadas, sem device
python IA/bot_ai.py --demo --demo-limit 30

# deixar agir
python IA/bot_ai.py --auto --min-confidence 0.80
```

Sem `--auto` **nada é enviado ao device** — a execução é
substituída na origem, não com um `if` espalhado pelo loop.

### As duas fontes de candidatos

`--source templates` (padrão): o detector atual diz **onde**
olhar, o modelo diz **o que é**. Não é circular — serve para
medir o modelo em tela real antes de confiar nele, e é o caminho
que funciona hoje.

`--source proposer`: regiões por cor e contorno, o caminho para
largar os templates da coleta de referência (184 naquele snapshot).
**Experimental** — é a única parte
destes arquivos que não saiu de número medido no dataset.
Confira antes com:

```bash
python IA/bot_ai.py --source proposer --debug-proposals
```

### Todas as flags do bot

| Flag | Exemplo | O que muda |
|---|---|---|
| `--auto` | `--auto` | **A única que toca no jogo.** Sem ela, `execute` e `swipe` são substituídos na origem e nada é enviado ao device. |
| `--model` | `--model IA/ensaio.joblib` | Qual modelo carregar. O load confere o número de features e se as classes são categorias — um modelo de ação é barrado aqui. |
| `--source` | `--source proposer` | De onde vêm as caixas. `templates` (padrão): o detector atual diz onde olhar. `proposer`: cor e contorno, **experimental**. |
| `--min-confidence` | `--min-confidence 0.80` | Piso global de confiança (padrão 0.60). `fly`, `renovate` e `plane` têm piso próprio mais baixo e **ignoram** o valor global quando ele é maior — perder um `fly` custa um ciclo de reforma inteiro. |
| `--compare` | `--compare` | Compara a categoria do modelo com a do template matching, caixa a caixa, e imprime a concordância. Só faz sentido com `--source templates`. |
| `--demo` | `--demo` | Roda sobre imagens do dataset e mede o acerto contra os rótulos. **Sem device.** É a forma barata de saber se o modelo presta. |
| `--demo-limit` | `--demo-limit 30` | Quantas amostras no modo demo (padrão 50). |
| `--debug-proposals` | `--debug-proposals` | Desenha em cinza também as caixas candidatas **descartadas**. É o que mostra se o proposer está propondo lixo. |
| `--device` | `--device emulator-5554` | Serial do device. Sem isto, pergunta. |
| `--headless` | `--headless` | Sem janela. Para medir sem o custo do `imshow`. |
| `--dataset-root` | `--dataset-root dataset2` | Pasta do dataset, no modo demo. |

Exemplos combinando:

```bash
# medir um modelo recém-treinado, sem device nenhum
python IA/bot_ai.py --model IA/ensaio.joblib --demo --demo-limit 30

# ver o proposer trabalhando, com as caixas descartadas
python IA/bot_ai.py --source proposer --debug-proposals

# agir, mas exigente
python IA/bot_ai.py --auto --min-confidence 0.80
```

### O overlay durante a execução

| Campo | O que dizer quando parece travado |
|---|---|
| `lag` | Idade real do frame que gerou as detecções, em ms. Acima de 2000 a StateMachine **para de clicar** de propósito (`MAX_DETECTION_AGE`) — detector lento demais. |
| `no est` | Tempo no estado atual. Subindo sem parar = estado sem saída. Em `RENOVATE`, quase sempre é o modelo não conhecer `fly`/`renovate`. |
| `cand` / `det` | Candidatos propostos e detecções aceitas. `cand` alto com `det` zero = tudo caiu no `--min-confidence`. |
| `auto` | `nao` = nada é enviado ao device. |

---

## Ordem sugerida

1. `python tests/test_ia.py` — o encanamento está de pé?
2. `python IA/train_ai.py --limit 2000 --trees 60` — ensaio rápido
3. `python IA/train_ai.py` — treino de verdade
4. `python IA/bot_ai.py --demo --demo-limit 30` — acerto de
   categoria contra os rótulos, sem device
5. `python IA/bot_ai.py --compare` — modelo x professor, ao vivo
6. `python IA/bot_ai.py --auto` — só depois dos anteriores

---

## Se ainda ficar ruim

O relatório diz onde olhar:

- **acurácia perto de 46%** → está no nível de "olhar só o
  estado". As features não estão carregando informação; confira
  se o modelo é `category` e não `action`.
- **macro muito abaixo da acurácia** → classes raras ignoradas.
  `--outcome changed negative` reduz o desbalanceamento, ou
  colete mais amostras das raras (`plane` tem 52, `fly` tem 39).
- **confusão concentrada em um par** (ex. `food` × `box`) → é um
  problema específico e resolvível: mais margem no recorte
  (`PATCH_MARGIN`) dá mais contexto.
- **queda grande de `--split phash` para `--split session`** → o
  modelo decorou o restaurante em vez de aprender o objeto.
  Precisa de sessões mais variadas, não de mais amostras.
