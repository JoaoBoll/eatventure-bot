"""Dataset I/O: leitura de todas as raízes, split por grupo (phash/sessão) para evitar vazamento."""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Guarda próprio: dataset_io é importado por train_ai, bot_ai, simulate_bot e
# pelos testes — não dá para depender de quem importa ter montado o path.
for _candidato in (
    str(Path(__file__).resolve().parent.parent / "src"),
):
    if _candidato not in sys.path:
        sys.path.insert(0, _candidato)

# Convenção de pastas mora num lugar só, com quem escreve (src/dataset).
from dataset.layout import (  # noqa: E402
    DATA_DIRNAME,
    INDEX_NAME,
    shard_roots,
)


def _read_shard(raiz, rotulo):
    """Registros de uma raiz; `image` é relativo a ELA, não à raiz do dataset."""

    # linha inválida é contada e ignorada, não derruba a leitura: o arquivo
    # é append de uma thread e pode truncar se a sessão foi morta no meio
    registros = []

    invalidas = 0
    sem_imagem = 0

    with (raiz / INDEX_NAME).open("r", encoding="utf-8") as arquivo:

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
            registro["_root"] = raiz
            registro["_shard"] = rotulo

            registros.append(registro)

    return registros, invalidas, sem_imagem


def read_index(dataset_root):
    """Registros de TODAS as raízes sob dataset_root, juntos."""

    raiz = Path(dataset_root)

    raizes = shard_roots(raiz)

    if not raizes:

        raise FileNotFoundError(
            f"Nenhum índice ({INDEX_NAME}) encontrado em {raiz} "
            f"nem em {raiz / DATA_DIRNAME}/<resolucao>/\n"
            "Ligue DATASET_SAVE em src/core/config.py e rode "
            "o bot para gerar amostras."
        )

    registros = []

    invalidas = 0
    sem_imagem = 0

    por_raiz = {}

    for pasta in raizes:

        rotulo = "." if pasta == raiz else pasta.name

        lidos, ruins, sem = _read_shard(pasta, rotulo)

        registros.extend(lidos)

        invalidas += ruins
        sem_imagem += sem

        por_raiz[rotulo] = len(lidos)

    return registros, {
        "invalidas": invalidas,
        "sem_imagem": sem_imagem,
        "raizes": por_raiz,
    }


def action_label(registro):
    # negativa (sem alvo) vira classe "negative" em vez de descartada:
    # "não fazer nada" é uma decisão que o modelo também precisa aprender
    acao = registro.get("action")

    if acao in (None, ""):
        return "negative"

    return str(acao)


def box_labels(registro):
    """Devolve [(caixa, categoria, agiu)] para TODAS as caixas do frame, não só a
    que virou ação — as outras são rótulo grátis."""

    saida = []

    for caixa in registro.get("boxes") or []:

        categoria = caixa.get("category")

        if not categoria:
            continue

        if caixa.get("width", 0) <= 0 or caixa.get("height", 0) <= 0:
            continue

        saida.append((caixa, str(categoria), bool(caixa.get("acted"))))

    return saida


def filter_by_outcome(registros, outcomes):
    """Mantém só amostras com `outcome` em `outcomes` (None mantém tudo). Treinar só
    em `changed` evita herdar erros do template matcher. Negativas (outcome None)
    passam quando "negative" é pedido explicitamente."""

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


def group_key(registro, mode="phash"):
    # "phash": quadros visualmente iguais no mesmo lado (56% das amostras repetem).
    # "session": sessão inteira num lado só — mede generalizar p/ outra partida
    if mode == "session":
        return registro.get("session") or "sem-sessao"

    return registro.get("phash") or registro.get("id")


def split_groups(registros, train_ratio=0.8, mode="phash", seed=42):
    """Divide em (treino, teste, info) por GRUPO, não por amostra — nenhum grupo
    aparece nos dois lados. Proporção é sobre grupos, não amostras."""

    if not 0.05 <= train_ratio <= 0.95:

        raise ValueError(
            "train_ratio deve ficar entre 0.05 e 0.95"
        )

    grupos = defaultdict(list)

    for registro in registros:
        grupos[group_key(registro, mode)].append(registro)

    # ordena antes de embaralhar: dict tem ordem de inserção (depende do
    # arquivo), sem isso o seed não garante reprodutibilidade
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


def majority_baseline(rotulos):
    """Acerto de sempre chutar a classe mais comum — referência mínima para a acurácia."""

    if not rotulos:
        return 0.0, None

    contagem = Counter(rotulos)

    classe, quantas = contagem.most_common(1)[0]

    return quantas / len(rotulos), classe


def state_baseline(registros):
    """Acerto de chutar, por estado, a ação mais comum naquele estado — um
    modelo de imagem que não bate isto está atrás de uma regra de uma linha."""

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
    """Teto: acerto de conhecer estado + categorias na tela — justifica a
    arquitetura: se isso resolve a escolha de ação, o modelo só precisa DETECTAR."""

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


def iou(a, b):
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
    """Sorteia regiões do frame sem caixa rotulada — sem negativos, o classificador
    aprende que todo recorte é objeto. `tamanhos` usa dimensões reais do dataset para
    não ensinar o atalho de distinguir negativo por tamanho."""

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
