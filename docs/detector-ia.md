# Trocar o template matching por um detector treinado

Memo de planejamento. Escrito em **18/08/2026**, com o projeto
em 50 templates / 12 classes / 4 fixtures.

> **Resumo em uma linha:** o que vale treinar é a **percepção**
> (substituir os 50 templates por um detector de objetos), não
> a **decisão** (a máquina de estados fica). É um fim de semana
> de trabalho, não um projeto de meses — e mais da metade do
> caminho já está pronto no repo.

---

## 1. Por que fazer isso

O problema não é a máquina de estados: são 540 linhas legíveis e
funciona. O problema é a esteira de templates.

Medições reais deste repo:

| Fato | Número |
|---|---|
| Templates de `food` | **25** (um por prato) |
| Templates totais | 50, em 12 classes |
| Templates de `food` recortados em 1 hora de trabalho | 8 |
| Detecções extras ao baixar o threshold de `food` de 0.90 → 0.70 | **zero** |
| Custo de uma passada, estado `NORMAL` | ~330 ms (3 FPS) |

A linha que importa é a penúltima: **os templates de comida não
se substituem**. Um template de pizza não acha um muffin, nem
com threshold frouxo. Então cobertura = você recortou aquele
prato, ou o bot é cego para ele. Cada prato novo do jogo é um
recorte novo, para sempre.

Um detector treinado aprende o *conceito* "estação de comida com
selo de upgrade" e acerta pratos que nunca viu.

### O que NÃO é motivo para fazer

- **Velocidade** não é o motivo principal. Você já ganha 30–40 FPS
  nos estados `UPGRADE`/`FOOD`/`RENOVATE` com o filtro de
  categorias. O detector treinado ajuda no `NORMAL` (330 ms), mas
  preencher `CATEGORY_ROIS` resolve boa parte disso de graça.
- **Não vai jogar melhor.** Ele só vê melhor. Quem decide continua
  sendo a `StateMachine`.

---

## 2. As quatro coisas que chamam de "IA"

| Caminho | Substitui | Custo | Veredito |
|---|---|---|---|
| **Detector de objetos** (YOLO) | os 50 templates | fim de semana | **faça este** |
| Imitação (behavioral cloning) | a máquina de estados | semanas | marginal |
| Reinforcement learning | joga sozinho | meses, e nem assim | não |
| VLM como oráculo | destravar tela desconhecida | horas | vale, pontual |

### Por que RL não dá (é estrutural, não falta de GPU)

- PPO em pixels precisa tipicamente de **10 a 50 milhões de passos**.
- **Não existe simulador.** Você está preso ao device em tempo real
  a ~30 FPS. 10M passos = ~93 h de tempo perfeito; na prática, meses.
- Recompensa de idle game chega em **minutos ou horas**. Atribuir
  crédito nesse horizonte é brutal.
- **Não existe reset.** Você não restaura o save para rodar
  episódios. Só isso já inviabiliza.

### Por que imitação é marginal

Precisaria de 10k+ pares (frame, toque) gravados de você jogando,
e entregaria uma política pior e menos depurável que a tabela de
regras que já existe. O jogo é reativo: a decisão certa é quase
sempre "clique no que apareceu", e isso a `StateMachine` já faz.

---

## 3. O que dá para aproveitar

### Aproveita inteiro

| Item | Por quê |
|---|---|
| `capture/screen.py` | o frame chega igual |
| `vision/worker.py` | roda qualquer detector fora do loop |
| `core/state_machine.py` | decisão não muda |
| `actions/` inteiro | toque não muda |
| `main.py` e o HUD | idem |
| **A interface `detect()`** | é o contrato inteiro, ver abaixo |
| **Os 50 recortes de template** | viram **fonte de dados sintéticos** |
| Os 4 fixtures + `tests/golden/` | viram conjunto de validação e comparador |
| `tools/template_selector.py` | vira ferramenta de anotação, com pouca mudança |
| `Detector.draw()` | desenha caixa igual, venha de onde vier |

O contrato é uma linha só:

```python
detect(frame, categories=None) -> [
    {"category", "x", "y", "width", "height", "confidence", ...},
    ...
]
```

Um `YoloDetector` com esse mesmo método entra no lugar **sem tocar**
em `VisionWorker`, `StateMachine`, `main.py` nem nos testes.

### Não aproveita (morre com o template matching)

| Item | Por quê |
|---|---|
| `CATEGORY_THRESHOLDS` | vira um `conf` único do modelo |
| `COLOR_THRESHOLD` e `_color_similarity` | o modelo já usa cor internamente |
| `COARSE_SCALE`, `COARSE_MARGIN`, `REFINE_SLACK` | busca em dois estágios não existe mais |
| `MIN_COARSE_SIDE`, o pré-cálculo de pirâmide | idem |
| `load_templates()` | o modelo é um arquivo `.onnx` |
| Suporte a máscara/alpha | irrelevante |

Guarde o `Detector` atual em vez de deletar: ele é o seu
comparador e o seu plano B.

### Aproveita com ressalva

| Item | Ressalva |
|---|---|
| `CATEGORY_ROIS` | continua útil contra falso positivo, mas não acelera mais |
| `wanted_categories()` | **deixa de economizar tempo** — ver abaixo |
| `NMS_IOU` | o YOLO já faz NMS; o seu passa a ser redundante |
| `MAX_MATCHES_PER_TEMPLATE` | vira `max_det` do modelo |

> **Mudança conceitual importante:** hoje filtrar categorias por
> estado economiza tempo de verdade (330 ms → 27 ms), porque cada
> template é uma passada de `matchTemplate` separada. Com YOLO é
> **uma única passada** que devolve todas as classes de uma vez —
> filtrar depois não economiza nada. O filtro continua útil para
> evitar reagir ao que não interessa, mas o ganho de performance
> desaparece. Em troca, o custo passa a ser **constante**: 50 ou
> 500 templates dá no mesmo.

---

## 4. Requisitos

### Software

```bash
pip install ultralytics          # treino
pip install onnxruntime          # inferência (ou use cv2.dnn)
```

Você **não** precisa de `torch` em produção se exportar para ONNX.
Isso importa: `torch` são ~2.5 GB, `onnxruntime` são ~50 MB.

### Hardware

- **Treino:** Colab grátis dá conta. Modelo nano em alguns milhares
  de imagens 2D é de minutos a uma hora.
- **Inferência:** CPU roda (30–80 ms), GPU roda folgado (5–15 ms).
  Sua máquina atual serve.

### Dados

| Fonte | Quantidade | Esforço |
|---|---|---|
| Sintético (colar recortes em fundos) | 2.000–5.000 imagens | **automático** |
| Real semi-anotado (detector atual + correção à mão) | 100–300 imagens | algumas horas |
| Validação (fixtures reais anotados à mão) | 30–50 imagens | 1–2 horas |

**Nunca valide em imagem sintética.** O modelo vai parecer perfeito
e falhar no jogo. Validação é em tela real, anotada à mão.

---

## 5. Passo a passo

### Fase 0 — não quebre nada (1 h)

O bot atual continua rodando durante todo o processo.

1. Restaure o `plane/item_002.png` se a exclusão foi sem querer —
   hoje a regra `("plane", "plane", RENOVATE)` em `NORMAL` está
   **morta**, porque não existe template da categoria.
2. Cresça a base de fixtures para 20–30 telas variadas:
   ```bash
   python tests/android_screenshot.py --fixture nome_descritivo.png
   python tests/test_detection.py --update
   ```
   Pegue tela de restaurante cheio, painel de upgrade aberto,
   renovate, loja, e **telas onde nada deve ser detectado**.
   Esta base é o que vai julgar o modelo depois. Vale o tempo.
3. Comite o estado atual funcionando. É o seu plano B.

### Fase 1 — gerador de dados sintéticos (meio dia)

O ponto que faz este projeto ser barato. Os sprites do jogo são
arte 2D achatada e determinística — não são fotos. Então colar
recortes em fundos gera anotação **perfeita e gratuita**, o que
não funcionaria com objetos fotográficos.

Esta etapa ainda não tem um script implementado no repositório. O gerador
abaixo é uma proposta de ferramenta futura, não um comando disponível:

1. Junte fundos: screenshots de gameplay **sem** os elementos de
   interesse (ou com eles apagados/desfocados).
2. Para cada imagem sintética:
   - sorteie de 1 a 8 recortes de `src/vision/templates/*/`
   - cole em posição aleatória, com escala de 0.85 a 1.15
   - varie brilho/contraste de leve (o jogo anima)
   - **grave a caixa** — você sabe onde colou, é a anotação
3. Salve no formato YOLO: `imagens/x.png` + `labels/x.txt`, cada
   linha `classe cx cy w h` normalizado de 0 a 1.
4. **Balanceie**: sorteie por classe, não por template. Senão
   `food` (25 templates) domina `upgrade` (1 template) em 25 para 1.
   Dado sintético permite balanceamento perfeito — dado real não.

Cuidados que evitam um modelo inútil:
- **Cole em posições plausíveis.** `close` no meio do cenário ensina
  o modelo a achar X onde não existe.
- **Inclua negativos:** imagens de fundo sem nenhum objeto.
- **Inclua o caso difícil que já conhecemos:** o selo vermelho de
  upgrade aparece na estação de comida **e** no painel de promo
  "Double". Se você não anotar os dois de forma consistente, o
  modelo vai confundir. Isso é uma decisão de **anotação** — e é
  onde o modelo ganha do template matching, porque ele pode
  aprender a distinção pelo contexto em volta.

### Fase 2 — dataset real (algumas horas)

1. Rode o detector atual sobre 100–300 screenshots e exporte no
   formato YOLO (ele já devolve caixa + classe).
2. Abra num anotador (`labelImg`, Label Studio, ou adapte o
   `template_selector.py`, que já tem seleção por arrastar) e
   **corrija**: adicione o que ele perdeu, apague o que ele
   inventou. Corrigir é muito mais rápido que anotar do zero.
3. Separe: treino = sintético + 80% do real; validação = 20% do
   real, nunca sintético.

### Fase 3 — treinar (1 h, no Colab)

```python
from ultralytics import YOLO

model = YOLO("yolo11n.pt")          # nano: leve e suficiente

model.train(
    data="eatventure.yaml",
    epochs=100,
    imgsz=(1440, 640),   # RETANGULAR — ver armadilha nº 1
    rect=True,
    batch=16,
)

model.export(format="onnx", imgsz=(1440, 640))
```

### Fase 4 — `YoloDetector` atrás da mesma interface (2 h)

`src/vision/yolo_detector.py`, com o **mesmo** `detect()`:

```python
class YoloDetector:
    def detect(self, frame, categories=None):
        # 1. redimensiona para (1440, 640)
        # 2. onnxruntime.run
        # 3. converte saída para coordenada do frame original
        # 4. filtra por `categories` se vier
        # 5. devolve a MESMA lista de dicts, ordenada por confiança
        ...

    def draw(self, frame, detections, stats=None):
        # reaproveite o do Detector atual
```

Ponto crítico: devolver coordenada no espaço do **frame original**,
não do tensor redimensionado. Se errar aqui, o bot clica torto — e
é o bug mais fácil de cometer nesta fase.

### Fase 5 — o portão de decisão (30 min)

```bash
python tests/test_detection.py            # templates: baseline
DETECTOR=yolo python tests/test_detection.py   # modelo
```

Compare **nas suas próprias telas**. O modelo só entra se:

- achar tudo que os templates achavam (nenhum `PERDEU`), **e**
- achar coisa que os templates não achavam (prato não recortado), **e**
- não inventar (`EXTRA` que não existe na tela).

Se falhar, volte para a Fase 1 com mais dados. Não vá para
produção com um modelo que perde detecção — o bot fica pior que
hoje.

### Fase 6 — produção com rede de segurança (1 h)

1. `DETECTOR_TYPE = "yolo" | "template"` no `config.py`.
2. Rode com o HUD ligado e olhe o `lag` e o `detector fps`.
3. Deixe o `Detector` de template no repo. Se o modelo falhar numa
   tela nova, trocar uma linha do config te devolve o bot que
   funciona.

---

## 6. Armadilhas

### 1. Aspecto alto — a mais importante

Sua tela é 1080x2400 (proporção 1:2,22). O YOLO por padrão faz
letterbox **quadrado**, e isso destrói os elementos pequenos:

| Entrada | Escala | `up_upgrade` (32 px) vira |
|---|---|---|
| 640x640 quadrado (padrão) | 0,267 | **8,5 px** — inviável |
| 1280x1280 quadrado | 0,533 | 17 px — limítrofe |
| **640x1440 retangular** | **0,600** | **19 px** — funciona |
| 960x2112 retangular | 0,880 | 28 px — ótimo, mas lento |

Use **entrada retangular** (`imgsz=(1440, 640)`, `rect=True`), ou
fatie o frame em 2 pedaços com sobreposição. Se você treinar no
640 quadrado padrão, o modelo vai simplesmente não ver
`up_upgrade`, `up_food` e `renovate_coin`, e você vai culpar os
dados.

### 2. Desbalanceamento de classes

`food` tem 25 templates, `upgrade` tem 1. Se gerar dado sintético
sorteando template, a proporção vira 25:1 e o modelo ignora as
classes raras. **Sorteie por classe.**

### 3. Overfitting em sintético

Modelo com 0.99 de mAP no sintético e cego no jogo é o resultado
padrão de quem valida errado. Validação é **só em tela real**.

### 4. Ausência de detecção

Template matching falha de forma previsível. Rede neural falha de
forma estranha — inclusive inventando caixa onde não há nada.
O `REPEATED_ACTION_WARNING` que já existe no `state_machine.py`
ajuda a perceber, mas considere um piso de confiança mais alto
para ações destrutivas (renovate, por exemplo).

### 5. Peso da dependência

`torch` são ~2,5 GB. Exporte para ONNX e use `onnxruntime` (~50 MB)
em produção. Só o ambiente de treino precisa do `torch`, e ele pode
viver só no Colab.

---

## 7. O híbrido que vale, independente do resto

Nem template matching nem YOLO resolvem **tela desconhecida** —
um evento novo, uma promoção que você nunca viu. Para isso um VLM
serve bem, e o custo é irrelevante porque roda raramente:

Quando disparar o `REPEATED_ACTION_WARNING` (bot repetindo a mesma
ação sem resolver), mande o screenshot e pergunte "o que eu toco
para sair desta tela?". É caro por chamada e lento, mas acontece
uma vez a cada muitas horas — e resolve exatamente a classe de
problema que os outros dois não resolvem.

Isso é independente do detector: dá para fazer hoje, com o bot como
está.

---

## 8. Ordem de custo-benefício

Se for fazer só uma coisa, faça de cima para baixo:

1. **`CATEGORY_ROIS`** — de graça, resolve o custo do `NORMAL` e
   mata falso positivo. Não precisa de IA nenhuma.
2. **Crescer os fixtures para 20–30 telas** — é o que torna
   qualquer mudança futura mensurável, inclusive a do modelo.
3. **Detector treinado** — acaba com a esteira de templates.
4. **VLM para destravar** — cobre o caso que nada mais cobre.
5. Imitação / RL — não.

---

## 9. Estado do repo que este plano assume

- `Detector.detect(frame, categories=None)` é o contrato — trocar o
  backend não toca em mais nada.
- `tests/test_detection.py` compara detecção contra
  `tests/golden/expectations.json`, com `--update` e `--bench`.
- Fixtures em `tests/images/` (versionados), captura de trabalho em
  `tests/capture/` (ignorada).
- 4 fixtures / 7 detecções no golden. **Precisa crescer** antes de
  julgar um modelo.
- 50 templates em 12 classes, e `plane` sem template (regra morta).
