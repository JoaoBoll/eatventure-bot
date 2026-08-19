# EatVenture AI

Bot de visão para EatVenture: captura o vídeo do device pelo
scrcpy, localiza os elementos por template matching e toca
via adb.

## Requisitos

- Python 3.12+
- [scrcpy](https://github.com/Genymobile/scrcpy) em `C:\scrcpy`
  (ajuste `SCRCPY_PATH` e `SCRCPY_SERVER_PATH` em
  [config.py](src/core/config.py))
- `adb` no PATH, com um único device conectado

```bash
pip install -r requirements.txt
```

## Executar

```bash
python src/main.py            # o bot
python src/test_main.py       # só o stream, sem detecção
```

### Qual device

Com mais de um device conectado, os dois programas **perguntam**:

```
Mais de um dispositivo conectado:

  1 - e2615705               22101320G            USB
  2 - 192.168.1.12:37889     22101320G            wifi (mesmo aparelho do 1)

Qual usar? [1]:
```

ENTER aceita a sugestão; também dá para digitar o número ou colar
o serial. A sugestão é sempre **USB** — a conexão wifi do adb cai
sozinha, e quando cai a captura morre no meio da sessão.

`(mesmo aparelho do 1)` sai quando os dois têm o mesmo
`ro.serialno`: é o caso comum de um celular ligado por USB **e**
por wifi ao mesmo tempo.

Para não ver a pergunta toda vez:

```bash
python src/main.py --device e2615705
```

ou fixe `DEVICE_SERIAL` em [config.py](src/core/config.py).

O serial escolhido atravessa o programa inteiro (captura **e**
toques). Isso não é detalhe: sem `-s`, com dois devices na lista
o adb recusa toda chamada com `more than one device` — e o erro
aparecia longe da causa, como "não conectou no stream".

Vale para as ferramentas também, que dependem do mesmo
`screencap`:

```bash
python tests/template_selector.py --device e2615705
python tests/android_screenshot.py --device e2615705
```

Sem `--device` elas perguntam igual.

### Quais janelas aparecem

Em [config.py](src/core/config.py), uma opção por janela —
as quatro combinações valem, inclusive as duas desligadas:

```python
SHOW_AI_VISION = True    # janela da IA, com as caixas de detecção
SHOW_SCRCPY    = False   # espelho do scrcpy
```

O `scrcpy.exe` é **apenas espelho** — a captura do bot não
passa por ele. O `ScreenCapture` sobe o próprio
`scrcpy-server` e lê o socket direto, e os toques vão por
adb. Então `SHOW_SCRCPY = False` não afeta o bot, só poupa um
decode H.264 e um render inteiros; nesse caso o `scrcpy.exe`
nem é iniciado. Com as duas desligadas o frame também deixa
de ser copiado, porque o overlay era o único lugar que
copiava.

O espelho continua útil para intervir na mão: a janela da IA
não aceita toque. Se ele for sua única janela,
`SCRCPY_EXTRA_ARGS` aceita coisas como `"--stay-awake"` e
`"--always-on-top"`.

**O `ESC` só funciona com a janela da IA aberta** — é ela que
recebe as teclas. Sem ela, encerre com `Ctrl+C`.

O que é desenhado dentro da janela da IA:

```python
SHOW_DETECTION_LABELS = True  # texto "categoria F=.. C=.." nas caixas
SHOW_FPS              = True  # os dois FPS, no canto
SHOW_DETECTION_LAG    = True  # idade do frame, no canto
```

Vale desligar os rótulos quando a tela tiver muita detecção
junta: agora que o detector acha várias instâncias por
categoria, o texto empilhado atrapalha mais do que ajuda.

### O HUD

```
captura   59.4 fps
detector   3.1 fps  318 ms
atraso     340 ms
```

- **captura** — frames por segundo chegando do device.
- **detector** — passadas do detector por segundo, e o custo
  médio de uma.
- **atraso** — idade do frame que gerou as detecções na tela.

Os dois FPS são números bem diferentes e é justamente a
comparação que diagnostica: captura em 60 com detector em 3
significa que o gargalo é a detecção, não a captura.

O detector fica **vermelho** abaixo de `1 / MAX_DETECTION_AGE`
— nesse ponto as detecções nascem mais velhas que o limite e
a máquina de estados para de clicar, em vez de acertar onde o
objeto estava. É o mesmo limite da checagem de idade, não um
número separado. O atraso fica vermelho na metade do limite.

## Estrutura

| Módulo | Responsabilidade |
|---|---|
| [capture/screen.py](src/capture/screen.py) | stream H.264 do scrcpy-server, frame versionado |
| [vision/detector.py](src/vision/detector.py) | template matching em dois estágios |
| [vision/worker.py](src/vision/worker.py) | roda o detector fora do loop principal |
| [core/state_machine.py](src/core/state_machine.py) | o que fazer com cada detecção |
| [actions/manager.py](src/actions/manager.py) | ação → toque, em thread própria |
| [actions/android.py](src/actions/android.py) | comandos adb |
| [core/devices.py](src/core/devices.py) | lista e escolhe o device |
| [core/config.py](src/core/config.py) | **todo** valor ajustável |
| [tools/regras.py](tools/regras.py) | imprime e valida as prioridades |
| [tools/renumerar.py](tools/renumerar.py) | compacta a numeração dos templates |
| [tools/selector_layout.py](tools/selector_layout.py) | escala e coordenadas do seletor |

## Ajustar templates e thresholds

Recortar um template novo:

```bash
python tests/template_selector.py
```

Com mais de um device conectado ele pergunta qual usar, igual ao
`main.py` (ver [Qual device](#qual-device)).

`R`/`F5` captura de novo, arrastar seleciona, `ENTER` salva
na categoria escolhida. O retângulo mostra o tamanho do recorte
em **pixels do device**, que é o que importa para o template.

### Tamanho da janela

A janela era fixa em 500x900, o que numa tela de device
1080x2400 dá escala 0.375 — 1 pixel na tela valia 2.7 pixels do
device, e recortar ficava impreciso.

Agora ela **acompanha a altura da sua tela** (80% dela por
padrão) e mantém a proporção da imagem. A tela do device aparece
inteira, de uma vez.

Medido num monitor 3440x1440:

| `SELECTOR_HEIGHT_FRACTION` | Janela | Escala | 1 px na tela = |
|---|---|---|---|
| antes (fixo 500x900) | 500x900 | 0.375 | 2.7 px |
| 0.70 | 453x1007 | 0.420 | 2.4 px |
| **0.80 (padrão)** | **518x1152** | **0.480** | **2.1 px** |
| 0.90 | 583x1296 | 0.540 | 1.9 px |
| 1.00 | 648x1440 | 0.600 | 1.7 px |

A escala nunca passa de 1.0: ampliar não cria detalhe, só deixa
o recorte borrado e mais difícil de acertar.

Ajustes em [config.py](src/core/config.py):

```python
SELECTOR_HEIGHT_FRACTION = 0.80   # fração da altura da tela
SELECTOR_WIDTH_FRACTION = 0.95    # só protege monitor deitado
SELECTOR_MAX_WIDTH = None         # None = detecta a tela
SELECTOR_MAX_HEIGHT = None
```

A matemática de escala e de coordenadas vive em
[tools/selector_layout.py](tools/selector_layout.py), separada da
GUI e coberta por [tests/test_selector_layout.py](tests/test_selector_layout.py)
— um erro de mapeamento aqui salvaria o recorte errado em
silêncio, e o template ruim só apareceria como "o bot clica no
lugar errado" semanas depois.

### Numeração automática

Ao salvar, o seletor **compacta a sequência** antes de gravar:
se falta o `item_005` entre 004 e 006, o 006 vira 005, o 007
vira 006, e o template novo entra no último número.

Assim "próximo número" volta a ser `len + 1`, sem ambiguidade —
antes uma lacuna fazia o cálculo apontar para um arquivo que já
existia, e o novo template **sobrescrevia** o antigo em silêncio.

Para compactar à mão (útil depois de apagar templates):

```bash
python tools/renumerar.py              # mostra o que mudaria
python tools/renumerar.py --aplicar    # renomeia
python tools/renumerar.py --aplicar --categoria food
```

O padrão é só mostrar, porque renomear é irreversível. Ele
processa em ordem crescente — o que torna colisão impossível,
já que o alvo de cada arquivo é sempre menor ou igual ao número
dele e quem ainda não foi processado tem número maior. Isso está
verificado por força bruta em `tests/test_renumerar.py`, sobre
todas as combinações de lacunas até 11 arquivos.

Renomear não invalida a regressão: o golden guarda categoria,
posição e confiança — não o nome do arquivo.

Depois de recortar ou mexer em threshold, **rode a
regressão**:

```bash
python tests/test_detection.py          # o que mudou?
python tests/test_detection.py --bench  # quanto custa uma passada?
python tests/test_detection.py --update # aceitar o novo esperado
```

Ela compara o resultado com [tests/golden/expectations.json](tests/golden/expectations.json)
e aponta o que deixou de ser detectado (`PERDEU`) e o que
passou a ser detectado (`EXTRA`, candidato a falso positivo).

Para ampliar a base:

```bash
python tests/android_screenshot.py --fixture nome.png
python tests/test_detection.py --update
```

e **confira o diff antes de comitar** — o arquivo golden vale
o que valer essa conferência. Telas onde nada deve ser
detectado são tão úteis quanto as outras: pegam falso
positivo.

Duas pastas de imagem, e a distinção importa:

| Pasta | O quê | Git |
|---|---|---|
| `tests/images/` | fixtures da regressão | versionado |
| `tests/capture/` | captura de trabalho do seletor | ignorado |

O `template_selector.py` grava na segunda. Ele antes gravava
sempre em `tests/images/screen.png`, então cada template
recortado sobrescrevia o fixture da regressão e invalidava a
linha de base sem avisar.

Nomeie fixture pelo que ele cobre — `new_point.png`,
`food_stations.png`, `up_food.png`. **Nunca `screen.png`**: é
o nome da captura de trabalho do seletor. Uma imagem em
`tests/images/` sem entrada no golden vira aviso, não falha,
então uma captura perdida ali não quebra o teste.

Enquanto estiver recortando templates, desligue
`VISION_FILTER_BY_STATE` em [config.py](src/core/config.py):
com o filtro ligado o overlay só mostra as categorias do
estado atual, o que é fácil confundir com "o detector parou
de achar".

## Testes

```bash
python tests/test_detection.py       # regressão de detecção
python tests/test_state_machine.py   # prioridade, cooldown, timeout
python tests/test_pipeline.py        # integração, com adb falso (inclui o modo headless)
python tests/test_renumerar.py       # compactação da numeração
python tests/test_selector_layout.py # coordenadas do seletor
python tests/test_devices.py         # escolha de device
```

Nenhum deles precisa de device.

## Prioridades da máquina de estados

A ordem das regras em [state_machine.py](src/core/state_machine.py)
**é** a prioridade. Em `NORMAL`:

| # | Categoria | Ação | Vai para |
|---|---|---|---|
| 1 | `open_store` | clica | — |
| 2 | `close` | clica no X | — |
| 3 | `gray_max` | toca ponto neutro | — |
| 4 | `up_food` | **segura** ponto neutro (0.4 s), escalando | — |
| 5 | `plane` | clica | `RENOVATE` |
| 6 | `build` | clica | `RENOVATE` |
| 7 | `upgrade` | clica | `UPGRADE` |
| 8 | `new_point` | clica | `NEW_POINT` |
| 9 | `box` | clica | — |
| 10 | `food` | clica | `FOOD` |

As quatro primeiras fecham o que não deveria estar aberto, e
por isso vêm antes de qualquer ação de jogo.

**Não existe número de prioridade escrito em lugar nenhum** — a
ordem da lista É a prioridade. Antes eram comentários
`# PRIORIDADE 1 → PLANE` espalhados por 200 linhas de `if`, o que
significava duas fontes de verdade: mudar a ordem sem mudar o
comentário deixava o código mentindo. Para reordenar, mova a
linha.

### Como acompanhar

```bash
python tools/regras.py
```

Imprime a numeração derivada da ordem, em todos os estados, com
timeout, comportamento de cada ação e destino. E confere três
coisas que o runtime não avisa:

- regra apontando para categoria **sem template** (regra morta,
  nunca pode disparar)
- regra apontando para **ação inexistente** na `ACTION_TABLE`
- template de categoria que **nenhuma regra usa** (custo de
  detecção sem uso)

Sai com código 1 se achar problema, então serve em hook de
commit. Foi ele que pegou a regra apontando para
`renovate_coin` depois da pasta ser renomeada para `renovate`.

Para acompanhar as decisões **em tempo real**, ponha
`LOG_LEVEL = "DEBUG"` em [config.py](src/core/config.py):

```
D [state] NORMAL prio 7/10: upgrade -> upgrade | na tela: food(0.97) box(0.93) upgrade(1.00)
I [state] NORMAL -> UPGRADE
```

A linha diz qual prioridade venceu **e o que ela venceu** — é o
que responde "por que clicou nisso e não naquilo".

`up_food` faz coisas **opostas** conforme o estado, e é de
propósito:

| Estado | O que faz | Onde | Duração | Gasta moeda? |
|---|---|---|---|---|
| `NORMAL` | fecha o painel | `DISMISS_POINT` | 0.4 s | não |
| `FOOD` | evolui a comida | centro da detecção | 4 s | **sim** |

É o estado que decide o significado da mesma detecção. Os dois
são toque mantido, e não tap: no ponto neutro um tap seco do adb
às vezes não fecha o painel. As durações são separadas
(`DISMISS_HOLD_DURATION` e `UPGRADE_FOOD_PRESS`) porque segurar
4 s só para fechar um painel congelaria a ação por 4 s.

Como `NORMAL` não tem timeout (é o estado base), uma regra que
dispara sem resolver nada repetiria para sempre — e achar algo
reseta a exploração, então o swipe não entra para salvar. Daí
o `REPEATED_ACTION_WARNING`: depois de N ações idênticas
seguidas sai um aviso no log.

### Fechar painel: por que existe uma escada

**Nenhum ponto fixo é seguro numa posição de rolagem qualquer.**
O `DISMISS_POINT`, hoje (10, 2200), fica encostado na barra de
botões de baixo, então ele mesmo pode ABRIR um painel. Se esse
painel mostra "max", a regra do `gray_max` toca o mesmo ponto,
que reabre: ciclo infinito, em que o ponto que causou o problema
é o usado para resolvê-lo.

Medindo os 4 fixtures, os únicos blocos realmente inertes ficam
na faixa de status do Android — onde tocar é pior. E o interior
muda por completo entre restaurantes.

**Mas existe uma posição de rolagem em que o canto de baixo fica
vazio: com a tela descida até o fim.** Daí a escada:

```
gray_max → gray_max → gray_max → scroll_bottom → gray_max → ...
```

| Config | O quê |
|---|---|
| `DISMISS_ACTIONS` | quais ações escalam (`dismiss`, `gray_max`) |
| `DISMISS_ATTEMPTS_BEFORE_SCROLL` | tentativas no ponto antes de rolar (3) |
| `SCROLL_BOTTOM_DIRECTION` | `"up"` — dedo para cima, **vista desce** |
| `SCROLL_BOTTOM_SWIPES` | 6, o bastante para chegar ao fim |

O ciclo **se repete** em vez de desistir: sai um aviso por
rodada, e a contagem zera quando alguma ação normal acontece (o
bot saiu do buraco) ou quando troca de estado.

> **Não use o BACK do Android aqui: neste jogo ele SAI DO JOGO.**
> O `android.back()` continua implementado, mas está fora da
> `ACTION_TABLE` de propósito, e dois testes falham se alguém o
> reintroduzir.

Swipe sozinho não resolve — swipe não fecha painel, só deixa o
loop mais lento. Ele serve para *chegar* na posição de rolagem
onde o ponto funciona.

## Quando não abre

Foram **três** causas empilhadas, e uma escondia a outra.

### 1. Socket sem `scid` (a causa de fundo)

No scrcpy 4.1 o socket abstrato do servidor é **sempre**
`scrcpy_<8 hex>` — não existe um `scrcpy` puro. O código
encaminhava para `localabstract:scrcpy`, que nunca existe:

```
$ adb shell cat /proc/net/unix | grep scrcpy
@scrcpy_7d7c122f
@scrcpy_6edc9dfd     ← o que o servidor cria
$ adb forward --list
tcp:27283 localabstract:scrcpy    ← para onde apontávamos
```

O adb aceita a conexão TCP e **só depois** tenta abrir o socket
no device. Se não existe, você recebe 0 bytes. Por isso o log
dizia "Socket conectado" e morria em seguida.

Corrigido: `scid` aleatório por execução, passado ao servidor e
usado no forward.

### 2. Porta compartilhada com o espelho

`scrcpy.exe` usa 27183-27199 por padrão, e a captura usava
27183. O sintoma no log do scrcpy era:

```
WARN: Could not listen on port 27183, retrying on 27184
```

**É isso que fazia "às vezes funcionar":** quando o espelho
ganhava a porta 27183, o forward dele apontava para um socket
`scrcpy_<scid>` válido — e a gente conectava no túnel *dele*,
recebendo o stream do espelho por acidente. Corrigir a porta
tirou essa muleta e expôs a causa nº 1.

Corrigido: `SCRCPY_PORT = 27283` e `SCRCPY_DEVICE_JAR` próprio
(os dois davam `adb push` no mesmo arquivo ao mesmo tempo, e o
nosso `stop()` apagava ele).

### 3. Conectar cedo demais, e desistir rápido demais

O servidor leva **~1,4 s** entre subir e servir o primeiro byte,
e passa disso com o espelho rodando. O código dormia 0,5 s e
tratava EOF como fatal.

A correção distingue dois casos que parecem iguais:

| Resultado do `recv` | Significa | O que fazer |
|---|---|---|
| **0 bytes** | socket abstrato não existe ainda | fechar e reconectar |
| **timeout** | servidor aceitou, só não mandou nada | **esperar no mesmo socket** |

Fechar no timeout derruba uma conexão boa — e o servidor aceita
**um cliente só**, então a segunda tentativa encontra servidor
morto.

### Verificado no device

| Cenário | Resultado |
|---|---|
| Captura sozinha | start em 1,49 s, 71 fps |
| Captura + espelho juntos | start em 1,98 s, 31 fps |
| Espelho sobrevive ao nosso `stop()` | sim |

### Outras causas

| Sintoma | Causa provável |
|---|---|
| Nenhum frame, tela do device apagada | o encoder captura o display; acorde a tela |
| `Erro ao enviar scrcpy-server` | `SCRCPY_SERVER_PATH` errado no config |
| Frames chegam mas nada é detectado | `VISION_FILTER_BY_STATE` ligado; desligue para ver todas as categorias |
| Bot vê mas não clica | veja o `atraso` no HUD — acima de `MAX_DETECTION_AGE` ele deixa de clicar de propósito |

Para investigar à mão:

```bash
adb shell cat /proc/net/unix | grep scrcpy   # o socket existe?
adb forward --list                            # para onde aponta?
```

Atalho de emergência: `SHOW_SCRCPY = False`. O espelho não é
usado pelo bot.

## Planos futuros

- [docs/detector-ia.md](docs/detector-ia.md) — trocar o template
  matching por um detector treinado: o que dá para aproveitar, o
  que morre, e o passo a passo.

## Custo do detector

Medido nas telas de `tests/images` (1080x2400, 43 templates):

| Situação | Tempo | FPS |
|---|---|---|
| busca direta em resolução cheia | 1697 ms | 0.6 |
| dois estágios, todas as categorias | 323 ms | 3.1 |
| dois estágios, estado `UPGRADE` (3 templates) | 27 ms | 37 |
| dois estágios, estado `FOOD` (4 templates) | 34 ms | 30 |

O estado `NORMAL` continua caro porque 39 dos 43 templates
são relevantes nele. O caminho para melhorar é
`CATEGORY_ROIS` em [config.py](src/core/config.py):
restringir cada categoria à parte da tela onde ela pode
aparecer corta o custo e o falso positivo junto. Está
deliberadamente vazio — ROI errada esconde detecção boa, e
isso precisa ser conferido no jogo.
