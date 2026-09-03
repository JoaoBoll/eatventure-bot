"""
Configuração central do EatVenture AI.

Todo valor ajustável do projeto mora aqui.
Nenhum outro módulo deve ter número mágico.
"""

import os
from pathlib import Path
import shutil


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

# Prefer system scrcpy/adb in PATH if installed; else prefer project tools
_scrcpy_dir = PROJECT_ROOT / "tools" / "scrcpy"

# O binario do server pode vir como "scrcpy-server-v3.x.jar" (release do
# GitHub) ou simplesmente "scrcpy-server" (build/zip do Windows).
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
            _exe = next(_scrcpy_dir.rglob("scrcpy.exe"), None)
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
            # look for adb.exe in the same directory
            candidate = scrcpy_p.parent / 'adb.exe'
            if candidate.exists():
                _adb_from_scrcpy = str(candidate)
            else:
                # also try sibling platform-tools or parent/platform-tools
                sibling = scrcpy_p.parent / 'platform-tools' / 'adb.exe'
                if sibling.exists():
                    _adb_from_scrcpy = str(sibling)
    except Exception:
        _adb_from_scrcpy = None

    if not _adb_from_scrcpy:
        # project tools/scrcpy may contain adb.exe somewhere under it
        _scrcpy_dir = PROJECT_ROOT / 'tools' / 'scrcpy'
        if _scrcpy_dir.exists():
            _adb_candidate = next(_scrcpy_dir.rglob('adb.exe'), None)
            if _adb_candidate:
                _adb_from_scrcpy = str(_adb_candidate)

    if _adb_from_scrcpy:
        ADB_PATH = _adb_from_scrcpy
    else:
        # No adb found in PATH or beside scrcpy: use system 'adb' fallback
        ADB_PATH = "adb"

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

# Teto de frames por segundo entregues ao resto do programa.
#
# 0 = sem teto (entrega tudo o que o device mandar).
#
# O device manda 60. O bot não usa 60: o detector faz ~30
# passadas por segundo no caminho rápido, e a decisão ainda
# espera ACTION_SETTLE (400 ms) depois de cada ação. Os frames
# entre um e outro são convertidos, copiados e descartados.
#
# O corte é feito ANTES da conversão de cor, que é a parte
# caríssima: cada frame de 1080x2400 custa um YUV->BGR de 7.8 MB
# (a decodificação em si não pode ser pulada — H.264 é
# inter-quadro, um frame descartado ainda é referência para os
# seguintes).
#
# Em 30 fps isso corta metade dessas conversões, e metade das
# acordadas do loop principal (que a cada frame novo redesenha a
# janela da IA). O que se paga em troca é até 33 ms de idade
# extra no frame mais recente — contra os 400 ms do settle, não
# muda a reação do bot.
CAPTURE_MAX_FPS = 30


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

# ---------------------------------------------------------
# PAINEL DE STATUS
# ---------------------------------------------------------
#
# Um bloco fixo, reescrito no mesmo lugar: device, quantas vezes
# voou, quantas reformou, o que está fazendo agora.
#
# É a resposta para "o que está acontecendo AGORA", que o log
# não dá bem: com o bot agindo ~1x/s, a linha que interessa já
# subiu na tela.
#
# LIGADO, o log do console cai para WARNING — senão as duas
# coisas disputam o terminal e o painel se redesenha sobre a
# linha errada. Aviso e erro continuam aparecendo; o INFO de
# cada ação sai, porque é exatamente o que o painel substitui.
#
# DESLIGADO, tudo volta ao log linha-por-linha de antes. Use
# assim quando estiver investigando algo.
STATUS_PANEL = True

# Segundos entre redesenhos. Meio segundo já parece vivo;
# redesenhar a cada quadro só gasta escrita no terminal.
STATUS_PANEL_INTERVAL = 0.5


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
SCRCPY_EXTRA_ARGS = [
    "--capture-orientation", "90",  # Força o Scrcpy a iniciar deitado (Modo Paisagem)
    "--no-audio"                    # Desativa o áudio para evitar o erro do Demuxer
]


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
# DATASET DE TREINO
# =========================================================
#
# Grava, para cada ação, o frame que motivou a decisão e os
# rótulos que o template matcher produziu.
#
# São gravadas as CAIXAS, não só o ponto do clique: um ponto por
# imagem é ambíguo quando há vários alvos e não ensina quantos
# existem. Com as caixas a tarefa é detecção de objetos — a
# mesma que o matcher faz, com muito mais rótulo por imagem. O
# ponto do clique se deriva da caixa; o contrário não.
#
# Também é gravado o RESULTADO da ação (o alvo saiu da tela?).
# Sem ele o treino herda todo erro do professor e o modelo não
# passa do template matcher.
#
# Detalhes e o DDL do banco: docs/dataset.md

# Liga a gravação do dataset — TUDO ou nada.
#
# True:  grava a imagem, a linha no samples.jsonl e o índice no
#        banco (se DATASET_DB_ENABLED).
# False: não grava nada. O DatasetRecorder nem é construído,
#        então não há thread, não há fila e não há custo — o bot
#        só joga.
#
# É um parâmetro só de propósito. Existiu por um tempo um
# DATASET_SAVE_IMAGES separado, para gravar índice sem imagem;
# saiu porque duas chaves para a mesma decisão é o tipo de coisa
# que fica dessincronizada e ninguém percebe. E sem imagem o
# treino de visão não roda de jeito nenhum (`features.py`
# recorta pixel), então "só o índice" não era um modo útil o
# bastante para justificar a segunda chave.
DATASET_SAVE = False

# Pasta de destino. É também a pasta que o treino LÊ.
#
#   <DATASET_DIR>/samples.jsonl        índice, fonte de verdade
#   <DATASET_DIR>/images/AAAA-MM-DD/   as imagens
DATASET_DIR = PROJECT_ROOT / "dataset"

# "png" ou "jpg".
#
# MEDIDO num frame decodificado de H.264 (o que o recorder de
# fato grava): PNG 2.10 MB, JPG q92 0.49 MB — 4.3x menor. Com o
# bot agindo ~1x/s isso é 7.6 GB/hora contra 1.8 GB/hora.
#
# JPG escolhido: a sessão é longa, e artefato de compressão é
# irrelevante para reconhecer botão de UI, que tem borda forte e
# cor saturada. Trocar de volta é esta linha.
DATASET_IMAGE_FORMAT = "jpg"
DATASET_JPEG_QUALITY = 92

# Ações que entram no dataset.
#
# TODAS as que o bot sabe fazer, inclusive `click` (o build) e
# `plane`, que antes ficavam de fora. Cada tipo precisa de
# exemplo próprio para ser aprendido.
#
# Note que `upgrade_food` cobre o processo de evolução da comida
# inteiro — é a mesma ação em NORMAL e em FOOD.
#
# `swipe_up`/`swipe_down` não estão em ACTION_TABLE: são a
# exploração, que chama o ActionManager por outro caminho. A
# direção entra no NOME em vez de num campo novo, porque para o
# treino subir e descer são rótulos diferentes.
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

# Segundos entre amostras NEGATIVAS (tela sem detecção).
#
# Um detector treinado só em telas com alvo aprende que sempre
# existe um alvo. 0 desliga.
#
# É INTERVALO e não probabilidade porque a probabilidade se
# aplicaria por passada do loop, que roda ~30x/s: medido, 5% por
# passada gravou 30 negativas em 25 s — 11 GB/hora em PNG. O
# intervalo é previsível, não importa a velocidade do loop.
DATASET_NEGATIVE_INTERVAL = 20.0

# Lado da miniatura usada para comparar telas (NxN pixels).
#
# Serve para duas coisas: gravar o `phash` como metadado (para
# deduplicar offline depois, com métrica melhor) e decidir se a
# tela MUDOU depois de uma ação sem alvo pontual.
DATASET_HASH_SIZE = 8

# Quantas miniaturas recentes manter em memória.
DATASET_DEDUPE_MEMORY = 200

# Máximo de amostras `unchanged` seguidas da MESMA ação.
#
# Substitui a deduplicação por conteúdo, que NÃO FUNCIONA aqui.
# Medido nos dados reais, com diferença média de miniatura 8x8:
#
#   bot travado na mesma tela ..... 1.9 a 5.8
#   jogo real, telas distintas .... 2.5 a 59.8
#
# As faixas se sobrepõem, porque a tela do jogo anima sozinha
# (contador de dinheiro, personagens). Qualquer limite que pegue
# o caso travado joga fora amostra distinta de verdade.
#
# O discriminador limpo é o RESULTADO, que já é gravado:
#
#   sessão travada ..... 35/35 unchanged, sempre a mesma ação
#   sessão produtiva ... open_box 5x seguidas, todas changed
#
# Então o corte é por sequência de `unchanged`: mata o caso
# travado (35 amostras viram 3) e não perde nenhuma amostra
# produtiva. Zera quando a ação muda ou quando dá `changed`.
DATASET_MAX_UNCHANGED_STREAK = 3

# Diferença média de miniatura a partir da qual a tela é
# considerada MUDADA, para ações sem alvo pontual (swipe,
# scroll, dismiss).
#
# Aqui a margem é confortável, ao contrário da deduplicação:
# detectar mudança GRANDE é fácil (rolagem move a vista inteira,
# 8.9 a 59.8 nos dados), enquanto distinguir "nenhuma mudança"
# de "mudança pequena" é que não dá.
DATASET_SCREEN_CHANGE = 8.0

# Tamanho da fila para a thread de gravação.
#
# Codificar PNG de 1080x2400 custa mais que uma passada do
# detector, então a gravação NUNCA roda no caminho crítico. Fila
# cheia DESCARTA a amostra: perder amostra é aceitável, atrasar
# o bot não é.
DATASET_QUEUE_SIZE = 8

# Segundos de espera por um frame que mostre o efeito da ação
# antes de gravar o resultado como "unknown".
DATASET_OUTCOME_TIMEOUT = 3.0

# Teto de amostras por execução. 0 = sem limite.
DATASET_MAX_SAMPLES = 0

# Teto de disco para a pasta do dataset, em MB. 0 = sem limite.
#
# MEDIDO: um frame 1080x2400 dá 2.10 MB em PNG e 0.49 MB em JPG
# q92 (4.3x menor). Com o bot agindo ~1x/s:
#
#   PNG ... 7.6 GB/hora
#   JPG ... 1.8 GB/hora
#
# O bot roda por horas sem ninguém olhando, então um teto evita
# descobrir o problema com o disco cheio. Ao estourar, a
# gravação para e avisa uma vez — o bot continua jogando.
DATASET_MAX_DISK_MB = 20_000


# ---------------------------------------------------------
# BANCO (opcional)
# ---------------------------------------------------------
#
# O banco é ÍNDICE, não armazenamento: as imagens ficam em
# arquivo. Guardar pixels como BLOB faria o treino ler gigabytes
# por época através do driver, e o dataset deixaria de ser
# copiável com um rsync.
#
# O treino funciona sem banco nenhum — o samples.jsonl basta.
#
# O banco é opcional e fica desligado por padrão. Só ligue quando
# houver PostgreSQL realmente funcionando e você quiser indexar o
# dataset em banco. Rode antes o DDL de docs/dataset.md.
DATASET_DB_ENABLED = os.environ.get(
    "EATVENTURE_DB_ENABLED",
    "false",
).strip().lower() in {"1", "true", "yes", "on"}

# ATENÇÃO: este arquivo está no git. Senha escrita aqui vai para
# o histórico do repositório, e apagar depois não a remove dos
# commits antigos. Se este repo for para algum lugar público,
# use a variável de ambiente e deixe a linha abaixo vazia:
#
#   PowerShell:  $env:EATVENTURE_DB_DSN = "host=... password=..."
#   bash:        export EATVENTURE_DB_DSN="host=... password=..."
#
# Forma KEYWORD/VALUE do libpq, e não URL, de propósito: a senha
# tem "@", que numa URL precisaria virar %40 — e um %40 esquecido
# faz o parser ler o host errado. Aqui não existe escape.
DATASET_DB_DSN = os.environ.get("EATVENTURE_DB_DSN", "")


DATASET_DB_SCHEMA = "public"

# Amostras por ida ao banco. Uma ida por amostra colocaria
# latência de rede na thread que também codifica PNG.
DATASET_DB_BATCH = 20


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


# ---------------------------------------------------------
# TEMPLATE EM QUALQUER RESOLUÇÃO
# ---------------------------------------------------------
#
# Os templates são reescalados para a resolução do frame (uma
# vez por resolução vista, não por passada). Falta decidir POR
# QUANTO — e é aí que device diferente deixava de detectar.
#
# O problema não é resolução, é PROPORÇÃO. Escalar pelo menor
# dos dois fatores (largura e altura) só está certo quando a
# proporção é a mesma da referência. Num 1080x1920 contra uma
# referência 1080x2400:
#
#   min(1080/1080, 1920/2400) = 0.80
#
# ou seja, todo template encolhia 20% — quando a largura é
# IDÊNTICA e o ícone na tela tem exatamente o mesmo tamanho em
# pixels. Nada passava do threshold, e o sintoma era "não
# detecta em outro aparelho".
#
# TEMPLATE_SCALE_BASIS escolhe de onde sai o fator:
#
#   "short_side" ... razão entre os LADOS CURTOS (padrão)
#   "long_side" .... razão entre os lados longos
#   "width" ........ só a largura
#   "height" ....... só a altura
#   "min" .......... o menor dos dois (o comportamento antigo)
#
# "short_side" é o padrão porque é assim que UI de jogo mobile
# costuma escalar: o layout se ancora na dimensão estreita
# (largura no retrato, altura no deitado) e o excedente da outra
# dimensão vira mais cenário, não interface maior. É também o
# único que dá 1.0 no caso acima, que é a resposta certa.
#
# A comparação é feita SEMPRE na mesma orientação: este jogo
# roda deitado, então o frame chega 2400x1080 enquanto a
# referência está escrita 1080x2400.
TEMPLATE_SCALE_BASIS = "short_side"

# Escalas EXTRA procuradas em volta da estimativa.
#
# Nenhuma regra acerta todo aparelho: densidade de tela, barra
# de status e a própria escolha de layout do jogo mudam o
# tamanho do ícone alguns por cento. E template matching é
# intolerante a isso — 8% de erro de escala já derruba a
# confiança abaixo de 0.95.
#
# Então, em vez de apostar num fator só, o detector procura o
# template em VÁRIOS tamanhos e fica com o que casar melhor. A
# supressão por sobreposição (NMS_IOU) já colapsa os acertos
# repetidos das escalas vizinhas, e a ordenação por confiança
# escolhe a melhor — não é preciso decidir a escala de antemão.
#
# CUSTO: multiplica os templates procurados. Só vale onde é
# necessário, então NÃO é aplicado quando o frame está na
# resolução de referência (aí é uma escala só, custo zero).
#
# Com o aparelho já conhecido e detectando bem, vale reduzir
# para (1.0,): a passada volta ao custo cheio de uma escala.
TEMPLATE_SCALE_STEPS = (0.92, 1.0, 1.08)


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


# Diz no log, por categoria, qual foi o MELHOR match quando
# nenhum passou.
#
# Sem isto, "não detectou" é indistinguível de "detectou e o
# threshold cortou" — e são problemas opostos: um pede template
# novo, o outro pede baixar o número. Ligue quando levar o bot
# para uma tela nova, e desligue depois: é uma linha a cada
# DETECTOR_DEBUG_INTERVAL segundos.
DETECTOR_DEBUG_MISSES = False
DETECTOR_DEBUG_INTERVAL = 3.0


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

# Long press do upgrade de comida (segundos).
UPGRADE_FOOD_PRESS = 4.0

# Ponto neutro para dispensar painel que abriu sem querer.
# Em coordenadas da resolução de referência.
DISMISS_POINT = (10, 2200)

# Ponto tocado pela ação `gray_coin`.
#
# Em coordenadas da resolução de referência, como o
# DISMISS_POINT — convertido para o device em runtime, então vale
# em qualquer aparelho.
#
# `gray_coin` funciona como o `gray_max`: dispensa o painel
# tocando num PONTO FIXO, ignorando onde a detecção apareceu. A
# única diferença é o ponto, e é por isso que ela existe como
# ação separada em vez de reusar o gray_max.
GRAY_COIN_POINT = (1070, 250)

# Ponto fixo por ação, para as que ignoram a detecção.
#
# Ação ausente aqui usa o DISMISS_POINT. É a tabela que permite
# uma ação nova de dispensa não mexer no despacho do
# ActionManager — só numa linha aqui.
ACTION_POINTS = {
    "gray_coin": GRAY_COIN_POINT,
}

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
#
# `gray_coin` entra junto com o `gray_max`: as duas dispensam
# painel tocando em ponto fixo, e o ponto fixo é justamente o que
# pode abrir outra coisa em vez de fechar.
DISMISS_ACTIONS = {"dismiss", "gray_max", "gray_coin"}

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
SWIPE_DISTANCE = 700
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

# Para de procurar na primeira CATEGORIA que for encontrada.
#
# As categorias chegam ao detector na ORDEM DAS REGRAS do
# estado, e a StateMachine age na PRIMEIRA regra que casar —
# então procurar as de baixo depois de um acerto é trabalho que
# nunca vira ação.
#
# MEDIDO em tests/images/eatventure.png, estado NORMAL:
#
#   procurando tudo ............. 264 ms   (182 templates)
#   parando na prioridade .......  ~20 ms  quando casa em cima
#
# O ganho vem de onde estava o custo: `food` são 124 dos 185
# templates e 174 dos 312 ms, e é a ÚLTIMA prioridade em NORMAL.
#
# A parada é por CATEGORIA, nunca dentro dela: duas comidas na
# mesma tela são duas detecções da mesma categoria, e cortar no
# primeiro template faria o bot ver uma só.
#
# O QUE SE PERDE: o overlay passa a mostrar só até a categoria
# que venceu, não a tela inteira. Desligue enquanto estiver
# recortando template ou ajustando threshold — ali você quer ver
# tudo o que está na tela, mesmo o que o bot ignoraria.
#
# Sem filtro por estado (VISION_FILTER_BY_STATE desligado) isto
# não tem efeito: não há ordem de prioridade para respeitar.
VISION_PRIORITY_STOP = True

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

# Espera depois de um SWIPE, antes de agir de novo.
#
# O mesmo papel do ACTION_SETTLE, com valor próprio porque
# swipe não é toque: ele move a VISTA INTEIRA, e o jogo continua
# deslizando por inércia depois de o dedo sair. Um toque mexe um
# painel; um swipe muda a posição de tudo na tela.
#
# O sintoma sem isto: o bot rola a tela, detecta um alvo num
# frame capturado enquanto a vista ainda escorregava, e toca
# onde o alvo ESTAVA. O clique cai no cenário — ou pior, no que
# passou a ocupar aquele ponto.
#
# Como o ACTION_SETTLE, a condição é CAUSAL e não temporal: o
# bot só age sobre um frame CAPTURADO pelo menos este tanto
# depois de o swipe TERMINAR. Não é "dormir meio segundo" —
# subir o cooldown não resolveria, porque um detector mais lento
# voltaria a estourar a margem.
#
# Contado do FIM do swipe, não do início: o swipe leva
# SWIPE_DURATION_MS (500 ms) só para executar, mais o overhead
# do adb. Medir da submissão faria esta espera ser consumida
# pelo próprio gesto e o valor não teria efeito nenhum.
SWIPE_WAITING_TIME = 0.5

# Ações que movem a VISTA INTEIRA, e por isso usam a espera do
# swipe em vez da de um toque.
#
# A exploração (swipe_up/swipe_down) não precisa estar aqui: ela
# chama o ActionManager por outro caminho e já é tratada como
# swipe. Quem precisa é `scroll_bottom`, que passa pelo caminho
# das ações normais e é SEIS swipes seguidos — ou seja, mexe a
# vista mais que qualquer swipe solto.
VIEW_MOVING_ACTIONS = {"scroll_bottom"}

# True: só explora depois de encontrar uma tela sem ação.
# False: tenta explorar a cada EXPLORATION_DELAY, mesmo ao achar ação.
SWIPE_WAIT_FOR_NO_ACTION = True

# Tempo sem detectar nada antes de fazer swipe.
EXPLORATION_DELAY = 5.0

# Espera antes de voltar a explorar depois de ENCONTRAR algo.
#
# Achou alvo = o bot está no lugar certo da tela, e rolar agora
# só tiraria de vista o que ele acabou de achar. Mas também não
# pode parar de explorar: o restaurante cresce para os lados, e
# o que está fora da tela nunca vira detecção.
#
# Então achar não CANCELA a exploração, só a ADIA:
#
#   não achou nada .... swipe a cada EXPLORATION_DELAY (5 s)
#   achou algo ........ espera 15 s e volta ao ritmo de 5 s
#
# O CICLO NÃO ZERA. São 5 swipes para um lado, 5 para o outro,
# e achar algo no meio não devolve a contagem para o começo —
# senão o bot passa a sessão inteira varrendo o mesmo pedaço da
# tela, porque sempre acha algo antes de fechar a volta.
EXPLORATION_DELAY_AFTER_ACTION = 15.0

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
# Espera ao ENTRAR num estado, antes de agir nele.
#
# O caso concreto: ao abrir a tela de upgrade, o bot fechava ela
# na hora ("insta fecha").
#
# Por que: as regras de UPGRADE são, em ordem,
#
#   1. up_upgrade -> evolui o item
#   2. close      -> fecha e volta para NORMAL
#
# O painel abre com ANIMAÇÃO. No meio dela o "X" já está
# desenhado e casa com o template, mas os botões de upgrade
# ainda não — estão entrando, com tamanho e posição errados. Aí
# a regra 1 não encontra nada, a regra 2 encontra, e o bot fecha
# o painel que ele mesmo acabou de abrir.
#
# ACTION_SETTLE não resolve: ele é contado do TOQUE que abriu, e
# cobre animação de fechar painel (0.15-0.3 s), não a de abrir
# uma tela inteira. Aumentar o ACTION_SETTLE global deixaria
# TODA ação do bot mais lenta para consertar uma tela.
#
# Como toda espera deste projeto, a condição é CAUSAL e não
# temporal: o bot só age sobre um frame CAPTURADO pelo menos
# este tanto depois de entrar no estado. Não é um sleep — o
# VisionWorker também usa isto para não gastar passada em frame
# que mostra a animação.
#
# Estado ausente = sem espera de entrada.
#
# Custo de errar para cima: o estado tem timeout
# (STATE_TIMEOUTS), então uma espera longa demais come o tempo
# que o bot tem para agir lá dentro.
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
