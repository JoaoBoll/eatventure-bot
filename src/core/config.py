"""Configuração central do EatVenture AI — todo valor ajustável mora aqui, nenhum outro módulo deve ter número mágico."""

import os
import platform
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

_IS_WINDOWS = platform.system() == "Windows"
_EXE_SUFFIX = ".exe" if _IS_WINDOWS else ""

# None = decide em runtime (único conectado usa ele; vários, PERGUNTA).
# Fixe quando o celular fica ligado por USB e wifi ao mesmo tempo: o
# `adb devices` lista o mesmo aparelho duas vezes e a pergunta reaparece
# toda execução. Prefira o serial do USB — a conexão wifi cai sozinha.
#
#   DEVICE_SERIAL = "e2615705"
DEVICE_SERIAL = None

# Prefer system scrcpy/adb in PATH if installed; else prefer project tools
_scrcpy_dir = PROJECT_ROOT / "tools" / "scrcpy"

# O binário do server pode vir como "scrcpy-server-v3.x.jar" (release do
# GitHub) ou simplesmente "scrcpy-server" (build/zip).
_SERVER_NAMES = ("scrcpy-server*.jar", "scrcpy-server")


def _find_server(base: Path):
    for pattern in _SERVER_NAMES:
        try:
            hit = next((p for p in base.rglob(pattern) if p.is_file()), None)
        except Exception:
            hit = None
        if hit is not None:
            return hit
    return None


# Prefer scrcpy from PATH when available
_scrcpy_path = shutil.which("scrcpy")
if _scrcpy_path:
    SCRCPY_PATH = _scrcpy_path
    # Primeiro, tenta achar o server ao lado do scrcpy.exe (instalacao do sistema)
    try:
        _jar = _find_server(Path(_scrcpy_path).parent)
    except Exception:
        _jar = None
    # Se nao encontrar, tenta o diretorio de ferramentas do projeto
    if _jar is None and _scrcpy_dir.exists():
        _jar = _find_server(_scrcpy_dir)
    SCRCPY_SERVER_PATH = str(_jar) if _jar is not None else r""
else:
    SCRCPY_PATH = r""
    SCRCPY_SERVER_PATH = r""
    if _scrcpy_dir.exists():
        try:
            _exe = next(_scrcpy_dir.rglob(f"scrcpy{_EXE_SUFFIX}"), None)
            _jar = _find_server(_scrcpy_dir)
            SCRCPY_PATH = str(_exe) if _exe is not None else r""
            SCRCPY_SERVER_PATH = str(_jar) if _jar is not None else r""
        except Exception:
            pass

SCRCPY_SERVER_VERSION = "4.1"


# ADB: prefer system adb from PATH
_adb_system = shutil.which("adb")
if _adb_system:
    ADB_PATH = _adb_system
else:
    # If scrcpy is available (either system or project), prefer adb next to it
    _adb_from_scrcpy = None
    try:
        if SCRCPY_PATH:
            scrcpy_p = Path(SCRCPY_PATH)
            # look for adb in the same directory
            candidate = scrcpy_p.parent / f'adb{_EXE_SUFFIX}'
            if candidate.exists():
                _adb_from_scrcpy = str(candidate)
            else:
                # also try sibling platform-tools or parent/platform-tools
                sibling = scrcpy_p.parent / 'platform-tools' / f'adb{_EXE_SUFFIX}'
                if sibling.exists():
                    _adb_from_scrcpy = str(sibling)
    except Exception:
        _adb_from_scrcpy = None

    if not _adb_from_scrcpy:
        # project tools/scrcpy may contain adb somewhere under it
        _scrcpy_dir = PROJECT_ROOT / 'tools' / 'scrcpy'
        if _scrcpy_dir.exists():
            _adb_candidate = next(_scrcpy_dir.rglob(f'adb{_EXE_SUFFIX}'), None)
            if _adb_candidate:
                _adb_from_scrcpy = str(_adb_candidate)

    if _adb_from_scrcpy:
        ADB_PATH = _adb_from_scrcpy
    else:
        # No adb found in PATH or beside scrcpy: use system 'adb' fallback
        ADB_PATH = "adb"

# NÃO use a faixa 27183-27199 (default do scrcpy.exe): com o espelho
# ligado os dois brigam pela porta — sintoma intermitente, "Stream
# encerrado" para quem perde a corrida.
SCRCPY_PORT = 27283

# Diferente do path do scrcpy.exe de propósito: os dois faziam adb push
# do mesmo arquivo ao mesmo tempo, e nosso stop() apagava ele, corrompendo
# o espelho de forma aleatória.
SCRCPY_DEVICE_JAR = "/data/local/tmp/eatventure-server.jar"

# O servidor leva ~1.4s (medido) para começar a servir; o adb aceita a
# conexão TCP antes de o socket abstrato estar pronto, então "conectou"
# não significa nada.
CAPTURE_CONNECT_TIMEOUT = 12.0

# Tempo para o primeiro frame DECODIFICADO depois de conectar.
CAPTURE_START_TIMEOUT = 6.0

# Teto de fps entregues ao resto do programa. 0 = sem teto.
#
# O device manda 60, mas o detector roda ~30 passadas/s e ainda espera
# ACTION_SETTLE (400ms) por ação — frames entre uma passada e outra só
# são convertidos e descartados. O corte é ANTES da conversão de cor
# (YUV->BGR, a parte cara); a decodificação H.264 não pode ser pulada
# (inter-quadro: frame descartado ainda é referência). Custo: até 33ms
# de idade extra no frame mais recente, irrelevante contra os 400ms do
# settle.
CAPTURE_MAX_FPS = 30


# Cada janela liga e desliga por conta própria; as quatro combinações
# são válidas, inclusive headless.

# Única que recebe teclado (ESC só funciona com ela ligada).
SHOW_AI_VISION = True

# Espelho do scrcpy.exe — é APENAS espelho, a captura do bot não passa
# por ele (lê o socket direto). Útil para intervir na mão, já que a
# janela da IA não aceita toque.
SHOW_SCRCPY = False

# Texto "categoria F=.. C=.." em cada caixa. Desligue com muita detecção
# junta: o texto empilhado atrapalha mais do que ajuda.
SHOW_DETECTION_LABELS = True

# Idade do frame que gerou as detecções — distingue "detecção errada"
# de "detecção atrasada".
SHOW_DETECTION_LAG = True

# TEMPO CORRIDO entre reformas: mede se o bot está PROGREDINDO, o que o
# FPS não diz (pode estar a 30fps clicando em nada há vinte minutos).
SHOW_CYCLE_TIME = True

# Bloco fixo reescrito no lugar: responde "o que está acontecendo AGORA",
# que o log não dá bem com o bot agindo ~1x/s.
#
# LIGADO, o log do console cai para WARNING (painel e log disputam o
# terminal); aviso e erro continuam aparecendo. DESLIGADO, volta ao log
# linha-por-linha — útil para investigar algo.
STATUS_PANEL = True

# Segundos entre redesenhos. Meio segundo já parece vivo.
STATUS_PANEL_INTERVAL = 0.5


# `build` e `plane` são as duas portas para RENOVATE (fim de um ciclo).
CYCLE_CATEGORIES = {"build", "plane"}

# Múltiplo do ciclo ANTERIOR (não um número fixo, já que cada
# restaurante leva o que leva) a partir do qual o tempo fica vermelho —
# sinal de bot travado.
CYCLE_STALL_FACTOR = 3.0

# Sessão que morre por bateria descarregada não deixa rastro no log,
# só para de agir.
SHOW_BATTERY = True

# `dumpsys battery` custa ~56ms, >3x uma passada do detector; roda em
# thread própria, nunca no loop de render. 30s é folgado de propósito.
BATTERY_POLL_INTERVAL = 30.0

# Abaixo disto o número fica vermelho no HUD.
BATTERY_WARNING_LEVEL = 20

# captura (fps do device) vs detector (passadas/s) — números muito
# diferentes; quem manda na reação do bot é o segundo.
SHOW_FPS = True

# Tamanho da janela da IA
WINDOW_WIDTH = 500
WINDOW_HEIGHT = 900

AI_WINDOW_NAME = "EatVenture - AI"

AI_WINDOW_POSITION = (600, 50)

# Úteis quando o espelho é a sua única janela:
#   "--stay-awake"        device não dorme enquanto plugado
#   "--window-borderless"
#   "--always-on-top"
SCRCPY_EXTRA_ARGS = [
    "--capture-orientation", "90",  # Força o Scrcpy a iniciar deitado (Modo Paisagem)
    "--no-audio"                    # Desativa o áudio para evitar o erro do Demuxer
]


# Janela acompanha a ALTURA da tela mantendo a proporção — fixa em
# 500x900 dava escala 0.375 num device 1080x2400 (1px = 2.7px reais),
# tornando o recorte de template impreciso. Escala não passa de 1.0:
# ampliar só borra o recorte.

# Fração da ALTURA da tela que a janela ocupa.
SELECTOR_HEIGHT_FRACTION = 0.80

# Fração da LARGURA — proteção para monitor deitado, não limita tela
# alta e estreita.
SELECTOR_WIDTH_FRACTION = 0.95

# Sobrepõe a detecção de tela. None = detecta automaticamente.
SELECTOR_MAX_WIDTH = None
SELECTOR_MAX_HEIGHT = None

# Usado quando a detecção de tela não funciona.
SELECTOR_FALLBACK_WIDTH = 1600
SELECTOR_FALLBACK_HEIGHT = 1000


# Grava, por ação, o frame que motivou a decisão e os rótulos do
# template matcher — as CAIXAS (não só o ponto do clique, ambíguo com
# vários alvos) e o RESULTADO da ação (senão o treino herda todo erro
# do professor). Detalhes e DDL: docs/dataset.md

# Liga a gravação do dataset — TUDO ou nada (imagem, samples.jsonl e
# índice no banco se DATASET_DB_ENABLED). False: DatasetRecorder nem é
# construído, sem custo.
DATASET_SAVE = False

# Pasta de destino, também lida pelo treino.
#
#   <DATASET_DIR>/samples.jsonl        índice, fonte de verdade
#   <DATASET_DIR>/images/AAAA-MM-DD/   as imagens
DATASET_DIR = PROJECT_ROOT / "dataset"

# "png" ou "jpg". MEDIDO: PNG 2.10MB, JPG q92 0.49MB (4.3x menor) por
# frame — 7.6GB/h contra 1.8GB/h a ~1 ação/s. JPG escolhido: artefato de
# compressão é irrelevante para botão de UI (borda forte, cor saturada).
DATASET_IMAGE_FORMAT = "jpg"
DATASET_JPEG_QUALITY = 92

# TODAS as ações que o bot sabe fazer — cada tipo precisa de exemplo
# próprio para ser aprendido. `upgrade_food` cobre o processo de
# evolução inteiro (NORMAL e FOOD). `swipe_up`/`swipe_down` não estão
# em ACTION_TABLE (chamam o ActionManager por outro caminho); a direção
# vai no NOME porque para o treino subir e descer são rótulos diferentes.
DATASET_ACTIONS = {
    # Um toque no centro da detecção.
    "click",              # build
    "close",
    "food",
    "new_point",
    "new_point_click",
    "open_box",
    "open_renovate",
    "open_store_click",
    "plane",
    "renovate_click",
    "upgrade",

    # Vários toques.
    "upgrade_item",

    # Toque longo.
    "upgrade_food",

    # Ponto fixo, ignora a detecção.
    "gray_max",
    "gray_coin",
    "dismiss",

    # Sem alvo pontual.
    "scroll_bottom",
    "swipe_up",
    "swipe_down",
}

# Segundos entre amostras NEGATIVAS (tela sem detecção) — sem elas o
# detector aprende que sempre existe um alvo. 0 desliga.
#
# É INTERVALO e não probabilidade: por passada (loop roda ~30x/s), 5%
# gravou 30 negativas em 25s (11GB/h em PNG). Intervalo independe da
# velocidade do loop.
DATASET_NEGATIVE_INTERVAL = 20.0

# Lado da miniatura para comparar telas (NxN pixels): grava o `phash`
# como metadado e decide se a tela MUDOU após ação sem alvo pontual.
DATASET_HASH_SIZE = 8

# Quantas miniaturas recentes manter em memória.
DATASET_DEDUPE_MEMORY = 200

# Máximo de amostras `unchanged` seguidas da MESMA ação — substitui a
# deduplicação por conteúdo, que NÃO FUNCIONA aqui (diferença média de
# miniatura 8x8: travado 1.9-5.8, telas reais distintas 2.5-59.8,
# faixas sobrepostas pela animação do jogo). O discriminador limpo é o
# RESULTADO já gravado (travado = sempre unchanged), então o corte é
# por sequência: mata o caso travado (35 amostras -> 3) sem perder
# amostra produtiva. Zera quando a ação muda ou dá `changed`.
DATASET_MAX_UNCHANGED_STREAK = 3

# Diferença média de miniatura a partir da qual a tela é MUDADA, para
# ações sem alvo pontual (swipe, scroll, dismiss) — mudança GRANDE é
# fácil de detectar (8.9-59.8 nos dados), ao contrário de "nenhuma" vs
# "pequena".
DATASET_SCREEN_CHANGE = 8.0

# Tamanho da fila para a thread de gravação — codificar PNG custa mais
# que uma passada do detector, então nunca roda no caminho crítico.
# Fila cheia DESCARTA a amostra: perder amostra é aceitável, atrasar o
# bot não é.
DATASET_QUEUE_SIZE = 8

# Segundos de espera por um frame que mostre o efeito da ação antes de
# gravar o resultado como "unknown".
DATASET_OUTCOME_TIMEOUT = 3.0

# Teto de amostras por execução. 0 = sem limite.
DATASET_MAX_SAMPLES = 0

# Teto de disco para a pasta do dataset, em MB. 0 = sem limite. MEDIDO:
# 7.6GB/h em PNG contra 1.8GB/h em JPG a ~1 ação/s. Sessões rodam horas
# sem supervisão, então o teto evita descobrir o problema com o disco
# cheio — ao estourar, a gravação para e avisa uma vez.
DATASET_MAX_DISK_MB = 20_000


# O banco é ÍNDICE, não armazenamento (imagens ficam em arquivo — BLOB
# faria o treino ler gigabytes por época e perderia a copiabilidade via
# rsync). Opcional e desligado por padrão; ligue só com PostgreSQL
# funcionando, rodando antes o DDL de docs/dataset.md.
DATASET_DB_ENABLED = os.environ.get(
    "EATVENTURE_DB_ENABLED",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

# ATENÇÃO: este arquivo está no git — senha aqui vai para o histórico
# do repositório e não some depois. Se for público, use variável de
# ambiente e deixe a linha abaixo vazia:
#
#   PowerShell:  $env:EATVENTURE_DB_DSN = "host=... password=..."
#   bash:        export EATVENTURE_DB_DSN="host=... password=..."
#
# Forma KEYWORD/VALUE do libpq (não URL) de propósito: a senha tem "@",
# que numa URL precisaria virar %40 sem escape.
DATASET_DB_DSN = os.environ.get("EATVENTURE_DB_DSN", "")


DATASET_DB_SCHEMA = "public"

# Amostras por ida ao banco. Uma ida por amostra colocaria
# latência de rede na thread que também codifica PNG.
DATASET_DB_BATCH = 20


# DEBUG / INFO / WARNING
LOG_LEVEL = "INFO"


# Templates e coordenadas fixas foram recortados nesta resolução; outra
# resolução de device/stream é convertida em runtime a partir daqui.
REFERENCE_WIDTH = 1080
REFERENCE_HEIGHT = 2400


# Templates são reescalados para a resolução do frame (uma vez por
# resolução vista). O problema não é resolução, é PROPORÇÃO: escalar
# pelo menor dos dois fatores só está certo quando a proporção é igual
# à referência — num 1080x1920 contra referência 1080x2400,
# min(1080/1080, 1920/2400)=0.80 encolhia todo template 20% mesmo com a
# largura idêntica, e o sintoma era "não detecta em outro aparelho".
#
# TEMPLATE_SCALE_BASIS escolhe o fator:
#
#   "short_side" ... razão entre os LADOS CURTOS (padrão)
#   "long_side" .... razão entre os lados longos
#   "width" / "height" ... só uma dimensão
#   "min" .......... o menor dos dois (comportamento antigo)
#
# "short_side" é o padrão porque UI de jogo mobile ancora na dimensão
# estreita, e é o único que dá 1.0 no caso acima. Comparação sempre na
# mesma orientação (o jogo roda deitado: frame 2400x1080, referência
# escrita 1080x2400).
TEMPLATE_SCALE_BASIS = "short_side"

# Escalas EXTRA em volta da estimativa: densidade de tela e layout mudam
# o tamanho do ícone alguns %, e template matching é intolerante a isso
# (8% de erro já derruba a confiança abaixo de 0.95). NMS_IOU colapsa os
# acertos repetidos das escalas vizinhas. CUSTO multiplica os templates
# procurados, por isso não se aplica na resolução de referência. Com o
# aparelho já conhecido, reduza para (1.0,).
TEMPLATE_SCALE_STEPS = (0.92, 1.0, 1.08)


# Similaridade mínima de formato (padrão).
SHAPE_THRESHOLD = 0.80

# Similaridade mínima de cor. ATENÇÃO: métrica reescrita (matiz
# circular + peso por saturação) — não comparável à antiga.
COLOR_THRESHOLD = 0.80

# Threshold de formato por categoria; ausente usa SHAPE_THRESHOLD.
# ÚNICA fonte de verdade — a StateMachine não refiltra por confiança.
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


# Diz no log, por categoria, qual foi o MELHOR match quando nenhum
# passou — distingue "não detectou" de "detectou e o threshold
# cortou" (problemas opostos: template novo vs baixar o número).
# Ligue ao levar o bot para tela nova, desligue depois.
DETECTOR_DEBUG_MISSES = False
DETECTOR_DEBUG_INTERVAL = 3.0


# Estágio 1 (grosso): procura numa cópia reduzida do frame.
# Estágio 2 (fino): reconfirma cada candidato em resolução cheia numa
# janela pequena. Resultado final sempre em resolução cheia, então os
# thresholds acima continuam valendo — o grosso só descarta regiões
# sem chance.
#
# MEDIDO nas duas telas de tests/images, com 43 templates: busca direta
# 1697ms, escala global 0.50 -> 562ms, 0.40 -> 323ms.

# TETO da escala do estágio grosso, não a escala usada — depende do
# TAMANHO do template (abaixo de 12px o grosso é abandonado). Escala
# global fica travada pelo MENOR template (up_upgrade, 32px -> 0.40) e
# os grandes pagam a conta.
#
# MEDIDO nos 4 fixtures, 106 templates: escala global 0.40 = 3409ms,
# escala por template = 1046ms (3.3x), com detecções IDÊNTICAS — o
# custo extra não comprava precisão. Escala derivada exatamente de
# 12/menor_lado, sem tabela por categoria. 75 dos 106 templates são
# food (mediana 80x93) e aguentam 0.15-0.20.
COARSE_SCALE = 0.40

# Piso da escala derivada — não é sobre precisão (a 0.10 as detecções
# são idênticas), é sobre o frame reduzido ficar pequeno demais e gerar
# candidato em excesso (cada um custa reconfirmação em resolução cheia).
COARSE_SCALE_MIN = 0.10

# Grade da escala derivada. Arredondar PARA CIMA dá folga ao piso e
# limita os tamanhos de frame reduzido (cada um custa um resize; 7
# escalas = 7.9ms medidos contra ~700ms de busca).
COARSE_SCALE_STEP = 0.05

# Folga do threshold no estágio grosso — a redução do frame degrada a
# confiança, então precisa ser mais permissivo que o fino.
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


# Restringe a busca de uma categoria a um pedaço da tela — maior ganho
# disponível em velocidade e precisão. Formato: (x1,y1,x2,y2) em FRAÇÃO
# da tela (independe de resolução). Categoria ausente = busca inteira.
#
# Deixado vazio de propósito: uma ROI errada esconde detecção boa, e
# isso precisa ser conferido na tela real. Exemplo:
#
#     "close":    (0.60, 0.00, 1.00, 0.20),
#     "gray_max": (0.00, 0.75, 1.00, 1.00),
CATEGORY_ROIS = {}


# Cliques repetidos do upgrade de item.
UPGRADE_ITEM_CLICKS = 5

# Long press do upgrade de comida (segundos).
UPGRADE_FOOD_PRESS = 4.0

# Ponto neutro para dispensar painel que abriu sem querer, em
# coordenadas da resolução de referência.
DISMISS_POINT = (10, 2200)

# Ponto da ação `gray_coin` — mesma ideia do `gray_max` (ponto fixo,
# ignora a detecção), separada porque o ponto é diferente.
GRAY_COIN_POINT = (1070, 250)

# Ponto fixo por ação (para as que ignoram a detecção); ação ausente
# usa DISMISS_POINT.
ACTION_POINTS = {
    "gray_coin": GRAY_COIN_POINT,
}

# ESCALONAMENTO DE FECHAMENTO: o "ponto seguro" pode abrir um painel
# que mostra "max", e a regra do gray_max toca o mesmo ponto de novo —
# ciclo sem fim. Não existe coordenada boa em qualquer posição de
# rolagem, mas com a tela descida até o fim o canto de baixo fica
# vazio. Por isso a escada: tenta o ponto N vezes, rola até o fim,
# tenta de novo.
#
# NÃO use o BACK do Android: neste jogo ele SAI DO JOGO (testado) —
# por isso android.back() existe mas não está em ACTION_TABLE. Swipe
# sozinho não fecha painel, só serve para chegar na posição certa.

# Ações que fecham painel tocando em ponto fixo, e por isso escalam.
DISMISS_ACTIONS = {"dismiss", "gray_max", "gray_coin"}

# Tentativas no ponto antes de rolar a tela até o fim.
DISMISS_ATTEMPTS_BEFORE_SCROLL = 5

# "up" = dedo para cima = a VISTA DESCE (convenção do ActionManager).
SCROLL_BOTTOM_DIRECTION = "up"

# Swipes na sequência de escape — precisa chegar ao fim de qualquer
# restaurante.
SCROLL_BOTTOM_SWIPES = 6

# Duração do toque MANTIDO no ponto neutro ("dismiss"): tap seco do adb
# às vezes não registra. 0.4s fica acima de um tap falho e abaixo dos
# ~500ms de long press do Android.
#
# NENHUMA regra usa "dismiss" hoje (up_food em NORMAL faz upgrade_food);
# a ação segue implementada e testada, pronta para voltar às regras.
DISMISS_HOLD_DURATION = 0.4

# Swipe de exploração, em coordenadas de referência.
SWIPE_X = 540
SWIPE_Y = 1200
SWIPE_DISTANCE = 700
SWIPE_DURATION_MS = 500


# Pausa entre passadas do detector — NÃO é o período de análise (uma
# passada custa muito mais); só evita que o worker monopolize a CPU
# quando o detector estiver rápido.
VISION_INTERVAL = 0.01

# Restringe a busca às categorias que o estado atual usa. Desligue ao
# recortar templates/ajustar thresholds: com o filtro ligado o overlay
# só mostra as categorias do estado, fácil de confundir com "o detector
# parou de achar".
VISION_FILTER_BY_STATE = True

# Para de procurar na primeira CATEGORIA encontrada: a StateMachine age
# na PRIMEIRA regra que casar, então procurar as de baixo depois de um
# acerto nunca vira ação. MEDIDO em estado NORMAL: 264ms procurando tudo
# (182 templates) vs ~20ms parando na prioridade (`food` sozinho é 124
# templates e 174 dos 312ms, última prioridade). Parada é por CATEGORIA,
# nunca dentro dela (duas comidas na tela são duas detecções).
#
# O QUE SE PERDE: overlay só mostra até a categoria que venceu —
# desligue ao recortar template/ajustar threshold. Sem
# VISION_FILTER_BY_STATE isto não tem efeito.
VISION_PRIORITY_STOP = True

# Idade máxima de uma detecção para a StateMachine agir sobre ela —
# detecção velha é clique em coordenada que já mudou. None desliga.
MAX_DETECTION_AGE = 2.0


# Intervalo mínimo entre ações.
ACTION_COOLDOWN = 0.5

# Tempo que o JOGO leva para reagir a um toque — resolve o duplo toque
# (fechar o "MAX" e tocar de novo no mesmo ponto REABRE o painel).
# O cooldown (0.5s) libera antes do detector produzir um frame posterior
# à ação (atraso ~0.535s), e mesmo um frame posterior ainda mostra o
# painel enquanto a animação de fechar não terminou.
#
# Por isso a condição é CAUSAL, não temporal: só age sobre frame
# CAPTURADO pelo menos ACTION_SETTLE depois da última ação — aumentar o
# cooldown não resolveria, um detector mais lento estouraria a margem
# de novo. 0.4s cobre animação de painel (tipicamente 0.15-0.3s) com
# folga.
ACTION_SETTLE = 0.4

# Espera depois de um SWIPE, antes de agir de novo — mesmo papel do
# ACTION_SETTLE, mas swipe move a VISTA INTEIRA e o jogo desliza por
# inércia depois de o dedo sair. Sem isto: o bot detecta um alvo num
# frame capturado ainda em movimento e toca onde o alvo ESTAVA.
#
# Condição CAUSAL como o ACTION_SETTLE: age sobre frame CAPTURADO pelo
# menos este tanto depois do swipe TERMINAR. Contado do FIM do swipe
# (não da submissão), já que SWIPE_DURATION_MS (500ms) + overhead do adb
# consumiriam a espera antes de ela ter efeito.
SWIPE_WAITING_TIME = 0.5

# Ações que movem a VISTA INTEIRA e por isso usam a espera do swipe em
# vez da de um toque. A exploração (swipe_up/down) não precisa estar
# aqui — já é tratada como swipe por outro caminho. `scroll_bottom` é
# SEIS swipes seguidos, mexendo a vista mais que qualquer swipe solto.
VIEW_MOVING_ACTIONS = {"scroll_bottom"}

# True: só explora depois de encontrar uma tela sem ação.
# False: tenta explorar a cada EXPLORATION_DELAY, mesmo ao achar ação.
SWIPE_WAIT_FOR_NO_ACTION = True

# Tempo sem detectar nada antes de fazer swipe.
EXPLORATION_DELAY = 5.0

# Espera antes de voltar a explorar depois de ENCONTRAR algo: rolar na
# hora tiraria de vista o alvo achado, mas parar de explorar deixaria o
# que está fora da tela nunca virar detecção. Achar não CANCELA a
# exploração, só ADIA (espera 15s e volta ao ritmo de 5s).
#
# O CICLO NÃO ZERA (5 swipes para um lado, 5 para o outro): achar algo
# no meio não reinicia a contagem, senão o bot varreria a sessão
# inteira no mesmo pedaço de tela.
EXPLORATION_DELAY_AFTER_ACTION = 15.0

# Swipes consecutivos antes de inverter a direção.
MAX_SWIPES = 5

# Direção do primeiro swipe: depende de onde o jogo costuma deixar
# conteúdo fora da tela — ajuste de jogo, não de código.
SWIPE_START_DIRECTION = "down"

# Espera pelo próximo "up food" depois do long press.
UP_FOOD_WAIT = 2.0

# Quantas vezes a MESMA ação pode repetir antes de virar aviso no log.
# NORMAL não tem timeout, então uma regra que dispara e não resolve
# repete para sempre (achar algo reseta a exploração, o swipe nunca
# entra para salvar). Caso concreto: up_food em NORMAL faz
# "upgrade_food", que não escala para rolagem se o painel não fechar —
# este aviso é o único sinal, e cada repetição gasta moeda. Só loga:
# agir sozinho aqui seria adivinhar.
REPEATED_ACTION_WARNING = 8

# Tempo máximo em cada estado antes de desistir e voltar para NORMAL —
# sem isto o bot trava para sempre num modal sem template.
#
# Espera ao ENTRAR num estado, antes de agir nele. Caso concreto: a
# tela de upgrade abre com ANIMAÇÃO; o "X" já casa com o template antes
# dos botões de upgrade (regra 1 não encontra nada, regra 2 encontra, e
# o bot fecha o painel que ele mesmo abriu). ACTION_SETTLE não resolve
# porque é contado do toque que abriu e cobre animação de FECHAR
# (0.15-0.3s), não de abrir uma tela inteira.
#
# Condição CAUSAL como as outras esperas do projeto: age sobre frame
# CAPTURADO pelo menos este tanto depois de entrar no estado (o
# VisionWorker usa isto para não gastar passada em frame de animação).
# Estado ausente = sem espera. Errar para cima come o tempo do
# STATE_TIMEOUTS.
STATE_ENTRY_SETTLE = {
    "UPGRADE": 1,
    "GRAY_MAX": 1,
}

STATE_TIMEOUTS = {
    "RENOVATE": 12.0,
    "UPGRADE": 15.0,
    "NEW_POINT": 10.0,
    "FOOD": 5.0,
}
