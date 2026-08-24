#!/usr/bin/env python3
"""
Bot com visão por modelo treinado.

    # ver o que o modelo enxerga, sem tocar no jogo
    python IA/bot_ai.py

    # comparar modelo x template matching, quadro a quadro
    python IA/bot_ai.py --source templates --compare

    # deixar agir
    python IA/bot_ai.py --auto --min-confidence 0.80

    # revisar imagens gravadas, sem device
    python IA/bot_ai.py --demo --demo-limit 20

==============================================================
O QUE ESTAVA ERRADO NA VERSÃO ANTERIOR
==============================================================

1. CLICAVA SEMPRE NO MEIO DA TELA.

       center = (frame.shape[1] // 2, frame.shape[0] // 2)
       detection = {"x": center[0] - 50, ...}

   O modelo previa QUAL ação, nunca ONDE. Então o toque ia para
   o centro do frame com uma caixa falsa de 100x100 em volta.
   Mesmo com um classificador perfeito, o clique cairia no lugar
   errado — e não havia como isso funcionar por acaso.

2. IGNORAVA A MÁQUINA DE ESTADOS.

   A ação certa depende do estado: `up_food` em FOOD evolui a
   comida, em NEW_POINT libera o ponto. Medido no dataset, só
   conhecer o estado já explica 46.2% da ação, e estado +
   categorias explica 96%. O bot decidia sem nenhum dos dois.

3. NÃO TINHA COOLDOWN NEM ESPERA DE EFEITO.

   Agia a cada quadro, sobre telas de antes da própria ação —
   o mesmo duplo toque que o bot de regras já teve e resolveu.

4. `set_frame_size(REFERENCE_WIDTH, REFERENCE_HEIGHT)` fixo.

   Se o stream vier em outra resolução, todo toque sai
   convertido errado.

==============================================================
COMO FUNCIONA AGORA
==============================================================

    frame -> caixas candidatas -> modelo diz a categoria
          -> StateMachine escolhe a ação e o alvo
          -> ActionManager toca

O modelo faz só a parte que é aprendizado: reconhecer. A
escolha da ação, a prioridade, as coordenadas, o cooldown, a
espera de efeito e a escada de fechamento vêm de
core/state_machine.py, que já existe e já é testado. É de lá que
sai o clique no lugar certo.

`--source templates` (padrão) pega as caixas do detector atual e
usa o modelo para classificar cada uma. Serve para MEDIR o
modelo em tela real, com `--compare`, antes de confiar nele.

`--source proposer` troca o detector por proposta de região por
cor — o caminho para largar os 184 templates. É experimental.

Este arquivo não executa nada sozinho e não tem testes: eles
estão em tests/test_ia.py.
"""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import joblib
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

for candidato in (str(ROOT), str(ROOT / "src"), str(ROOT / "IA")):
    if candidato not in sys.path:
        sys.path.insert(0, candidato)

from features import (                                # noqa: E402
    FEATURE_SIZE,
    box_features,
    screen_features,
)

DEFAULT_MODEL = ROOT / "IA" / "model.joblib"
DATASET_ROOT = ROOT / "dataset"

WINDOW_NAME = "IA - visao"
WINDOW_WIDTH = 405
WINDOW_HEIGHT = 900

CORES = {
    "box": (60, 200, 255),
    "food": (80, 220, 120),
    "close": (60, 60, 255),
    "upgrade": (255, 200, 60),
    "up_upgrade": (255, 160, 60),
    "up_food": (200, 120, 255),
    "new_point": (255, 255, 90),
    "gray_max": (160, 160, 160),
    "build": (120, 255, 200),
    "plane": (255, 120, 200),
    "renovate": (90, 255, 255),
    "fly": (200, 255, 90),
    "open_store": (255, 90, 160),
}

COR_PADRAO = (200, 200, 200)


# =========================================================
# MODELO
# =========================================================

class Model:
    """
    Modelo treinado, com o metadado que diz COMO ele espera a
    entrada.
    """

    def __init__(self, clf, meta, path):

        self.clf = clf
        self.meta = meta
        self.path = path

        self.kind = meta.get("kind", "category")

        self.labels = [str(c) for c in getattr(clf, "classes_", [])]

        if not self.labels:
            self.labels = [
                str(x) for x in meta.get("labels", [])
            ]

        if not self.labels:

            raise SystemExit(
                f"Modelo sem classes: {path}\n"
                "Treine de novo com IA/train_ai.py."
            )

    # -----------------------------------------------------

    @classmethod
    def load(cls, path):

        caminho = Path(path).expanduser().resolve()

        if not caminho.exists():

            raise SystemExit(
                f"Modelo não encontrado: {caminho}\n\n"
                "Treine primeiro:\n"
                "  python IA/train_ai.py\n"
            )

        clf = joblib.load(caminho)

        meta_path = caminho.with_suffix(".meta.json")

        meta = {}

        if meta_path.exists():

            try:
                meta = json.loads(
                    meta_path.read_text(encoding="utf-8")
                )

            except json.JSONDecodeError:

                print(
                    f"[aviso] {meta_path.name} ilegível — "
                    "assumindo modelo de categoria."
                )

        modelo = cls(clf, meta, caminho)

        modelo._check_features()

        return modelo

    def _check_features(self):
        """
        Confere que o modelo espera o mesmo número de features
        que o IA/features.py produz hoje.

        Sem isto, mudar PATCH_SIZE e rodar um modelo antigo dá
        erro obscuro de shape no meio do loop — ou, pior, passa
        e prevê lixo.
        """

        esperado = getattr(self.clf, "n_features_in_", None)

        if esperado is None:
            return

        atual = (
            FEATURE_SIZE
            if self.kind == "category"
            else len(screen_features(np.zeros((10, 10, 3), np.uint8)))
        )

        if int(esperado) != int(atual):

            raise SystemExit(
                f"Modelo incompatível: {self.path.name} espera "
                f"{esperado} features, mas IA/features.py produz "
                f"{atual}.\n\n"
                "IA/features.py mudou depois do treino. "
                "Retreine:\n"
                f"  python IA/train_ai.py --kind {self.kind}\n"
            )

    # -----------------------------------------------------

    def classify_batch(self, vetores):
        """
        Classifica vários recortes de uma vez.

        Em lote de propósito: uma chamada por recorte custaria
        overhead de sklearn por candidato, e são dezenas por
        quadro.
        """

        if not vetores:
            return []

        matriz = np.asarray(vetores, dtype=np.float32)

        if hasattr(self.clf, "predict_proba"):

            probabilidades = self.clf.predict_proba(matriz)

            indices = probabilidades.argmax(axis=1)

            return [
                (
                    str(self.clf.classes_[indice]),
                    float(probabilidades[linha, indice]),
                )
                for linha, indice in enumerate(indices)
            ]

        previsto = self.clf.predict(matriz)

        return [(str(p), 1.0) for p in previsto]


# =========================================================
# DETECÇÃO
# =========================================================

def detect_with_model(model, frame, caixas, min_confidence):
    """
    Classifica cada caixa candidata e devolve detecções no
    formato que a StateMachine consome.

    O formato tem de ser o MESMO do vision/detector.py, senão a
    máquina de estados não sabe ler: category, name, confidence,
    color_similarity, x, y, width, height.
    """

    if not caixas:
        return []

    vetores = []
    validas = []

    for caixa in caixas:

        vetor = box_features(frame, caixa)

        if vetor is None:
            continue

        vetores.append(vetor)
        validas.append(caixa)

    resultados = model.classify_batch(vetores)

    deteccoes = []

    for caixa, (categoria, confianca) in zip(validas, resultados):

        # "background" é a classe de recorte sem objeto, criada
        # no treino justamente para o modelo poder dizer "aqui
        # não tem nada".
        if categoria == "background":
            continue

        if confianca < min_confidence:
            continue

        x, y, w, h = _box_tuple(caixa)

        deteccoes.append(
            {
                "category": categoria,
                "name": f"modelo:{categoria}",
                "confidence": confianca,

                # A StateMachine não usa, mas o overlay e os
                # testes do detector esperam a chave.
                "color_similarity": confianca,

                "x": x,
                "y": y,
                "width": w,
                "height": h,
            }
        )

    # Maior confiança primeiro: a StateMachine pega a PRIMEIRA
    # detecção de cada categoria, então a ordem decide qual
    # alvo é usado.
    deteccoes.sort(key=lambda d: -d["confidence"])

    return deteccoes


def _box_tuple(caixa):

    if isinstance(caixa, dict):
        return (
            int(caixa["x"]),
            int(caixa["y"]),
            int(caixa["width"]),
            int(caixa["height"]),
        )

    return tuple(int(v) for v in caixa)


# =========================================================
# FONTES DE CANDIDATOS
# =========================================================

class TemplateSource:
    """
    Caixas vindas do detector de template atual.

    Não é circular: o detector diz ONDE olhar, e o modelo diz O
    QUE é. Serve para medir o modelo contra o professor em tela
    real (`--compare`) antes de confiar nele — e é o caminho que
    funciona hoje, sem depender do proponente experimental.
    """

    nome = "templates"

    def __init__(self):

        from vision.detector import Detector

        self.detector = Detector()

    def boxes(self, frame, categorias=None):

        self.deteccoes = self.detector.detect(frame, categorias)

        return [
            {
                "x": d["x"],
                "y": d["y"],
                "width": d["width"],
                "height": d["height"],
            }
            for d in self.deteccoes
        ]


class ProposerSource:
    """
    Caixas por proposta de região (cor + contorno).

    É o caminho para largar os 184 templates. EXPERIMENTAL:
    confira com --debug-proposals antes de ligar --auto.
    """

    nome = "proposer"

    def __init__(self):

        import proposer

        self.proposer = proposer

        self.deteccoes = []

    def boxes(self, frame, categorias=None):
        """
        `categorias` é ignorado: a proposta por cor não sabe
        filtrar por categoria antes de classificar. O filtro por
        estado só faz sentido na fonte de templates.
        """

        self.deteccoes = []

        return [
            {"x": x, "y": y, "width": w, "height": h}
            for x, y, w, h in self.proposer.propose(frame)
        ]


# =========================================================
# OVERLAY
# =========================================================

def draw(frame, deteccoes, estado, stats, candidatos=None):

    saida = frame.copy()

    if candidatos:

        for x, y, w, h in (_box_tuple(c) for c in candidatos):

            cv2.rectangle(
                saida,
                (x, y),
                (x + w, y + h),
                (70, 70, 70),
                2,
            )

    for deteccao in deteccoes:

        x, y = deteccao["x"], deteccao["y"]
        w, h = deteccao["width"], deteccao["height"]

        cor = CORES.get(deteccao["category"], COR_PADRAO)

        cv2.rectangle(saida, (x, y), (x + w, y + h), cor, 3)

        etiqueta = (
            f"{deteccao['category']} "
            f"{deteccao['confidence']:.2f}"
        )

        for espessura, tom in ((5, (0, 0, 0)), (2, cor)):

            cv2.putText(
                saida,
                etiqueta,
                (x, max(y - 8, 20)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                tom,
                espessura,
                cv2.LINE_AA,
            )

    linhas = [f"estado   {estado}"] + [
        f"{chave:<8} {valor}"
        for chave, valor in stats.items()
    ]

    y = 40

    for linha in linhas:

        for espessura, tom in ((5, (0, 0, 0)), (2, (255, 255, 255))):

            cv2.putText(
                saida,
                linha,
                (18, y),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                tom,
                espessura,
                cv2.LINE_AA,
            )

        y += 34

    return saida


def show(saida):

    cv2.imshow(
        WINDOW_NAME,
        cv2.resize(
            saida,
            (WINDOW_WIDTH, WINDOW_HEIGHT),
            interpolation=cv2.INTER_AREA,
        ),
    )

    return cv2.waitKey(1) & 0xFF


# =========================================================
# MODO DEMO
# =========================================================

def demo(model, args):
    """
    Roda sobre imagens já gravadas e COMPARA com os rótulos do
    dataset. Sem device, sem tocar em nada.

    É a forma barata de saber se o modelo presta antes de
    apontá-lo para o jogo.
    """

    from dataset_io import read_index

    registros, _ = read_index(args.dataset_root)

    if not registros:

        print("Nenhuma amostra no dataset.")

        return 1

    registros = registros[: args.demo_limit]

    acertos = 0
    total = 0

    confusao = Counter()

    for registro in registros:

        frame = cv2.imread(str(registro["_path"]))

        if frame is None:
            continue

        caixas = registro.get("boxes") or []

        if not caixas:
            continue

        deteccoes = detect_with_model(
            model,
            frame,
            caixas,
            args.min_confidence,
        )

        # Compara categoria prevista com a do índice, caixa a
        # caixa. É a métrica que importa: se a categoria estiver
        # certa, a ação sai certa da tabela de prioridade.
        previstas = {
            (d["x"], d["y"]): d["category"] for d in deteccoes
        }

        for caixa in caixas:

            verdade = caixa["category"]

            palpite = previstas.get(
                (caixa["x"], caixa["y"]),
                "(nenhuma)",
            )

            total += 1

            if palpite == verdade:
                acertos += 1

            else:
                confusao[(verdade, palpite)] += 1

        if not args.headless:

            saida = draw(
                frame,
                deteccoes,
                registro.get("state", "?"),
                {
                    "acao": registro.get("action") or "negativa",
                    "caixas": len(caixas),
                    "acerto": (
                        f"{acertos}/{total}"
                        f" ({acertos / max(1, total):.0%})"
                    ),
                },
            )

            if show(saida) in (27, ord("q")):
                break

    if not args.headless:
        cv2.destroyAllWindows()

    print()
    print(f"caixas avaliadas ... {total}")

    if total:
        print(f"categoria correta .. {acertos / total:.2%}")

    if confusao:

        print()
        print("maiores confusões:")

        for (verdade, palpite), quantas in confusao.most_common(10):

            print(
                f"  {verdade:<14} previsto como "
                f"{palpite:<14} {quantas:5d}"
            )

    return 0


# =========================================================
# MODO AO VIVO
# =========================================================

def live(model, args):

    from actions.manager import ActionManager
    from capture.screen import ScreenCapture
    from core import devices, log
    from core.config import (
        LOG_LEVEL,
        VISION_FILTER_BY_STATE,
    )
    from core.state_machine import StateMachine

    log.setup(LOG_LEVEL)

    serial = devices.resolver(args.device)

    fonte = (
        TemplateSource()
        if args.source == "templates"
        else ProposerSource()
    )

    captura = ScreenCapture(serial)

    acoes = ActionManager(serial)

    # A StateMachine é quem escolhe a ação, o alvo e o momento.
    # O modelo entra só como fonte de detecção.
    maquina = StateMachine(acoes)

    if not args.auto:

        # Sem --auto nada é tocado. O jeito de garantir isso é
        # substituir a execução, não confiar num if espalhado
        # pelo loop.
        acoes.execute = lambda action, detection=None: True
        acoes.swipe = lambda direction: True

        print(
            "MODO SEGURO: nenhuma ação é enviada ao device. "
            "Use --auto para deixar agir."
        )

    captura.start()

    acoes.start()

    versao = 0

    contagem = Counter()

    concordancia = [0, 0]

    try:

        while True:

            frame, versao, _ts = captura.get_frame(
                since_version=versao,
                timeout=0.2,
            )

            if not captura.is_running():

                print("Stream encerrado.")

                break

            if frame is None:
                continue

            altura, largura = frame.shape[:2]

            # A resolução REAL do frame, não a de referência: o
            # stream pode vir reduzido, e aí o toque precisa da
            # conversão certa.
            acoes.set_frame_size(largura, altura)

            categorias = (
                maquina.wanted_categories()
                if VISION_FILTER_BY_STATE
                and args.source == "templates"
                else None
            )

            inicio = time.monotonic()

            candidatos = fonte.boxes(frame, categorias)

            deteccoes = detect_with_model(
                model,
                frame,
                candidatos,
                args.min_confidence,
            )

            if args.source == "proposer":

                import proposer

                deteccoes = proposer.suppress(deteccoes)

            custo = (time.monotonic() - inicio) * 1000

            # -------------------------------------------
            # Comparação com o professor
            # -------------------------------------------

            if args.compare and getattr(fonte, "deteccoes", None):

                verdade = {
                    (d["x"], d["y"]): d["category"]
                    for d in fonte.deteccoes
                }

                for deteccao in deteccoes:

                    esperado = verdade.get(
                        (deteccao["x"], deteccao["y"])
                    )

                    if esperado is None:
                        continue

                    concordancia[1] += 1

                    if esperado == deteccao["category"]:
                        concordancia[0] += 1

            # -------------------------------------------
            # Decisão
            # -------------------------------------------

            antes = maquina.state

            # lag=0: as detecções são deste frame, calculadas
            # agora. Ao contrário do bot de regras, aqui não há
            # worker assíncrono.
            maquina.update(deteccoes, 0.0)

            if maquina.state != antes:

                contagem[f"{antes}->{maquina.state}"] += 1

            for deteccao in deteccoes:
                contagem[deteccao["category"]] += 1

            if args.headless:
                continue

            stats = {
                "fonte": fonte.nome,
                "cand": len(candidatos),
                "det": len(deteccoes),
                "ms": f"{custo:.0f}",
                "auto": "SIM" if args.auto else "nao",
            }

            if args.compare and concordancia[1]:

                stats["vs prof"] = (
                    f"{concordancia[0] / concordancia[1]:.0%}"
                )

            saida = draw(
                frame,
                deteccoes,
                maquina.state,
                stats,
                candidatos if args.debug_proposals else None,
            )

            if show(saida) in (27, ord("q")):
                break

    except KeyboardInterrupt:

        print("Interrompido.")

    finally:

        if not args.headless:
            cv2.destroyAllWindows()

        acoes.stop()

        captura.stop()

    print()

    if concordancia[1]:

        print(
            f"concordância com o template matching: "
            f"{concordancia[0] / concordancia[1]:.2%} "
            f"({concordancia[0]}/{concordancia[1]} caixas)"
        )

    if contagem:

        print("o que apareceu:")

        for chave, quantas in contagem.most_common(15):
            print(f"  {chave:<24} {quantas}")

    return 0


# =========================================================
# CLI
# =========================================================

def parse_args(argv=None):

    parser = argparse.ArgumentParser(
        description=(
            "Bot com visão por modelo treinado. A ação vem da "
            "StateMachine, não do modelo."
        ),
    )

    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL),
        help="modelo treinado (padrão: IA/model.joblib)",
    )

    parser.add_argument(
        "--source",
        choices=("templates", "proposer"),
        default="templates",
        help=(
            "de onde vêm as caixas candidatas. templates "
            "(padrão): o detector atual diz onde olhar e o "
            "modelo diz o que é — serve para medir o modelo. "
            "proposer: por cor e contorno, EXPERIMENTAL."
        ),
    )

    parser.add_argument(
        "--device",
        default=None,
        help="serial do device; sem isto pergunta",
    )

    parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            "deixa o bot TOCAR no device. Sem isto nada é "
            "enviado."
        ),
    )

    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.60,
        help=(
            "confiança mínima para uma detecção contar "
            "(padrão: 0.60)"
        ),
    )

    parser.add_argument(
        "--compare",
        action="store_true",
        help=(
            "compara a categoria do modelo com a do template "
            "matching, caixa a caixa. Só com "
            "--source templates."
        ),
    )

    parser.add_argument(
        "--debug-proposals",
        action="store_true",
        help="desenha também as caixas candidatas descartadas",
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            "roda sobre imagens do dataset e mede o acerto de "
            "categoria contra os rótulos. Sem device."
        ),
    )

    parser.add_argument(
        "--demo-limit",
        type=int,
        default=50,
        help="quantas amostras no modo demo (padrão: 50)",
    )

    parser.add_argument(
        "--dataset-root",
        default=str(DATASET_ROOT),
        help="pasta do dataset, para o modo demo",
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="sem janela",
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)

    model = Model.load(args.model)

    print(f"modelo .......... {model.path.name}")
    print(f"tipo ............ {model.kind}")
    print(f"classes ......... {', '.join(model.labels)}")

    if model.meta.get("accuracy") is not None:

        print(
            f"acurácia (teste)  "
            f"{model.meta['accuracy']:.2%}"
        )

    for nome, valor in (model.meta.get("baselines") or {}).items():
        print(f"  referência: {nome} {valor:.2%}")

    print()

    if model.kind != "category":

        print(
            "AVISO: este é um modelo de tela inteira "
            "(--kind action).\n"
            "Ele não localiza nada, então não serve para "
            "guiar o clique.\n"
            "Treine o de categoria:\n"
            "  python IA/train_ai.py --kind category\n"
        )

        return 1

    if args.demo:
        return demo(model, args)

    return live(model, args)


if __name__ == "__main__":

    raise SystemExit(main())
