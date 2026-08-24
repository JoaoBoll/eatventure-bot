"""
Leitura do dataset e montagem dos conjuntos de treino/teste.

Sem OpenCV e sem sklearn nas funções de decisão, para poderem
ser testadas sem imagem e sem treino.

--------------------------------------------------------------
O PROBLEMA DO VAZAMENTO
--------------------------------------------------------------

O `train_test_split` aleatório da versão anterior era inválido
neste dataset. Medido: das 29.297 amostras, **56% têm `phash`
repetido** — o bot age ~1x/s sobre uma tela quase estática, e o
mesmo quadro cai em treino E em teste.

Isso não deixa a acurácia "um pouco otimista": deixa ela sem
significado. O modelo pode ter decorado o frame de teste, ou
pode estar sendo penalizado por variação de animação. Não há
como saber qual, e é por isso que os 30% não diziam nada.

Aqui o split é por GRUPO: todas as amostras com o mesmo `phash`
vão para o mesmo lado, e opcionalmente separa-se por SESSÃO
inteira, que é o teste mais duro — treinar num restaurante e
avaliar em outro.
"""

import json
from collections import Counter, defaultdict
from pathlib import Path


# =========================================================
# LEITURA
# =========================================================

def read_index(dataset_root):
    """
    Lê o samples.jsonl e devolve os registros que têm imagem no
    disco.

    Linha inválida é contada e ignorada, não derruba a leitura:
    o arquivo é append de uma thread e pode ter uma linha
    truncada se a sessão foi morta no meio.
    """

    raiz = Path(dataset_root)

    caminho = raiz / "samples.jsonl"

    if not caminho.exists():

        raise FileNotFoundError(
            f"Índice não encontrado: {caminho}\n"
            "Ligue RECORD_DATASET em src/core/config.py e rode "
            "o bot para gerar amostras."
        )

    registros = []

    invalidas = 0
    sem_imagem = 0

    with caminho.open("r", encoding="utf-8") as arquivo:

        for linha in arquivo:

            linha = linha.strip()

            if not linha:
                continue

            try:
                registro = json.loads(linha)

            except json.JSONDecodeError:

                invalidas += 1

                continue

            relativo = registro.get("image")

            if not relativo:

                sem_imagem += 1

                continue

            imagem = raiz / relativo

            if not imagem.exists():

                sem_imagem += 1

                continue

            registro["_path"] = imagem

            registros.append(registro)

    return registros, {
        "invalidas": invalidas,
        "sem_imagem": sem_imagem,
    }


# =========================================================
# RÓTULOS
# =========================================================

def action_label(registro):
    """
    Rótulo do modelo `action`: a ação que o bot tomou.

    Negativa (tela sem alvo) vira a classe "negative" em vez de
    ser descartada — "não fazer nada" é uma decisão, e sem ela o
    modelo aprende que sempre há algo a fazer.
    """

    acao = registro.get("action")

    if acao in (None, ""):
        return "negative"

    return str(acao)


def box_labels(registro):
    """
    Rótulos do modelo `category`: uma entrada por caixa
    detectada no frame.

    Devolve [(caixa, categoria, agiu)].

    São TODAS as caixas, não só a que virou ação — as outras são
    rótulo grátis, e é isso que dá 65.730 exemplos a partir de
    29.297 frames.
    """

    saida = []

    for caixa in registro.get("boxes") or []:

        categoria = caixa.get("category")

        if not categoria:
            continue

        if caixa.get("width", 0) <= 0 or caixa.get("height", 0) <= 0:
            continue

        saida.append((caixa, str(categoria), bool(caixa.get("acted"))))

    return saida


# =========================================================
# FILTROS
# =========================================================

def filter_by_outcome(registros, outcomes):
    """
    Mantém só as amostras cujo resultado está em `outcomes`.

    `outcomes=None` mantém tudo.

    Para que serve: treinar só em `changed` deixa de fora as
    ações do professor que NÃO funcionaram. É o que impede o
    modelo de herdar os erros do template matcher. Custa metade
    do dataset (14.224 de 29.297 são `changed`), então vale
    medir os dois.

    As negativas (outcome None) passam quando "negative" é
    pedido explicitamente.
    """

    if not outcomes:
        return registros

    alvo = set(outcomes)

    mantidas = []

    for registro in registros:

        resultado = registro.get("outcome")

        if resultado is None:

            if "negative" in alvo or "none" in alvo:
                mantidas.append(registro)

            continue

        if resultado in alvo:
            mantidas.append(registro)

    return mantidas


# =========================================================
# SPLIT SEM VAZAMENTO
# =========================================================

def group_key(registro, mode="phash"):
    """
    Chave de agrupamento para o split.

    "phash"   quadros visualmente iguais ficam do mesmo lado.
              É o mínimo necessário: 56% das amostras têm phash
              repetido.

    "session" a sessão inteira vai para um lado. É o teste mais
              honesto — mede generalizar para outra partida, não
              para outro frame da mesma tela.
    """

    if mode == "session":
        return registro.get("session") or "sem-sessao"

    return registro.get("phash") or registro.get("id")


def split_groups(registros, train_ratio=0.8, mode="phash", seed=42):
    """
    Divide em (treino, teste) por GRUPO, não por amostra.

    Devolve (treino, teste, info).

    Nenhum grupo aparece nos dois lados — é isso que torna a
    acurácia comparável com a realidade. A proporção é sobre
    grupos, então a contagem final de amostras não bate exato
    com o train_ratio, e isso é esperado.
    """

    if not 0.05 <= train_ratio <= 0.95:

        raise ValueError(
            "train_ratio deve ficar entre 0.05 e 0.95"
        )

    grupos = defaultdict(list)

    for registro in registros:
        grupos[group_key(registro, mode)].append(registro)

    # Ordena antes de embaralhar: dict tem ordem de inserção,
    # que depende da ordem do arquivo. Sem isto o "seed" não
    # garante reprodutibilidade entre execuções.
    chaves = sorted(grupos)

    import random

    random.Random(seed).shuffle(chaves)

    corte = max(1, int(round(len(chaves) * train_ratio)))

    if corte >= len(chaves):
        corte = len(chaves) - 1

    treino = [r for chave in chaves[:corte] for r in grupos[chave]]
    teste = [r for chave in chaves[corte:] for r in grupos[chave]]

    return treino, teste, {
        "mode": mode,
        "grupos": len(chaves),
        "grupos_treino": corte,
        "grupos_teste": len(chaves) - corte,
        "amostras_treino": len(treino),
        "amostras_teste": len(teste),
    }


# =========================================================
# BASELINES
# =========================================================

def majority_baseline(rotulos):
    """
    Acerto de sempre chutar a classe mais comum.

    Sem este número a acurácia não quer dizer nada: no dataset
    atual chutar `open_box` já dá 16.7%.
    """

    if not rotulos:
        return 0.0, None

    contagem = Counter(rotulos)

    classe, quantas = contagem.most_common(1)[0]

    return quantas / len(rotulos), classe


def state_baseline(registros):
    """
    Acerto de chutar, para cada estado, a ação mais comum
    NAQUELE estado.

    É a referência que expõe o problema: dá 46.2% no dataset
    atual, contra menos de 30% do modelo anterior. Um modelo de
    imagem que não bate isto está atrás de uma regra de uma
    linha.
    """

    if not registros:
        return 0.0

    por_estado = defaultdict(Counter)

    for registro in registros:

        por_estado[registro.get("state")][action_label(registro)] += 1

    acertos = sum(
        contagem.most_common(1)[0][1]
        for contagem in por_estado.values()
    )

    return acertos / len(registros)


def ceiling_state_categories(registros):
    """
    Teto: acerto de conhecer o estado E as categorias na tela.

    Dá 96.0% no dataset atual, e é a justificativa de toda a
    arquitetura — se detectar categoria resolve 96% da escolha
    de ação, o que o modelo precisa aprender é DETECTAR, e a
    ação sai da tabela de prioridade que já existe e já é
    testada.
    """

    if not registros:
        return 0.0

    combinacoes = defaultdict(Counter)

    for registro in registros:

        categorias = frozenset(
            caixa.get("category")
            for caixa in registro.get("boxes") or []
        )

        chave = (registro.get("state"), categorias)

        combinacoes[chave][action_label(registro)] += 1

    acertos = sum(
        contagem.most_common(1)[0][1]
        for contagem in combinacoes.values()
    )

    return acertos / len(registros)


# =========================================================
# NEGATIVOS PARA O MODELO DE CAIXA
# =========================================================

def iou(a, b):
    """
    Sobreposição entre duas caixas (x, y, w, h).
    """

    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b

    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)

    if ix2 <= ix1 or iy2 <= iy1:
        return 0.0

    intersecao = (ix2 - ix1) * (iy2 - iy1)

    uniao = aw * ah + bw * bh - intersecao

    return intersecao / uniao if uniao > 0 else 0.0


def sample_negative_boxes(
    registro,
    quantas,
    tamanhos,
    seed=0,
    max_iou=0.1,
    tentativas_por_caixa=12,
):
    """
    Sorteia regiões do frame que NÃO contêm caixa rotulada.

    Por que precisa existir: sem negativos, o classificador de
    recorte só vê objetos e aprende que todo recorte é algum
    objeto. Na inferência, cada pedaço de cenário viraria uma
    detecção.

    `tamanhos` é a lista de (w, h) observada no dataset — usar
    tamanhos reais evita que o negativo seja distinguível do
    positivo só pela dimensão, o que ensinaria o atalho errado.
    """

    import random

    sorteio = random.Random(seed)

    largura = registro.get("frame_width") or 0
    altura = registro.get("frame_height") or 0

    if largura <= 0 or altura <= 0 or not tamanhos:
        return []

    ocupadas = [
        (c["x"], c["y"], c["width"], c["height"])
        for c in registro.get("boxes") or []
    ]

    encontradas = []

    for _ in range(quantas):

        for _ in range(tentativas_por_caixa):

            w, h = sorteio.choice(tamanhos)

            if w >= largura or h >= altura:
                continue

            x = sorteio.randint(0, largura - w)
            y = sorteio.randint(0, altura - h)

            candidata = (x, y, w, h)

            if any(
                iou(candidata, ocupada) > max_iou
                for ocupada in ocupadas
            ):
                continue

            encontradas.append(
                {"x": x, "y": y, "width": w, "height": h}
            )

            break

    return encontradas
