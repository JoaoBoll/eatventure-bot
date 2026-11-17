#!/usr/bin/env python3
"""Bot com visão por modelo treinado."""

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent

for candidato in (str(ROOT), str(ROOT / "src"), str(ROOT / "IA")):
    if candidato not in sys.path:
        sys.path.insert(0, candidato)
from core.cli import ArgumentParser, configure_locale
from core.i18n import display_code, t

from features import (                                # noqa: E402
    FEATURE_SIZE,
    HSV_BINS,
    PATCH_MARGIN,
    PATCH_SIZE,
    box_features,
    screen_features,
)
from model_updates import ModelWatcher, resolve_publication  # noqa: E402

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
    "while_up_food": (180, 90, 255),
    "new_point": (255, 255, 90),
    "gray_max": (160, 160, 160),
    "gray_coin": (120, 120, 120),
    "build": (120, 255, 200),
    "plane": (255, 120, 200),
    "renovate": (90, 255, 255),
    "fly": (200, 255, 90),
    "open_store": (255, 90, 160),
    "delivery": (255, 0, 0),
    "accept_delivery": (0, 100, 255),
    "helper": (180, 0, 255),
    "invite_helper": (0, 180, 80),
}

COR_PADRAO = (160, 0, 200)

# sem `renovate` ou `fly` a StateMachine nunca sai de RENOVATE (ver
# RENOVATE_RULES); `fly` é rara e larga (414x141 vs 95x94 mediano), então sai
# do modelo com confiança baixa e um --min-confidence global a descartaria
CATEGORIAS_CRITICAS = ("renovate", "fly")

# piso por categoria: só ABAIXA o --min-confidence global, nunca exige mais
CONFIANCA_MINIMA_POR_CATEGORIA = {
    "fly": 0.30,
    "renovate": 0.35,
    "plane": 0.40,
}


def min_confidence_for(categoria, global_min):
    # usa o menor dos dois pisos: o específico existe para não perder classe rara
    especifico = CONFIANCA_MINIMA_POR_CATEGORIA.get(categoria)

    if especifico is None:
        return global_min

    return min(global_min, especifico)


def known_categories():
    categories = set(CORES)
    try:
        from core.state_machine import STATE_RULES

    except Exception:
        pass
    else:
        categories.update(
            categoria
            for regras in STATE_RULES.values()
            for categoria, _acao, _proximo in regras
        )

    try:
        from core.config import (
            CATEGORY_COLOR_THRESHOLDS,
            CATEGORY_ROIS,
            CATEGORY_THRESHOLDS,
        )
    except Exception:
        pass
    else:
        categories.update(CATEGORY_COLOR_THRESHOLDS)
        categories.update(CATEGORY_ROIS)
        categories.update(CATEGORY_THRESHOLDS)

    return categories


class Model:
    """Modelo treinado, com o metadado que diz como ele espera a entrada."""

    def __init__(self, clf, meta, path):

        self.clf = clf
        self.meta = meta
        self.path = path

        # `label_mode` é nome antigo de `kind`: sem ler os dois, um modelo
        # de ação gravado pela versão anterior cai no default "category" e
        # o bot trava sem erro nenhum no log
        self.kind = (
            meta.get("kind")
            or meta.get("label_mode")
            or "category"
        )

        self.labels = [str(c) for c in getattr(clf, "classes_", [])]

        if not self.labels:
            self.labels = [
                str(x) for x in meta.get("labels", [])
            ]

        if not self.labels:

            raise SystemExit(
                t("messages.model_classes_missing", path=path)
            )

    @classmethod
    def load(cls, path):
        return cls.load_publication(resolve_publication(path))

    @classmethod
    def load_publication(cls, publication):

        caminho = publication.model_path

        if not caminho.exists():

            raise SystemExit(
                t("messages.model_training_required", caminho=caminho)
            )

        meta_path = caminho.with_suffix(".meta.json")

        meta = {}

        if meta_path.exists():

            try:
                meta = json.loads(
                    meta_path.read_text(encoding="utf-8")
                )

            except json.JSONDecodeError:

                if publication.run_id is not None:
                    raise ValueError(t("messages.metadata_unreadable", meta_path=meta_path)) from None

                print(
                    t("messages.metadata_fallback_warning", value_1=meta_path.name)
                )

        if not isinstance(meta, dict):
            raise ValueError(t("messages.invalid_metadata", meta_path=meta_path))

        if publication.run_id is not None:
            if meta.get("run_id") != publication.run_id:
                raise ValueError(t("messages.metadata_version_mismatch", value_1=publication.run_id))
            if meta.get("kind") != "category":
                raise ValueError(t("messages.publication_category_required"))
            feature_metadata = meta.get("features")
            expected_features = {
                "patch_size": PATCH_SIZE,
                "patch_margin": PATCH_MARGIN,
                "hsv_bins": HSV_BINS,
                "n_features": FEATURE_SIZE,
            }
            if not isinstance(feature_metadata, dict) or any(
                feature_metadata.get(name) != expected
                for name, expected in expected_features.items()
            ):
                raise ValueError(t("messages.published_features_incompatible"))

        import joblib

        clf = joblib.load(caminho)

        if publication.run_id is not None and getattr(clf, "n_features_in_", None) is None:
            raise ValueError(t("messages.published_features_missing"))

        modelo = cls(clf, meta, caminho)

        modelo._check_features()

        modelo._check_labels()

        if publication.run_id is not None:
            categories = known_categories()
            labels = set(modelo.labels)
            if not labels.intersection(categories):
                raise ValueError(t("messages.published_categories_missing"))

        return modelo

    def _check_features(self):
        # sem isto, PATCH_SIZE mudar e rodar um modelo antigo dá erro obscuro
        # de shape no loop — ou, pior, passa e prevê lixo
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
                t("messages.model_features_incompatible", value_1=self.path.name, esperado=esperado, atual=atual, value_4=self.kind)
            )

    def _check_labels(self):
        # metadado pode estar ausente/desatualizado; as classes, não — se
        # nenhuma é categoria conhecida o bot fica parado, sem exceção
        if self.kind != "category":
            return

        conhecidas = known_categories()

        reconhecidas = [
            r
            for r in self.labels
            if r in conhecidas or r == "background"
        ]

        if reconhecidas:
            return

        raise SystemExit(
            t("messages.model_classes_incompatible", value_1=self.path.name, value_2=', '.join(self.labels[:12]))
        )

    def classify_batch(self, vetores):
        # em lote de propósito: uma chamada por recorte pagaria overhead de
        # sklearn por candidato, e são dezenas por quadro
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


def detect_with_model(model, frame, caixas, min_confidence):
    # formato tem de ser o mesmo de vision/detector.py (category, name,
    # confidence, color_similarity, x, y, width, height) para a StateMachine ler
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

        # classe de recorte sem objeto, criada no treino
        if categoria == "background":
            continue

        if confianca < min_confidence_for(categoria, min_confidence):
            continue

        x, y, w, h = _box_tuple(caixa)

        deteccoes.append(
            {
                "category": categoria,
                "name": f"modelo:{categoria}",
                "confidence": confianca,

                # StateMachine não usa, mas overlay e testes do detector esperam a chave
                "color_similarity": confianca,

                "x": x,
                "y": y,
                "width": w,
                "height": h,
            }
        )

    # maior confiança primeiro: StateMachine pega a primeira detecção de cada categoria
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


class TemplateSource:
    """Caixas do detector de template: ele diz onde olhar, o modelo diz o quê.
    Serve para medir o modelo contra o professor (`--compare`) sem depender do proposer."""

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
    """Caixas por proposta de região (cor + contorno), para largar os
    templates. EXPERIMENTAL: confira com --debug-proposals antes de ligar --auto."""

    nome = "proposer"

    def __init__(self):

        import proposer

        self.proposer = proposer

        self.deteccoes = []

    def boxes(self, frame, categorias=None):
        # categorias ignorado: proposta por cor não filtra por categoria antes de classificar
        self.deteccoes = []

        return [
            {"x": x, "y": y, "width": w, "height": h}
            for x, y, w, h in self.proposer.propose(frame)
        ]


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


def demo(model, args):
    """Roda sobre imagens do dataset e compara com os rótulos, sem device — forma
    barata de saber se o modelo presta antes de apontá-lo para o jogo."""

    from dataset_io import read_index

    registros, _ = read_index(args.dataset_root)

    if not registros:

        print(t("messages.dataset_empty"))

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

        # se a categoria está certa, a ação sai certa da tabela de prioridade
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
    print(t("messages.evaluated_boxes", total=total))

    if total:
        print(t("messages.category_accuracy", value_1=acertos / total))

    if confusao:

        print()
        print(t("messages.top_confusions"))

        for (verdade, palpite), quantas in confusao.most_common(10):

            print(
                t("messages.predicted_category", verdade=verdade, palpite=palpite, quantas=quantas)
            )

    return 0


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
    logger = log.get("bot_ai")

    serial = devices.resolver(args.device)

    fonte = (
        TemplateSource()
        if args.source == "templates"
        else ProposerSource()
    )

    captura = ScreenCapture(serial)

    acoes = ActionManager(serial)

    # StateMachine escolhe ação/alvo/momento; o modelo só entra como detecção
    maquina = StateMachine(acoes)

    # importado uma vez, fora do loop: import por quadro paga lookup ~30x/s sem motivo
    modulo_proposer = None

    if args.source == "proposer":

        import proposer as modulo_proposer

    if not args.auto:

        # garante --auto substituindo a execução, não confiando num if espalhado pelo loop
        acoes.execute = lambda action, detection=None: True
        acoes.swipe = lambda direction: True

        print(
            t("messages.safe_mode")
        )

    versao = 0

    contagem = Counter()

    concordancia = [0, 0]

    watcher = ModelWatcher(
        args.model,
        model,
        Model.load_publication,
        logger=logger,
    )

    try:

        captura.start()
        acoes.start()
        watcher.start()

        while True:

            frame, versao, capturado_em = captura.get_frame(
                since_version=versao,
                timeout=0.2,
            )

            if not captura.is_running():

                print(t("messages.stream_ended"))

                break

            if frame is None:
                continue

            updated_model = watcher.take_pending()
            if updated_model is not None:
                model = updated_model
                logger.info(t("messages.model_version_updated"), model.meta["run_id"])

            altura, largura = frame.shape[:2]

            # resolução real do frame, não a de referência: stream pode vir reduzido
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

            if modulo_proposer is not None:
                deteccoes = modulo_proposer.suppress(deteccoes)

            custo = (time.monotonic() - inicio) * 1000

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

            antes = maquina.state

            # lag real, não 0.0: sem ele, _can_act acha que a tela em mão já
            # reflete a última ação (duplo toque) e MAX_DETECTION_AGE nunca
            # dispara para denunciar um detector lento
            lag = (
                max(0.0, time.monotonic() - capturado_em)
                if capturado_em
                else 0.0
            )

            maquina.update(deteccoes, lag)

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

                # lag + tempo no estado: diz se "travou" é detector lento ou estado sem saída
                "lag": f"{lag * 1000:.0f}",
                "no est": f"{time.monotonic() - maquina.state_entered:.0f}s",
                "auto": "SIM" if args.auto else "nao",
                "modelo": model.meta.get("run_id") or model.path.name,
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

        watcher.stop()

        if not args.headless:
            cv2.destroyAllWindows()

        acoes.stop()

        captura.stop()

    print()

    if concordancia[1]:

        print(
            t("messages.template_agreement", value_1=concordancia[0] / concordancia[1], value_2=concordancia[0], value_3=concordancia[1])
        )

    if contagem:

        print(t("messages.observed_categories"))

        for chave, quantas in contagem.most_common(15):
            print(f"  {chave:<24} {quantas}")

    # fora do most_common de propósito: `fly` é raro e cairia do corte de 15
    print()
    print(t("messages.renovate_categories"))

    for categoria in CATEGORIAS_CRITICAS:

        print(f"  {categoria:<24} {contagem.get(categoria, 0)}")

    return 0


def parse_args(argv=None):
    configure_locale(argv)

    parser = ArgumentParser(
        description=(
            t("messages.ai_bot_description")
        ),
    )

    parser.add_argument(
        "--model",
        default=str(DEFAULT_MODEL),
        help=t("messages.model_help"),
    )

    parser.add_argument(
        "--source",
        choices=("templates", "proposer"),
        default="templates",
        help=(
            t("messages.source_help")
        ),
    )

    parser.add_argument(
        "--device",
        default=None,
        help=t("messages.device_help"),
    )

    parser.add_argument(
        "--auto",
        action="store_true",
        help=(
            t("messages.allow_actions_help")
        ),
    )

    parser.add_argument(
        "--min-confidence",
        type=float,
        default=0.60,
        help=(
            t("messages.confidence_help")
        ),
    )

    parser.add_argument(
        "--compare",
        action="store_true",
        help=(
            t("messages.compare_help")
        ),
    )

    parser.add_argument(
        "--debug-proposals",
        action="store_true",
        help=t("messages.debug_proposals_help"),
    )

    parser.add_argument(
        "--demo",
        action="store_true",
        help=(
            t("messages.demo_help")
        ),
    )

    parser.add_argument(
        "--demo-limit",
        type=int,
        default=50,
        help=t("messages.demo_limit_help"),
    )

    parser.add_argument(
        "--dataset-root",
        default=str(DATASET_ROOT),
        help=t("messages.demo_dataset_help"),
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help=t("messages.headless_help"),
    )

    return parser.parse_args(argv)


def main(argv=None):

    args = parse_args(argv)

    model = Model.load(args.model)

    print(t("messages.model_name", value_1=model.path.name))
    print(t("messages.model_kind", value_1=model.kind))
    print(t("messages.model_classes", value_1=', '.join(model.labels)))

    if model.meta.get("accuracy") is not None:

        print(
            t("messages.test_accuracy", value_1=model.meta['accuracy'])
        )

    for nome, valor in (model.meta.get("baselines") or {}).items():
            print(t("messages.baseline_accuracy", nome=display_code("baselines", nome), valor=valor))

    print()

    if model.kind != "category":

        print(
            t("messages.full_screen_model_warning")
        )

        return 1

    # sem `fly`/`renovate` nas classes o bot nunca sai de RENOVATE (as duas
    # únicas regras de RENOVATE_RULES) sem dar erro nenhum — só fica parado
    ausentes = [
        c for c in CATEGORIAS_CRITICAS if c not in model.labels
    ]

    if ausentes:

        print(
            t("messages.missing_critical_categories", value_1=', '.join(ausentes))
        )

    if args.demo:
        return demo(model, args)

    return live(model, args)


if __name__ == "__main__":

    raise SystemExit(main())
