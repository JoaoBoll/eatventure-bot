"""
Configuração central do EatVenture AI.

Todo valor ajustável do projeto mora aqui.
Nenhum outro módulo deve ter número mágico.
"""

from pathlib import Path


# =========================================================
# CAMINHOS
# =========================================================

# Raiz do repositório (dois níveis acima de src/core/).
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Device a usar, como aparece em `adb devices`.
#
# None = decide em runtime: se houver só um conectado, usa ele;
# se houver vários, PERGUNTA no terminal.
#
# Vale fixar aqui quando o celular fica ligado por USB e por
# wifi ao mesmo tempo: o `adb devices` lista o mesmo aparelho
# duas vezes, e a pergunta aparece em toda execução.
#
# Prefira o serial do USB. A conexão wifi do adb cai sozinha, e
# quando cai a captura morre no meio da sessão.
#
#   DEVICE_SERIAL = "e2615705"
DEVICE_SERIAL = None

SCRCPY_PATH = r"C:\scrcpy\scrcpy.exe"

SCRCPY_SERVER_PATH = r"C:\scrcpy\scrcpy-server"

SCRCPY_SERVER_VERSION = "4.1"

# Porta local do NOSSO túnel de captura.
#
# NÃO use a faixa 27183-27199: é a default do scrcpy.exe, e com
# o espelho ligado os dois brigam pela mesma porta. O sintoma é
# intermitente ("às vezes não abre") e aparece no log do scrcpy:
#
#   WARN: Could not listen on port 27183, retrying on 27184
#
# Quem perde a corrida conecta no túnel errado e o stream morre
# na hora ("Stream encerrado").
SCRCPY_PORT = 27283

# Caminho do servidor NO DEVICE.
#
# Diferente do que o scrcpy.exe usa (/data/local/tmp/
# scrcpy-server.jar) de propósito: os dois faziam adb push do
# mesmo arquivo ao mesmo tempo, e o nosso stop() apagava ele —
# o que corrompe ou derruba o espelho de forma aleatória.
SCRCPY_DEVICE_JAR = "/data/local/tmp/eatventure-server.jar"

# Tempo para CONSEGUIR o primeiro byte do stream.
#
# O servidor leva ~1.4 s (medido) entre subir e começar a servir.
# Antes o código dormia 0.5 s, conectava, recebia 0 bytes e
# desistia — o adb aceita a conexão TCP e só depois tenta o
# socket abstrato, então "conectou" não significa nada.
CAPTURE_CONNECT_TIMEOUT = 12.0

# Tempo para o primeiro frame DECODIFICADO depois de conectar.
CAPTURE_START_TIMEOUT = 6.0


# =========================================================
# VISUALIZAÇÃO
# =========================================================
#
# Cada janela liga e desliga por conta própria. As quatro
# combinações são válidas, inclusive as duas desligadas
# (headless).
#

# Janela da IA: o frame com as caixas de detecção desenhadas.
# É a única que recebe teclado, então o ESC só funciona com
# ela ligada. Desligada, encerre com Ctrl+C.
SHOW_AI_VISION = True

# Espelho do scrcpy (scrcpy.exe).
#
# É APENAS espelho: a captura do bot não passa por ele. O
# ScreenCapture sobe o próprio scrcpy-server e lê o socket
# direto, e os toques vão por adb. Desligar não afeta o bot,
# só economiza um decode H.264 e um render inteiros.
#
# Ligado é útil para intervir na mão, porque a janela da IA
# não aceita toque.
SHOW_SCRCPY = False

# Texto "categoria F=.. C=.." em cima de cada caixa.
#
# Desligue quando a tela tiver muita detecção junta: agora
# que o detector acha várias instâncias por categoria, o
# texto empilhado atrapalha mais do que ajuda.
SHOW_DETECTION_LABELS = True

# Idade do frame que gerou as detecções, no canto da janela.
# Ajuda a distinguir "detecção errada" de "detecção atrasada".
SHOW_DETECTION_LAG = True

# TEMPO CORRIDO entre reformas no canto da janela.
#
# Mede o que o bot existe para fazer: fechar o ciclo de um
# restaurante. Os FPS dizem se a visão está saudável, mas não
# dizem se o bot está PROGREDINDO — ele pode estar a 30 fps
# clicando em nada há vinte minutos.
SHOW_CYCLE_TIME = True

# Categorias que marcam o fim de um ciclo.
#
# `build` e `plane` são as duas portas para RENOVATE, ou seja,
# as duas formas de o bot passar de restaurante. Qualquer uma
# reinicia o cronômetro.
CYCLE_CATEGORIES = {"build", "plane"}

# Múltiplo do último ciclo a partir do qual o tempo corrido fica
# vermelho.
#
# Não existe "tempo normal" fixo: cada restaurante leva o que
# leva, e vai ficando mais lento. Então a referência é o ciclo
# ANTERIOR, não um número inventado. Passar de 3x dele é sinal
# de bot travado, não de restaurante difícil.
CYCLE_STALL_FACTOR = 3.0

# Bateria do device no canto da janela.
#
# Vale mais do que parece: o bot roda por horas, e sessão que
# morre no meio por bateria descarregada não deixa rastro no log
# — só para de agir.
SHOW_BATTERY = True

# Intervalo entre leituras da bateria (segundos).
#
# `dumpsys battery` custa ~56 ms, mais de 3x uma passada do
# detector em UPGRADE. Nunca é chamado do loop de render: roda
# em thread própria e o HUD lê o último valor.
#
# 30 s é folgado de propósito — 1% de bateria leva vários
# minutos, então ler mais rápido só gasta adb.
BATTERY_POLL_INTERVAL = 30.0

# Abaixo disto o número fica vermelho no HUD.
BATTERY_WARNING_LEVEL = 20

# FPS no canto da janela, em duas linhas:
#
#   captura  = frames por segundo chegando do device
#   detector = passadas do detector por segundo
#
# São números MUITO diferentes e medem coisas diferentes. A
# captura pode estar em 60 e o detector em 3: quem manda na
# reação do bot é o segundo. Ver os dois juntos é o que
# mostra onde está o gargalo.
SHOW_FPS = True

# Tamanho da janela da IA
WINDOW_WIDTH = 500
WINDOW_HEIGHT = 900

AI_WINDOW_NAME = "EatVenture - AI"

AI_WINDOW_POSITION = (600, 50)

# Argumentos extras para o espelho do scrcpy.
#
# Úteis quando ele é a sua única janela:
#   "--stay-awake"        device não dorme enquanto plugado
#   "--window-borderless"
#   "--always-on-top"
SCRCPY_EXTRA_ARGS = []


# =========================================================
# SELETOR DE TEMPLATES
# =========================================================
#
# A janela era fixa em 500x900: numa tela de device 1080x2400
# isso dá escala 0.375, ou seja 1 pixel na janela valendo 2.7
# pixels do device — recortar template fica impreciso.
#
# Agora a janela acompanha a ALTURA da tela e mantém a
# proporção da imagem. A tela do device aparece inteira, de uma
# vez.
#
# Numa tela de 1440 de altura:
#
#   0.80 -> janela 518x1152, escala 0.480
#   0.90 -> janela 583x1296, escala 0.540
#
# A escala não passa de 1.0: ampliar não cria detalhe, só
# deixa o recorte borrado e mais difícil de acertar.

# Fração da ALTURA da tela que a janela ocupa.
SELECTOR_HEIGHT_FRACTION = 0.80

# Fração da LARGURA. Não deve limitar nada numa tela alta e
# estreita — está aqui como proteção para monitor deitado.
SELECTOR_WIDTH_FRACTION = 0.95

# Sobrepõe a detecção de tela. None = detecta (Windows).
SELECTOR_MAX_WIDTH = None
SELECTOR_MAX_HEIGHT = None

# Usado quando a detecção de tela não funciona.
SELECTOR_FALLBACK_WIDTH = 1600
SELECTOR_FALLBACK_HEIGHT = 1000


# =========================================================
# LOG
# =========================================================

# DEBUG / INFO / WARNING
LOG_LEVEL = "INFO"


# =========================================================
# RESOLUÇÃO DE REFERÊNCIA
# =========================================================
#
# Os templates foram recortados de screenshots nesta
# resolução, e as coordenadas fixas abaixo também.
#
# Se o device (ou o stream do scrcpy) vier em outra
# resolução, tudo é convertido em runtime a partir daqui.
#

REFERENCE_WIDTH = 1080
REFERENCE_HEIGHT = 2400


# =========================================================
# DETECÇÃO
# =========================================================

# Similaridade mínima de formato (padrão).
SHAPE_THRESHOLD = 0.80

# Similaridade mínima de cor.
#
# ATENÇÃO: a métrica de cor foi reescrita (matiz circular
# + peso por saturação). Os valores não são comparáveis
# aos da métrica antiga — este threshold foi recalibrado.
COLOR_THRESHOLD = 0.80

# Threshold de formato por categoria.
# Categoria ausente aqui usa SHAPE_THRESHOLD.
#
# Esta é a ÚNICA fonte de verdade dos thresholds.
# A StateMachine não refiltra por confiança.
CATEGORY_THRESHOLDS = {
    "build": 0.95,
    "new_point": 0.97,
    "food": 0.95,
    "upgrade": 0.98,
    "up_food": 0.90,
    "up_upgrade": 0.90,
    "close": 0.85,
    "plane": 0.98,
    "box": 0.95,
}


# ---------------------------------------------------------
# BUSCA EM DOIS ESTÁGIOS
# ---------------------------------------------------------
#
# Estágio 1 (grosso): procura numa cópia reduzida do frame.
# Estágio 2 (fino):   reconfirma cada candidato em resolução
#                     cheia, numa janela pequena.
#
# O resultado final tem SEMPRE a confiança e a coordenada de
# resolução cheia, então os thresholds acima continuam valendo.
# O estágio grosso só serve para descartar rapidamente as
# regiões sem chance.
#
# MEDIDO nas duas telas de tests/images, com 43 templates:
#
#   busca direta (versão antiga) ... 1697 ms
#   escala global 0.50 ............  562 ms
#   escala global 0.40 ............  323 ms
#

# TETO da escala do estágio grosso — não a escala usada.
#
# A escala em que um template sobrevive à redução depende do
# TAMANHO dele: abaixo de MIN_COARSE_SIDE (12 px) o estágio
# grosso é abandonado e a busca cai em resolução cheia, que é
# justamente a lenta. Uma escala global fica travada pelo MENOR
# template de todos (up_upgrade, 32 px -> 0.40), e os grandes
# pagam a conta.
#
# MEDIDO nos 4 fixtures, com os 106 templates atuais:
#
#   escala global 0.40 ........ 3409 ms
#   escala por template ....... 1046 ms   (3.3x)
#
# E as detecções (coordenada E confiança) ficam IDÊNTICAS: o
# custo extra não estava comprando precisão nenhuma.
#
# O padrão é exato, não empírico — o custo explode PRECISAMENTE
# quando a escala cai abaixo de 12/menor_lado. Por isso a escala
# é derivada daí, e não existe tabela por categoria para manter
# na mão.
#
# 75 dos 106 templates são food (mediana 80x93): eles aguentam
# 0.15-0.20, contra o 0.40 que a escala global impunha.
COARSE_SCALE = 0.40

# Piso da escala derivada.
#
# Não é sobre precisão: a 0.10 as detecções continuaram
# idênticas. É sobre o frame reduzido ficar tão pequeno que
# aparece candidato demais, e cada candidato custa uma
# reconfirmação em resolução cheia.
COARSE_SCALE_MIN = 0.10

# Grade da escala derivada.
#
# Arredondar PARA CIMA na grade dá a folga em relação ao piso
# teórico, e limita quantos tamanhos distintos de frame reduzido
# existem — cada um custa um resize por passada (7 escalas =
# 7.9 ms medidos, contra ~700 ms de busca).
COARSE_SCALE_STEP = 0.05

# Folga do threshold no estágio grosso.
#
# A redução do frame degrada a confiança, então o estágio
# grosso precisa ser mais permissivo que o fino, ou
# descartaria matches que o fino aprovaria.
COARSE_MARGIN = 0.18

# Raio (em pixels de resolução cheia) da janela de
# reconfirmação em volta de cada candidato.
REFINE_SLACK = 8

# Máximo de candidatos por template, por frame.
# Protege contra um threshold baixo virar milhares de pontos.
MAX_MATCHES_PER_TEMPLATE = 12

# Sobreposição a partir da qual duas detecções da mesma
# categoria são consideradas o mesmo objeto.
NMS_IOU = 0.35


# ---------------------------------------------------------
# REGIÕES DE INTERESSE (ROI)
# ---------------------------------------------------------
#
# Restringe a busca de uma categoria a um pedaço da tela.
#
# É o maior ganho disponível, em velocidade E em precisão:
# procurar o "X" de fechar no meio do cenário só produz
# custo e falso positivo.
#
# Formato: (x1, y1, x2, y2) em FRAÇÃO da tela (0.0 a 1.0),
# então independe de resolução.
#
# Categoria ausente = busca em toda a tela.
#
# TODO: preencher conforme o layout do jogo. Exemplo:
#
#     "close":    (0.60, 0.00, 1.00, 0.20),
#     "gray_max": (0.00, 0.75, 1.00, 1.00),
#
# Deixado vazio de propósito: uma ROI errada esconde
# detecção boa, e isso precisa ser conferido na tela real.
#
CATEGORY_ROIS = {}


# =========================================================
# AÇÕES
# =========================================================

# Cliques repetidos do upgrade de item.
UPGRADE_ITEM_CLICKS = 5
UPGRADE_ITEM_DELAY = (0.08, 0.18)

# Long press do upgrade de comida (segundos).
UPGRADE_FOOD_PRESS = 4.0

# Ponto neutro para dispensar painel que abriu sem querer.
# Em coordenadas da resolução de referência.
DISMISS_POINT = (10, 2200)

# ESCALONAMENTO DE FECHAMENTO
#
# O problema: o "ponto seguro" não é seguro. Ele pode abrir um
# painel, esse painel mostra "max", a regra do gray_max toca o
# ponto de novo, e o ciclo nunca termina — o ponto que causou o
# painel é o mesmo usado para fechá-lo.
#
# Medindo os fixtures, os únicos blocos realmente inertes ficam
# na faixa de status do Android, e o interior muda por completo
# entre restaurantes. Ou seja: em UMA POSIÇÃO DE ROLAGEM
# qualquer, não existe coordenada boa.
#
# Mas existe uma posição de rolagem em que o canto de baixo FICA
# vazio: com a tela descida até o fim. Então a escada é:
#
#   1. tenta o ponto N vezes
#   2. rola a tela até o fim
#   3. tenta o ponto outra vez
#   (repete)
#
# NÃO use o BACK do Android aqui: neste jogo ele SAI DO JOGO.
# Foi testado. É por isso que o android.back() existe mas não
# está em ACTION_TABLE.
#
# Swipe sozinho também não resolve: swipe não fecha painel, só
# deixa o loop mais lento. Ele serve para chegar na posição de
# rolagem onde o ponto funciona.

# Ações que servem para fechar painel, e por isso escalam.
DISMISS_ACTIONS = {"dismiss", "gray_max"}

# Tentativas no ponto antes de rolar a tela até o fim.
DISMISS_ATTEMPTS_BEFORE_SCROLL = 5

# Rolagem de escape.
#
# "up" = dedo para cima = a VISTA DESCE. Contraintuitivo, mas é
# a convenção do ActionManager: swipe "up" vai de SWIPE_Y para
# SWIPE_Y - SWIPE_DISTANCE.
SCROLL_BOTTOM_DIRECTION = "up"

# Swipes na sequência de escape. Precisa ser o bastante para
# chegar ao fim de qualquer restaurante.
SCROLL_BOTTOM_SWIPES = 6

# Pausa entre os swipes da sequência, para o jogo animar.
SCROLL_BOTTOM_PAUSE = 0.25

# Duração do toque MANTIDO no ponto neutro, em segundos.
#
# Da ação "dismiss": um tap seco do adb às vezes não registra
# no jogo, e o painel não fecha.
#
# NENHUMA regra usa "dismiss" hoje — up_food em NORMAL faz
# upgrade_food, por escolha do dono do projeto. A ação segue
# implementada e testada, pronta para voltar às regras.
#
# 0.4s é deliberado: bem acima de um tap falho, e abaixo dos
# ~500ms que o Android trata como long press — não queremos
# disparar gesto de segurar, só garantir que o toque registre.
DISMISS_HOLD_DURATION = 0.4

# Swipe de exploração, em coordenadas de referência.
SWIPE_X = 540
SWIPE_Y = 1200
SWIPE_DISTANCE = 600
SWIPE_DURATION_MS = 500


# =========================================================
# VISION WORKER
# =========================================================

# Pausa entre passadas do detector.
#
# NÃO é o período de análise: uma passada custa muito mais
# que isso. Serve só para o worker não monopolizar a CPU
# quando o detector estiver rápido.
VISION_INTERVAL = 0.01

# Restringe a busca às categorias que o estado atual usa.
#
# Desligue enquanto estiver recortando templates ou
# ajustando thresholds: com o filtro ligado, o overlay só
# mostra as categorias do estado, e é fácil confundir isso
# com "o detector parou de achar".
VISION_FILTER_BY_STATE = True

# Idade máxima de uma detecção para a StateMachine agir
# sobre ela (segundos).
#
# Detecção velha = clique em coordenada que já mudou.
# None desliga a checagem.
MAX_DETECTION_AGE = 2.0


# =========================================================
# STATE MACHINE
# =========================================================

# Intervalo mínimo entre ações.
ACTION_COOLDOWN = 0.5

# Tempo que o JOGO leva para reagir a um toque, em segundos.
#
# Resolve o duplo toque: fechar o "MAX" e tocar de novo no mesmo
# ponto, o que REABRE o painel.
#
# Duas coisas conspiram. Primeiro, o cooldown (0.5 s) libera
# antes de o detector produzir um frame posterior à ação, porque
# o atraso dele é ~0.535 s — a máquina decidia sobre uma tela de
# ANTES do próprio toque. Segundo, mesmo um frame posterior ao
# toque ainda mostra o painel enquanto a animação de fechar não
# terminou.
#
# Por isso a condição não é temporal, é causal: só age sobre
# frame CAPTURADO pelo menos ACTION_SETTLE depois da última
# ação. Aumentar o cooldown não resolveria — um detector mais
# lento voltaria a estourar a margem.
#
# 0.4 s cobre animação de painel de jogo (tipicamente
# 0.15-0.3 s) com folga. Se o duplo toque voltar a aparecer,
# aumente: o custo é o bot agir um pouco mais devagar, contra
# uma ação errada que desfaz a anterior.
ACTION_SETTLE = 0.4

# Tempo sem detectar nada antes de fazer swipe.
EXPLORATION_DELAY = 5.0

# Swipes consecutivos antes de inverter a direção.
MAX_SWIPES = 5

# Direção do primeiro swipe: "up" ou "down".
#
# Depende de onde o jogo costuma deixar o conteúdo fora da
# tela — é ajuste de jogo, então mora aqui e não no código.
SWIPE_START_DIRECTION = "down"

# Espera pelo próximo "up food" depois do long press.
UP_FOOD_WAIT = 2.0

# Quantas vezes a MESMA ação pode repetir em sequência antes
# de virar aviso no log.
#
# NORMAL não tem timeout (é o estado base), então uma regra
# que dispara e não resolve fica repetindo para sempre — e
# como encontrar algo reseta a exploração, o swipe nunca entra
# para salvar.
#
# O caso concreto hoje: up_food em NORMAL faz "upgrade_food",
# que não está em DISMISS_ACTIONS e por isso não escala para
# rolagem. Se o painel de comida não fechar, este aviso é o
# único sinal — e cada repetição gasta moeda.
#
# Só loga: agir sozinho aqui seria adivinhar.
REPEATED_ACTION_WARNING = 8

# Tempo máximo em cada estado antes de desistir e voltar
# para NORMAL.
#
# Sem isto o bot trava para sempre se aparecer um modal
# sem template: RENOVATE só sai achando a moeda, UPGRADE
# só sai achando o botão de fechar.
STATE_TIMEOUTS = {
    "RENOVATE": 12.0,
    "UPGRADE": 15.0,
    "NEW_POINT": 10.0,
    "FOOD": 5.0,
}
