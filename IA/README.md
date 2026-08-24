# Pasta IA

Treino e execução do modelo de visão.

| Arquivo | O quê |
|---|---|
| `features.py` | extração de features — **fonte única** para treino e inferência |
| `dataset_io.py` | leitura do índice, split sem vazamento, referências |
| `proposer.py` | proposta de regiões candidatas (**experimental**) |
| `train_ai.py` | treina o modelo |
| `bot_ai.py` | roda o modelo, mostrando ou agindo |
| `model.joblib` | modelo treinado |
| `model.joblib.meta.json` | como o modelo foi feito e quanto ele acerta |

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
largar os 184 templates. **Experimental** — é a única parte
destes arquivos que não saiu de número medido no dataset.
Confira antes com:

```bash
python IA/bot_ai.py --source proposer --debug-proposals
```

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
