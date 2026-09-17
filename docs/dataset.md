# Dataset de treino

Gravação dos dados para treinar um detector no lugar do template
matching. Sobre *por que* trocar e o que se aproveita, ver
[detector-ia.md](detector-ia.md).

---

## O que é gravado, e por quê

Para cada ação do bot: o **frame** que motivou a decisão e os
**rótulos** que o template matcher produziu.

| O que | Por quê |
|---|---|
| **todas as caixas** do frame, com categoria | Um ponto por imagem é ambíguo quando há vários alvos e não ensina quantos existem. Com as caixas, a tarefa é detecção de objetos — a mesma do matcher, com muito mais rótulo por imagem. O ponto do clique se deriva da caixa; o contrário, não. |
| qual caixa **virou ação** (`acted`) | Separa "o que existe na tela" de "o que o bot escolheu". |
| o **ponto tocado**, em pixel do frame | Para ações de ponto fixo (`gray_max`) não há caixa: o alvo é uma coordenada. |
| o **resultado** (`outcome`) | O alvo saiu da tela? É o que permite treinar só nas ações que **funcionaram**. Sem isso o modelo herda todo erro do professor e nunca passa dele. |
| telas **sem alvo nenhum** (negativas) | Um detector treinado só em telas com alvo aprende que sempre existe um alvo. |

### Por que isso é honesto quanto ao limite

Isto é **behavior cloning**: o professor é o template matcher, e
rótulo nenhum fica melhor que ele. Serve porque o objetivo não é
decidir melhor — é **generalizar**. Hoje são 184 templates
recortados à mão (124 só de comida), e cada prato novo exige um
recorte novo. Uma rede reconhece o que nunca viu; template
matching, por construção, não.

O campo `outcome` é o que quebra parte desse teto: treinando só
em `changed`, os erros do professor ficam de fora.

---

## Ligar

Em [src/core/config.py](../src/core/config.py):

```python
DATASET_SAVE = True                      # já ligado
DATASET_DIR = PROJECT_ROOT / "dataset"     # destino, e origem do treino
DATASET_IMAGE_FORMAT = "jpg"               # já em jpg
```

Rode o bot normalmente. A pasta fica assim:

```
dataset/
├── samples.jsonl               índice — a FONTE DE VERDADE
└── images/
    └── 2026-08-21/
        ├── 405b15c4….png
        └── de97ab51….png
```

`dataset/` está no `.gitignore`: imagem pesa e não entra em
commit. Backup por cópia/`rsync`.

### Espaço em disco (medido)

Frame de 1080x2400:

| formato | por imagem | com o bot agindo ~1x/s |
|---|---|---|
| PNG | 2,10 MB | **7,6 GB/hora** |
| JPG q92 | 0,49 MB | **1,8 GB/hora** |

**JPG está configurado.** PNG não introduz artefato de
compressão; JPG é 4,3x menor. A sessão é longa, e artefato de
compressão é irrelevante para reconhecer botão de UI, que tem
borda forte e cor saturada.

A margem depende da origem do frame: `adb screencap` (sem perda)
dá 1,8x, e frame decodificado de H.264 — que é o que o recorder
grava — dá 4,3x, porque o H.264 introduz ruído em tudo, que o
PNG não comprime e o JPG descarta.

Há um teto: `DATASET_MAX_DISK_MB` (20 GB por padrão). Ao
estourar, a gravação para e avisa **uma vez** — o bot continua
jogando.

### Quais ações entram

`DATASET_ACTIONS` no config: **todas as 16 que o bot sabe
fazer**, mais os dois swipes de exploração.

| Comportamento | Ações |
|---|---|
| toque no centro da detecção | `click` (o build), `close`, `food`, `new_point`, `new_point_click`, `open_box`, `open_renovate`, `open_store_click`, `plane`, `renovate_click`, `upgrade` |
| vários toques | `upgrade_item` |
| toque longo | `upgrade_food` — cobre a evolução da comida inteira, em `NORMAL` e em `FOOD` |
| ponto fixo | `gray_max`, `dismiss` |
| sem alvo pontual | `scroll_bottom`, `swipe_up`, `swipe_down` |

`swipe_up`/`swipe_down` não estão em `ACTION_TABLE`: são a
exploração, que chama o `ActionManager` por outro caminho e por
isso tem gancho próprio em `_explore_screen`. A direção vai no
**nome** e não num campo novo, porque para o treino subir e
descer são rótulos diferentes — e assim o índice e o banco não
mudam de forma.

`tests/test_dataset.py::test_todas_as_acoes_do_bot_entram`
confere que nenhuma ação do `ACTION_TABLE` ficou de fora: uma
ação que o bot faz e o dataset ignora é um buraco que só aparece
quando o modelo não sabe fazer aquilo.

---

## O formato do índice

Uma linha JSON por amostra em `samples.jsonl`:

```json
{
  "id": "405b15c441eb4bfd8a9d533cf9a9f4d2",
  "session": "aa2f92cd…",
  "created_at": "2026-08-21T15:15:32.481+00:00",
  "image": "images/2026-08-21/405b15c4….png",
  "image_sha256": "9f2a…",
  "phash": "0110100…",
  "frame_width": 1080,
  "frame_height": 2400,
  "state": "NORMAL",
  "action": "open_box",
  "action_kind": "click",
  "click_x": 348,
  "click_y": 1544,
  "target_category": "box",
  "target_template": "/default/box/item_003.png",
  "target_confidence": 0.9712,
  "detect_lag_ms": 420,
  "outcome": "changed",
  "outcome_after_ms": 910,
  "cycle_count": 3,
  "boxes": [
    {"category":"box","template":"item_003.png","confidence":0.9712,
     "color_similarity":0.9931,"x":300,"y":1500,
     "width":96,"height":88,"acted":true},
    {"category":"food","template":"item_017.png","confidence":0.9410,
     "color_similarity":0.9802,"x":700,"y":900,
     "width":115,"height":96,"acted":false}
  ]
}
```

Notas que evitam armadilha:

- **`image` é relativo** à raiz do dataset. Mover a pasta não
  invalida o índice.
- **`click_x/y` estão no espaço do frame** — o mesmo da imagem
  gravada. Se o stream vier reduzido, o rótulo escala com ele.
- **`outcome`** é `changed` (o alvo saiu), `unchanged` (continua
  lá) ou `unknown` (não chegou frame para julgar, ou a sessão
  terminou antes).
- **`phash`** é hash de *similaridade*, não criptográfico: serve
  para telas quase iguais colidirem. `image_sha256` é a
  identidade exata do arquivo.
- **`action` é `null`** nas amostras negativas, e `boxes` vem
  vazio.
- **`target_template`** é o caminho completo da template usada
  (ex: `/default/box/item_003.png` ou `/1080x2400/food/Pizza.png`).
  Permite rastrear a origem exata de cada amostra para o treino.

O treino pode ler só isto. **O banco é opcional.**

---

## Banco de dados (opcional)

### O que vai, e o que não vai

**Vai:** metadado. **Não vai:** pixel.

Guardar imagem como BLOB parece organizado e é ruim na prática —
o treino leria gigabytes por época através do driver, e o dataset
deixaria de ser copiável com um `rsync`. O banco ganha o lugar
dele como **índice consultável**:

```sql
-- todo open_box que funcionou, com boa confiança
SELECT image
  FROM dataset_sample
 WHERE action = 'open_box'
   AND outcome = 'changed'
   AND target_confidence > 0.9;
```

### 1. Instalar o driver

```bash
pip install 'psycopg[binary]'
```

### 2. Criar as tabelas

O DDL executável está em **[schema.sql](schema.sql)** — três
tabelas (`dataset_session`, `dataset_sample`, `dataset_box`) e
sete índices:

```bash
psql -h 192.168.1.100 -U admin -d eatventure -f docs/schema.sql
```

Ou cole o conteúdo no pgAdmin/DBeaver com o banco aberto. É
seguro rodar de novo: tudo usa `IF NOT EXISTS`. No fim do script
há duas consultas de conferência (esperado: 3 tabelas, 7
índices).

O DDL vive **só** ali, e não repetido aqui: duas cópias seriam
duas fontes de verdade, e a divergência apareceria na primeira
execução com banco. `tests/test_dataset.py::test_ddl_cobre_as_colunas_do_insert`
confere que as colunas do `schema.sql` cobrem exatamente o que o
`PostgresStore` insere.

### 3. Configurar

```python
DATASET_DB_ENABLED = True
DATASET_DB_DSN = "postgresql://usuario:senha@host:5432/banco"
DATASET_DB_SCHEMA = "public"
DATASET_DB_BATCH = 20
```

### 4. Carregar

O caminho recomendado é **importar depois**, não gravar ao vivo:

```bash
python tools/dataset_import.py
python tools/dataset_import.py --dsn postgresql://... --jsonl outro/samples.jsonl
```

Funciona para sessões antigas, gravadas antes de existir banco.
Reimportar é seguro: chave primária + `ON CONFLICT DO NOTHING`.

Ligar `DATASET_DB_ENABLED` faz o bot indexar ao vivo, em lotes.
É só conveniência — **falha de banco nunca derruba a gravação**,
porque o arquivo é o que não se recupera depois.

---

## Consultas úteis

```sql
-- Quanto tenho de cada ação, e quanto disso funcionou?
SELECT action,
       COUNT(*)                                   AS total,
       COUNT(*) FILTER (WHERE outcome='changed')  AS ok,
       COUNT(*) FILTER (WHERE outcome='unchanged')AS falhou
  FROM dataset_sample
 GROUP BY action
 ORDER BY total DESC;
```

```sql
-- Categorias com poucas caixas: onde o dataset está fraco.
SELECT category, COUNT(*) AS caixas
  FROM dataset_box
 GROUP BY category
 ORDER BY caixas;
```

```sql
-- Ações que NÃO funcionaram: os erros do professor, para
-- inspecionar à mão ou deixar fora do treino.
SELECT s.image, s.action, s.target_template, s.target_confidence
  FROM dataset_sample s
 WHERE s.outcome = 'unchanged'
 ORDER BY s.created_at DESC
 LIMIT 50;
```

```sql
-- Quase-duplicatas entre sessões diferentes.
SELECT phash, COUNT(*), COUNT(DISTINCT session_id)
  FROM dataset_sample
 GROUP BY phash
HAVING COUNT(*) > 1
 ORDER BY 2 DESC;
```

---

## Por que não há deduplicação por imagem

A tentativa óbvia — descartar frames parecidos — foi implementada,
medida nos dados reais e **descartada**, porque não funciona aqui.

Diferença média de miniatura 8x8 entre pares de frames:

| | faixa |
|---|---|
| bot travado, mesma tela | 1,9 a 5,8 |
| jogo real, telas que devem ser mantidas | 2,5 a 59,8 |

**As faixas se sobrepõem.** A tela do jogo anima sozinha (contador
de dinheiro, personagens andando), então frames da mesma tela
diferem bastante; e telas genuinamente distintas podem diferir
pouco (uma caixa a menos no mesmo restaurante). Qualquer limite
que pegasse o caso travado jogaria fora amostra boa — e amostra
distinta perdida é pior que amostra repetida guardada.

O que separa limpo é o **resultado**, que já é gravado:

| sessão | amostras |
|---|---|
| travada | 35/35 `unchanged`, sempre a mesma ação |
| produtiva | `open_box` 5x seguidas, **todas** `changed` |

Daí o corte ser por sequência de `unchanged` na mesma ação
(`DATASET_MAX_UNCHANGED_STREAK = 3`), zerando quando a ação muda
ou quando dá `changed`. Reprocessando os 35 frames reais da
sessão travada pelo recorder atual: **35 viram 3, redução de
91%**, sem perder nenhuma amostra produtiva.

O `phash` continua no índice, mas como **metadado** — serve para
deduplicar offline depois, com métrica melhor e sem pressa.

### O caso das ações sem alvo

Para `swipe_*`, `scroll_bottom` e `dismiss` não existe "o alvo
saiu da tela": não há alvo. O `outcome` dessas vem da **mudança
de tela** (`DATASET_SCREEN_CHANGE = 8.0`).

Aqui a margem é confortável, ao contrário da deduplicação:
detectar mudança **grande** é fácil — uma rolagem move a vista
inteira (8,9 a 59,8 nos dados) enquanto o ruído de animação fica
abaixo de 5,8. O que não dá é separar "nada mudou" de "mudou
pouco", que é exatamente o que a deduplicação exigiria.

## Ler no treino, sem banco

```python
import json
from pathlib import Path

RAIZ = Path("dataset")

with (RAIZ / "samples.jsonl").open(encoding="utf-8") as f:
    amostras = [json.loads(linha) for linha in f if linha.strip()]

# Só o que funcionou: deixa de fora os erros do professor.
treino = [a for a in amostras if a["outcome"] == "changed"]

# Negativas: telas sem alvo. Sem elas o modelo aprende que
# sempre existe um alvo.
negativas = [a for a in amostras if a["action"] is None]

for a in treino:
    imagem = RAIZ / a["image"]
    caixas = [
        (b["category"], b["x"], b["y"], b["width"], b["height"])
        for b in a["boxes"]
    ]
```

Para YOLO, converter cada caixa para `classe cx cy w h`
normalizado por `frame_width`/`frame_height`. O
[detector-ia.md](detector-ia.md) tem a conta do letterbox, que é
onde é fácil errar por um fator de 2.

---

## Garantias, e o que elas custam

| Garantia | Como |
|---|---|
| O bot nunca espera o disco | Gravação em thread própria, fila limitada (`DATASET_QUEUE_SIZE`) que **descarta** quando enche. Perder amostra é aceitável; atrasar o bot não é. O log avisa quando descarta. |
| Imagem e rótulo são da MESMA passada | O loop usa `vision.get_input()`, que devolve o frame que **produziu** aquelas detecções — não o mais recente. |
| Só ação que saiu de verdade é gravada | O gancho está em `_act`, o único ponto que sabe que a ação não foi barrada por cooldown nem por worker ocupado. |
| Bot travado não enche o dataset | Corte por sequência de `unchanged` na mesma ação (`DATASET_MAX_UNCHANGED_STREAK`). Ver abaixo por que **não** é deduplicação por conteúdo. |
| Negativas não enchem o disco | Espaçadas por **tempo** (`DATASET_NEGATIVE_INTERVAL`), não por sorteio: `observe()` roda ~30x/s, e 5% por passada gravou 11 GB/hora no ensaio. |
| Banco fora do ar não perde dado | O JSONL é a fonte de verdade; o banco se preenche depois com `tools/dataset_import.py`. |

Coberto por [tests/test_dataset.py](../tests/test_dataset.py) (20
testes), incluindo: o ponto do clique bate com o toque real, o
ponto neutro escala em frame reduzido, `unchanged` não dispara
por outra instância da mesma categoria em outro canto da tela, e
a fila cheia descarta sem bloquear.
