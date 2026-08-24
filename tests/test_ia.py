"""
Testes do treino e do bot de IA.

    python tests/test_ia.py

Ficam AQUI e não em IA/, porque IA/ é o código que roda treino e
inferência — e nada ali deve rodar teste como efeito colateral.

Nada aqui treina modelo nem toca em device. O que está sob teste
é o que, errado, produz dano silencioso:

  - features de treino e de inferência divergindo, que faz o
    modelo receber entrada diferente da que aprendeu
  - split com vazamento, que faz a acurácia mentir
  - mapeamento de coordenada, que faz o clique cair no lugar
    errado
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

for candidato in (
    str(ROOT),
    str(ROOT / "src"),
    str(ROOT / "IA"),
):
    if candidato not in sys.path:
        sys.path.insert(0, candidato)

import features as feat                            # noqa: E402
import dataset_io as dio                           # noqa: E402


# =========================================================
# HELPERS
# =========================================================

def frame_com_alvo(alvo=(300, 1500, 96, 88), cor=(40, 200, 255)):
    """
    Frame do tamanho do device, com um bloco colorido na posição
    da caixa e fundo escuro em volta.
    """

    imagem = np.full((2400, 1080, 3), 30, np.uint8)

    x, y, w, h = alvo

    imagem[y:y + h, x:x + w] = cor

    return imagem


def registro(
    identificador="a",
    acao="open_box",
    estado="NORMAL",
    caixas=None,
    phash="0101",
    sessao="s1",
    outcome="changed",
):

    if caixas is None:

        caixas = [
            {
                "category": "box",
                "template": "item_001.png",
                "confidence": 0.97,
                "color_similarity": 0.99,
                "x": 300,
                "y": 1500,
                "width": 96,
                "height": 88,
                "acted": True,
            }
        ]

    return {
        "id": identificador,
        "session": sessao,
        "image": f"images/{identificador}.jpg",
        "phash": phash,
        "frame_width": 1080,
        "frame_height": 2400,
        "state": estado,
        "action": acao,
        "action_kind": "click",
        "click_x": 348,
        "click_y": 1544,
        "target_category": "box",
        "outcome": outcome,
        "boxes": caixas,
    }


# =========================================================
# FEATURES: A FONTE ÚNICA
# =========================================================

def test_recorte_pega_o_objeto_e_nao_a_tela():
    """
    O defeito de fundo da versão anterior: reduzir 1080x2400
    para 32x32 fazia o alvo de 95x94 px virar 2.8 x 1.3 px.

    Aqui o recorte tem de conter o objeto de verdade — ele
    precisa dominar o quadro, não ser 3 pixels.
    """

    alvo = (300, 1500, 96, 88)

    imagem = frame_com_alvo(alvo, cor=(255, 255, 255))

    recorte = feat.crop_box(imagem, {
        "x": alvo[0], "y": alvo[1],
        "width": alvo[2], "height": alvo[3],
    })

    assert recorte is not None

    # Com margem de 25%, o objeto ocupa cerca de (1/1.5)^2 = 44%
    # do recorte. Bem longe de 3 pixels numa tela inteira.
    brancos = int((recorte > 200).all(axis=2).sum())

    fracao = brancos / (recorte.shape[0] * recorte.shape[1])

    assert 0.3 < fracao < 0.8, fracao

    # E para comparar: na tela inteira reduzida a 32x32, o mesmo
    # alvo praticamente não existe.
    inteira = feat.screen_features(imagem)

    assert inteira is not None


def test_features_tem_tamanho_declarado():
    """
    O bot confere `n_features_in_` do modelo contra este
    número. Se FEATURE_SIZE mentir, a checagem passa e o modelo
    recebe entrada errada.
    """

    imagem = frame_com_alvo()

    vetor = feat.box_features(imagem, {
        "x": 300, "y": 1500, "width": 96, "height": 88,
    })

    assert vetor is not None

    assert len(vetor) == feat.FEATURE_SIZE, (
        len(vetor),
        feat.FEATURE_SIZE,
    )

    assert vetor.dtype == np.float32


def test_features_sao_deterministicas():
    """
    Duas chamadas com a mesma entrada têm de dar o mesmo vetor.
    Sem isso o treino aprende uma coisa e a inferência vê outra.
    """

    imagem = frame_com_alvo()

    caixa = {"x": 300, "y": 1500, "width": 96, "height": 88}

    a = feat.box_features(imagem, caixa)
    b = feat.box_features(imagem, caixa)

    assert np.array_equal(a, b)


def test_features_distinguem_cores():
    """
    O histograma HSV existe porque o jogo codifica por cor. Se
    dois recortes de cores bem diferentes derem vetores
    parecidos, ele não está fazendo nada.
    """

    caixa = {"x": 300, "y": 1500, "width": 96, "height": 88}

    azul = feat.box_features(
        frame_com_alvo(cor=(255, 60, 60)), caixa
    )

    verde = feat.box_features(
        frame_com_alvo(cor=(60, 255, 60)), caixa
    )

    distancia = float(np.abs(azul - verde).mean())

    assert distancia > 0.05, distancia


def test_recorte_fora_da_imagem_nao_estoura():
    """
    Caixa parcialmente fora do frame acontece de verdade — o
    detector acha objeto na borda. Índice negativo em numpy não
    dá erro: recorta do outro lado da imagem.
    """

    imagem = frame_com_alvo()

    for caixa in (
        {"x": -50, "y": -50, "width": 96, "height": 88},
        {"x": 1050, "y": 2380, "width": 96, "height": 88},
        {"x": 0, "y": 0, "width": 1, "height": 1},
    ):

        vetor = feat.box_features(imagem, caixa)

        # Ou devolve vetor do tamanho certo, ou None. O que não
        # pode é estourar nem devolver tamanho errado.
        if vetor is not None:
            assert len(vetor) == feat.FEATURE_SIZE, caixa


def test_recorte_degenerado_devolve_none():

    imagem = frame_com_alvo()

    assert feat.box_features(imagem, {
        "x": 500, "y": 500, "width": 0, "height": 0,
    }) is None


# =========================================================
# SPLIT SEM VAZAMENTO
# =========================================================

def test_split_nao_reparte_o_mesmo_grupo():
    """
    O erro que invalidava a acurácia anterior: 56% das amostras
    têm phash repetido, e o split aleatório colocava o mesmo
    quadro em treino e teste.
    """

    registros = [
        registro(f"r{i}", phash=f"h{i % 10}")
        for i in range(100)
    ]

    treino, teste, info = dio.split_groups(
        registros,
        train_ratio=0.7,
        mode="phash",
    )

    grupos_treino = {r["phash"] for r in treino}
    grupos_teste = {r["phash"] for r in teste}

    assert not (grupos_treino & grupos_teste), (
        grupos_treino & grupos_teste
    )

    assert len(treino) + len(teste) == 100

    assert info["grupos"] == 10


def test_split_por_sessao_separa_sessoes():
    """
    O teste mais duro: treinar numa partida e avaliar em outra.
    """

    registros = [
        registro(f"r{i}", sessao=f"s{i % 4}", phash=f"h{i}")
        for i in range(40)
    ]

    treino, teste, _ = dio.split_groups(
        registros,
        train_ratio=0.75,
        mode="session",
    )

    assert not (
        {r["session"] for r in treino}
        & {r["session"] for r in teste}
    )


def test_split_e_reproduzivel():
    """
    Mesma semente, mesmo split — senão comparar duas execuções
    não quer dizer nada.
    """

    registros = [
        registro(f"r{i}", phash=f"h{i % 12}")
        for i in range(60)
    ]

    a = dio.split_groups(registros, seed=7)[0]
    b = dio.split_groups(registros, seed=7)[0]

    assert [r["id"] for r in a] == [r["id"] for r in b]

    c = dio.split_groups(registros, seed=8)[0]

    assert [r["id"] for r in a] != [r["id"] for r in c]


def test_split_nunca_deixa_teste_vazio():
    """
    Com poucos grupos, arredondar para cima levaria tudo para o
    treino e a avaliação viraria divisão por zero.
    """

    registros = [registro(f"r{i}", phash="unico") for i in range(5)]
    registros += [registro("outro", phash="segundo")]

    treino, teste, _ = dio.split_groups(
        registros,
        train_ratio=0.95,
    )

    assert treino and teste


# =========================================================
# REFERÊNCIAS
# =========================================================

def test_baseline_da_maioria():

    rotulos = ["a"] * 7 + ["b"] * 3

    fracao, classe = dio.majority_baseline(rotulos)

    assert classe == "a"

    assert abs(fracao - 0.7) < 1e-9


def test_baseline_do_estado():
    """
    A referência que expôs o problema: 46.2% no dataset real,
    contra menos de 30% do modelo. Sem imprimir isto, ninguém
    percebe que o modelo está atrás de uma regra de uma linha.
    """

    registros = (
        [registro(f"a{i}", acao="upgrade_item", estado="UPGRADE")
         for i in range(8)]
        + [registro(f"b{i}", acao="close", estado="UPGRADE")
           for i in range(2)]
        + [registro(f"c{i}", acao="open_box", estado="NORMAL")
           for i in range(10)]
    )

    # UPGRADE: chuta upgrade_item, acerta 8/10.
    # NORMAL: chuta open_box, acerta 10/10.
    assert abs(dio.state_baseline(registros) - 0.9) < 1e-9


def test_teto_por_estado_e_categorias():
    """
    O número que justifica a arquitetura: se saber estado +
    categorias resolve quase toda a escolha de ação, então o
    modelo precisa aprender a DETECTAR, e a ação sai da tabela
    de prioridade que já existe.
    """

    def com_categorias(identificador, cats, acao, estado="NORMAL"):

        return registro(
            identificador,
            acao=acao,
            estado=estado,
            caixas=[
                {
                    "category": cat,
                    "x": 10, "y": 10,
                    "width": 50, "height": 50,
                    "acted": indice == 0,
                }
                for indice, cat in enumerate(cats)
            ],
        )

    registros = [
        com_categorias("a", ["box"], "open_box"),
        com_categorias("b", ["box"], "open_box"),
        com_categorias("c", ["food"], "food"),
        com_categorias("d", ["food"], "food"),
    ]

    assert dio.ceiling_state_categories(registros) == 1.0

    # Mesma entrada com rótulo diferente = teto abaixo de 100%,
    # e é exatamente o ruído irredutível que limita o modelo.
    registros.append(com_categorias("e", ["box"], "upgrade"))

    assert dio.ceiling_state_categories(registros) < 1.0


# =========================================================
# FILTRO POR RESULTADO
# =========================================================

def test_filtro_por_outcome():
    """
    Treinar só em `changed` deixa de fora as ações do professor
    que não funcionaram — é o que impede o modelo de herdar os
    erros do template matcher.
    """

    registros = [
        registro("a", outcome="changed"),
        registro("b", outcome="unchanged"),
        registro("c", outcome=None, acao=None),
    ]

    assert len(dio.filter_by_outcome(registros, None)) == 3

    apenas = dio.filter_by_outcome(registros, ["changed"])

    assert [r["id"] for r in apenas] == ["a"]

    # As negativas entram quando pedidas explicitamente: sem
    # elas o modelo aprende que sempre existe alvo.
    com_negativa = dio.filter_by_outcome(
        registros,
        ["changed", "negative"],
    )

    assert sorted(r["id"] for r in com_negativa) == ["a", "c"]


# =========================================================
# NEGATIVOS
# =========================================================

def test_negativos_nao_caem_sobre_as_caixas():
    """
    Negativo sobre um objeto ensina o contrário do pretendido:
    o modelo aprende que aquele objeto é fundo.
    """

    caixas = [
        {"category": "box", "x": 300, "y": 1500,
         "width": 96, "height": 88, "acted": True},
        {"category": "food", "x": 700, "y": 900,
         "width": 90, "height": 77, "acted": False},
    ]

    reg = registro("a", caixas=caixas)

    negativos = dio.sample_negative_boxes(
        reg,
        quantas=30,
        tamanhos=[(96, 88), (90, 77)],
        seed=1,
    )

    assert negativos, "não sorteou nenhum negativo"

    for negativo in negativos:

        candidata = (
            negativo["x"], negativo["y"],
            negativo["width"], negativo["height"],
        )

        for caixa in caixas:

            ocupada = (
                caixa["x"], caixa["y"],
                caixa["width"], caixa["height"],
            )

            assert dio.iou(candidata, ocupada) <= 0.1, (
                negativo,
                caixa,
            )


def test_negativos_ficam_dentro_do_frame():

    reg = registro("a")

    for negativo in dio.sample_negative_boxes(
        reg,
        quantas=40,
        tamanhos=[(96, 88), (300, 150)],
        seed=3,
    ):

        assert negativo["x"] >= 0
        assert negativo["y"] >= 0
        assert negativo["x"] + negativo["width"] <= 1080
        assert negativo["y"] + negativo["height"] <= 2400


def test_iou():

    assert dio.iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0

    assert dio.iou((0, 0, 10, 10), (100, 100, 10, 10)) == 0.0

    meio = dio.iou((0, 0, 10, 10), (5, 0, 10, 10))

    assert abs(meio - (50 / 150)) < 1e-9


# =========================================================
# LEITURA DO ÍNDICE
# =========================================================

def test_leitura_ignora_linha_invalida_e_imagem_ausente():
    """
    O samples.jsonl é append de uma thread: se a sessão morreu
    no meio, a última linha pode estar truncada. Isso não pode
    derrubar o treino.
    """

    pasta = Path(tempfile.mkdtemp())

    try:

        (pasta / "images").mkdir()

        import cv2

        cv2.imwrite(
            str(pasta / "images" / "existe.jpg"),
            np.full((100, 100, 3), 128, np.uint8),
        )

        linhas = [
            json.dumps({
                "id": "ok",
                "image": "images/existe.jpg",
                "boxes": [],
            }),
            "{isso nao e json",
            json.dumps({
                "id": "sem_imagem",
                "image": "images/nao_existe.jpg",
            }),
            json.dumps({"id": "sem_campo"}),
            "",
        ]

        (pasta / "samples.jsonl").write_text(
            "\n".join(linhas) + "\n",
            encoding="utf-8",
        )

        registros, aviso = dio.read_index(pasta)

        assert [r["id"] for r in registros] == ["ok"]

        assert aviso["invalidas"] == 1

        assert aviso["sem_imagem"] == 2

        # O caminho resolvido tem de existir de verdade.
        assert registros[0]["_path"].exists()

    finally:

        shutil.rmtree(pasta, ignore_errors=True)


def test_indice_ausente_da_erro_com_instrucao():

    pasta = Path(tempfile.mkdtemp())

    try:

        dio.read_index(pasta)

    except FileNotFoundError as erro:

        assert "RECORD_DATASET" in str(erro), str(erro)

        return

    finally:

        shutil.rmtree(pasta, ignore_errors=True)

    raise AssertionError("deveria ter levantado")


# =========================================================
# RÓTULOS
# =========================================================

def test_negativa_vira_classe_propria():
    """
    "não fazer nada" é uma decisão. Descartar as negativas
    ensina que sempre há algo a fazer.
    """

    assert dio.action_label(registro("a", acao=None)) == "negative"

    assert dio.action_label(registro("a", acao="open_box")) == "open_box"


def test_todas_as_caixas_viram_exemplo():
    """
    É isso que dá 65.730 exemplos a partir de 29.297 frames: as
    caixas que NÃO viraram ação também são rótulo.
    """

    caixas = [
        {"category": "box", "x": 1, "y": 1,
         "width": 10, "height": 10, "acted": True},
        {"category": "food", "x": 20, "y": 20,
         "width": 10, "height": 10, "acted": False},
        {"category": "upgrade", "x": 40, "y": 40,
         "width": 10, "height": 10, "acted": False},
    ]

    rotulos = dio.box_labels(registro("a", caixas=caixas))

    assert len(rotulos) == 3

    assert [cat for _, cat, _ in rotulos] == [
        "box", "food", "upgrade",
    ]

    assert [agiu for _, _, agiu in rotulos] == [True, False, False]


def test_caixa_degenerada_e_ignorada():

    caixas = [
        {"category": "box", "x": 1, "y": 1,
         "width": 0, "height": 10, "acted": True},
        {"category": "", "x": 1, "y": 1,
         "width": 10, "height": 10, "acted": False},
    ]

    assert dio.box_labels(registro("a", caixas=caixas)) == []


# =========================================================
# PROPONENTE
# =========================================================

def test_proponente_acha_bloco_colorido():
    """
    O proponente é a parte EXPERIMENTAL: substitui os 184
    templates por proposta de região. Aqui só se verifica o
    contrato geométrico, com um bloco saturado sintético.

    Em tela real ele precisa ser conferido com
    `--debug-proposals` — nenhum teste sintético prova isso.
    """

    import proposer

    imagem = np.full((2400, 1080, 3), 40, np.uint8)

    # Bloco bem saturado, do tamanho de um botão real.
    imagem[1500:1588, 300:396] = (255, 120, 20)

    candidatos = proposer.propose(imagem)

    assert candidatos, "não propôs nada"

    # Algum candidato tem de cobrir o bloco.
    alvo = (300, 1500, 96, 88)

    melhor = max(
        dio.iou(c, alvo) for c in candidatos
    )

    assert melhor > 0.3, (melhor, candidatos[:5])


def test_proponente_respeita_limites_de_tamanho():

    import proposer

    imagem = np.full((2400, 1080, 3), 40, np.uint8)

    # Muito pequeno e muito grande: nenhum pode passar.
    imagem[100:110, 100:110] = (255, 120, 20)
    imagem[500:900, 0:1080] = (255, 120, 20)

    for x, y, w, h in proposer.propose(imagem):

        assert w >= proposer.MIN_SIDE
        assert h >= proposer.MIN_SIDE
        assert w <= proposer.MAX_WIDTH
        assert h <= proposer.MAX_HEIGHT


def test_proponente_tem_teto_de_candidatos():
    """
    Cada candidato custa uma classificação. Sem teto, uma tela
    cheia de cor derruba o FPS.
    """

    import proposer

    rng = np.random.default_rng(0)

    imagem = np.full((2400, 1080, 3), 40, np.uint8)

    for _ in range(200):

        x = int(rng.integers(0, 950))
        y = int(rng.integers(0, 2300))

        imagem[y:y + 60, x:x + 60] = (
            int(rng.integers(100, 255)),
            int(rng.integers(100, 255)),
            int(rng.integers(100, 255)),
        )

    assert len(proposer.propose(imagem)) <= proposer.MAX_PROPOSALS


def test_supressao_remove_repetidos_da_mesma_categoria():

    import proposer

    def det(categoria, x, y, confianca):

        return {
            "category": categoria,
            "x": x, "y": y,
            "width": 100, "height": 100,
            "confidence": confianca,
        }

    deteccoes = [
        det("box", 100, 100, 0.9),
        det("box", 105, 105, 0.7),     # mesma coisa, pior
        det("food", 100, 100, 0.8),    # outra categoria, fica
        det("box", 900, 900, 0.6),     # longe, fica
    ]

    mantidas = proposer.suppress(deteccoes)

    assert len(mantidas) == 3, mantidas

    caixas = [(d["category"], d["x"]) for d in mantidas]

    assert ("box", 105) not in caixas

    # Sobrou a de maior confiança.
    assert ("box", 100) in caixas


# =========================================================
# CONTRATO COM A MÁQUINA DE ESTADOS
# =========================================================

def test_deteccao_do_modelo_tem_o_formato_que_a_maquina_le():
    """
    O bot alimenta a StateMachine com as detecções do modelo. Se
    o formato divergir do que o vision/detector.py produz, a
    máquina não sabe ler — e o sintoma é bot parado, não erro.
    """

    import bot_ai

    class ModeloFalso:

        def classify_batch(self, vetores):
            return [("box", 0.95)] * len(vetores)

    imagem = frame_com_alvo()

    caixas = [{"x": 300, "y": 1500, "width": 96, "height": 88}]

    deteccoes = bot_ai.detect_with_model(
        ModeloFalso(),
        imagem,
        caixas,
        min_confidence=0.5,
    )

    assert len(deteccoes) == 1

    deteccao = deteccoes[0]

    # As chaves que core/state_machine.py e vision/detector.py
    # usam.
    for chave in (
        "category", "name", "confidence",
        "color_similarity", "x", "y", "width", "height",
    ):
        assert chave in deteccao, chave

    assert deteccao["category"] == "box"

    assert deteccao["x"] == 300
    assert deteccao["width"] == 96


def test_maquina_de_estados_aceita_as_deteccoes_do_modelo():
    """
    Fim a fim da decisão, sem device: detecção do modelo entra,
    ação sai — e o alvo é a CAIXA, não o centro da tela.

    É o conserto do defeito principal da versão anterior, que
    tocava sempre em (largura/2, altura/2).
    """

    import bot_ai
    from core import state_machine as sm

    class AcoesFalsas:

        def __init__(self):
            self.feitas = []

        def execute(self, action, detection=None):
            self.feitas.append((action, detection))
            return True

        def is_busy(self):
            return False

        def swipe(self, direction):
            return True

    class ModeloFalso:

        def classify_batch(self, vetores):
            return [("box", 0.95)] * len(vetores)

    imagem = frame_com_alvo()

    deteccoes = bot_ai.detect_with_model(
        ModeloFalso(),
        imagem,
        [{"x": 300, "y": 1500, "width": 96, "height": 88}],
        min_confidence=0.5,
    )

    acoes = AcoesFalsas()

    maquina = sm.StateMachine(acoes)

    maquina.action_cooldown = 0.0
    maquina.action_settle = 0.0
    maquina.last_action_time = 0.0

    maquina.update(deteccoes, 0.0)

    assert acoes.feitas, "a máquina não agiu"

    acao, alvo = acoes.feitas[0]

    assert acao == "open_box", acao

    # O alvo é a caixa detectada, não o meio da tela.
    assert alvo["x"] == 300 and alvo["y"] == 1500

    centro_da_tela = (1080 // 2, 2400 // 2)

    assert (
        alvo["x"] + alvo["width"] // 2,
        alvo["y"] + alvo["height"] // 2,
    ) != centro_da_tela


def test_background_nao_vira_deteccao():
    """
    A classe "background" existe para o modelo poder dizer "aqui
    não tem nada". Se ela virasse detecção, a máquina agiria
    sobre cenário.
    """

    import bot_ai

    class ModeloFalso:

        def classify_batch(self, vetores):
            return [("background", 0.99)] * len(vetores)

    deteccoes = bot_ai.detect_with_model(
        ModeloFalso(),
        frame_com_alvo(),
        [{"x": 300, "y": 1500, "width": 96, "height": 88}],
        min_confidence=0.5,
    )

    assert deteccoes == []


def test_confianca_baixa_e_descartada():

    import bot_ai

    class ModeloFalso:

        def classify_batch(self, vetores):
            return [("box", 0.20)] * len(vetores)

    assert bot_ai.detect_with_model(
        ModeloFalso(),
        frame_com_alvo(),
        [{"x": 300, "y": 1500, "width": 96, "height": 88}],
        min_confidence=0.60,
    ) == []


def test_deteccoes_saem_ordenadas_por_confianca():
    """
    A StateMachine pega a PRIMEIRA detecção de cada categoria,
    então a ordem decide qual alvo é tocado.
    """

    import bot_ai

    class ModeloFalso:

        def __init__(self):
            self.confiancas = [0.6, 0.95, 0.8]

        def classify_batch(self, vetores):
            return [
                ("box", self.confiancas[i])
                for i in range(len(vetores))
            ]

    deteccoes = bot_ai.detect_with_model(
        ModeloFalso(),
        frame_com_alvo(),
        [
            {"x": 100, "y": 100, "width": 96, "height": 88},
            {"x": 300, "y": 1500, "width": 96, "height": 88},
            {"x": 500, "y": 800, "width": 96, "height": 88},
        ],
        min_confidence=0.5,
    )

    confiancas = [d["confidence"] for d in deteccoes]

    assert confiancas == sorted(confiancas, reverse=True), confiancas


# =========================================================
# RUNNER
# =========================================================

def main():

    testes = [
        valor
        for nome, valor in sorted(globals().items())
        if nome.startswith("test_") and callable(valor)
    ]

    falhas = 0

    for teste in testes:

        try:

            teste()

            print(f"  ok    {teste.__name__}")

        except AssertionError as erro:

            falhas += 1

            print(f"  FALHA {teste.__name__}: {erro}")

        except Exception as erro:

            falhas += 1

            print(
                f"  ERRO  {teste.__name__}: "
                f"{type(erro).__name__}: {erro}"
            )

    print()

    if falhas:

        print(f"{falhas}/{len(testes)} falharam.")

        return 1

    print(f"{len(testes)}/{len(testes)} passaram.")

    return 0


if __name__ == "__main__":

    sys.exit(main())
