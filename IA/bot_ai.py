#!/usr/bin/env python3
"""Bot com visão por modelo treinado."""

import argparse
import json
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import cv2
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
    # tiradas das tabelas de regras, não de lista copiada: categoria nova
    # em core/state_machine.py passa a valer sozinha
    try:
        from core.state_machine import STATE_RULES

    except Exception:
        # core não importável (nem device, nem config): sobra a lista do overlay
        return set(CORES)

    return {
        categoria
        for regras in STATE_RULES.values()
        for categoria, _acao, _proximo in regras
    }


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
                f"Modelo sem classes: {path}\n"
                "Treine de novo com IA/train_ai.py."
            )

    @classmethod
    def load(cls, path):

        import joblib

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

        modelo._check_labels()

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
                f"Modelo incompatível: {self.path.name} espera "
                f"{esperado} features, mas IA/features.py produz "
                f"{atual}.\n\n"
                "IA/features.py mudou depois do treino. "
                "Retreine:\n"
                f"  python IA/train_ai.py --kind {self.kind}\n"
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
            f"As classes de {self.path.name} não são categorias "
            "de visão:\n"
            f"  {', '.join(self.labels[:12])}\n\n"
            "Isso é um modelo de AÇÃO rodando como se fosse de "
            "categoria. Nenhuma previsão casa com as regras da "
            "StateMachine, então o bot não age — parece "
            "travado, sem erro no log.\n\n"
            "Retreine:\n"
            "  python IA/train_ai.py --kind category\n"
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


class ModelVisionWorker:
    """Processa o frame mais recente fora da thread da janela/StateMachine."""

    def __init__(self, model, fonte, min_confidence, proposer=None):
        self.model = model
        self.fonte = fonte
        self.min_confidence = min_confidence
        self.proposer = proposer
        self.condition = threading.Condition()
        self.running = False
        self.thread = None
        self.pending = None
        self.result = None
        self.last_error = None

    def start(self):
        with self.condition:
            self.running = True
        self.thread = threading.Thread(
            target=self._run, name="model-vision", daemon=True,
        )
        self.thread.start()

    def stop(self):
        with self.condition:
            self.running = False
            self.condition.notify_all()
        if self.thread is not None:
            self.thread.join(timeout=5)
            self.thread = None

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def submit(self, frame, version, timestamp, categories=None):
        # Uma única vaga: durante uma inferência, frames antigos são substituídos.
        with self.condition:
            self.pending = (frame, version, timestamp, categories)
            self.condition.notify()

    def latest(self):
        with self.condition:
            return self.result

    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(
                    lambda: self.pending is not None or not self.running
                )
                if not self.running:
                    return
                frame, version, timestamp, categories = self.pending
                self.pending = None

            try:
                inicio = time.monotonic()
                candidatos = self.fonte.boxes(frame, categories)
                deteccoes = detect_with_model(
                    self.model, frame, candidatos, self.min_confidence,
                )
                if self.proposer is not None:
                    deteccoes = self.proposer.suppress(deteccoes)

                # TemplateSource guarda a verdade da mesma passada; copie-a
                # junto com o resultado para não misturar frames.
                referencia = tuple(getattr(self.fonte, "deteccoes", ()))
                resultado = (frame, version, timestamp, candidatos,
                             deteccoes, referencia,
                             (time.monotonic() - inicio) * 1000)
                with self.condition:
                    self.result = resultado
                    self.last_error = None
            except Exception as error:
                with self.condition:
                    self.last_error = error
                return


def demo(model, args):
    """Roda sobre imagens do dataset e compara com os rótulos, sem device — forma
    barata de saber se o modelo presta antes de apontá-lo para o jogo."""

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


def live(model, args):
    from actions.manager import ActionManager
    from capture.screen import ScreenCapture
    from core import devices, log
    from core.config import LOG_LEVEL, VISION_FILTER_BY_STATE
    from core.state_machine import StateMachine

    log.setup(LOG_LEVEL)
    serial = devices.resolver(args.device)
    fonte = TemplateSource() if args.source == "templates" else ProposerSource()
    captura = ScreenCapture(serial)
    acoes = ActionManager(serial)
    maquina = StateMachine(acoes)

    modulo_proposer = None
    if args.source == "proposer":
        import proposer as modulo_proposer

    if not args.auto:
        acoes.execute = lambda action, detection=None: True
        acoes.swipe = lambda direction: True
        print("MODO SEGURO: nenhuma ação é enviada ao device. Use --auto para deixar agir.")

    visao = ModelVisionWorker(model, fonte, args.min_confidence, modulo_proposer)
    contagem = Counter()
    concordancia = [0, 0]
    versao = 0
    versao_analisada = 0
    resultado_atual = None
    ultimo_desenho = 0.0

    try:
        captura.start()
        acoes.start()
        visao.start()

        while True:
            # A janela fica na thread principal, mesmo com inferência demorada.
            frame, nova_versao, capturado_em = captura.get_frame(
                since_version=versao, timeout=0.05,
            )
            if not captura.is_running():
                print("Stream encerrado.")
                break
            if not visao.is_alive():
                raise RuntimeError(f"Thread da visão encerrada: {visao.last_error!r}")

            if frame is not None and nova_versao != versao:
                versao = nova_versao
                categorias = (
                    maquina.wanted_categories()
                    if VISION_FILTER_BY_STATE and args.source == "templates"
                    else None
                )
                visao.submit(frame, versao, capturado_em, categorias)

            resultado = visao.latest()
            if resultado is not None and resultado[1] != versao_analisada:
                (frame_analisado, versao_analisada, tempo_analisado,
                 candidatos, deteccoes, referencia, custo) = resultado
                resultado_atual = resultado
                altura, largura = frame_analisado.shape[:2]
                acoes.set_frame_size(largura, altura)

                if args.compare and referencia:
                    verdade = {
                        (d["x"], d["y"]): d["category"] for d in referencia
                    }
                    for deteccao in deteccoes:
                        esperado = verdade.get((deteccao["x"], deteccao["y"]))
                        if esperado is not None:
                            concordancia[1] += 1
                            if esperado == deteccao["category"]:
                                concordancia[0] += 1

                antes = maquina.state
                lag = (
                    max(0.0, time.monotonic() - tempo_analisado)
                    if tempo_analisado else 0.0
                )
                maquina.update(deteccoes, lag)
                if maquina.state != antes:
                    contagem[f"{antes}->{maquina.state}"] += 1
                for deteccao in deteccoes:
                    contagem[deteccao["category"]] += 1

            if args.headless:
                continue

            # Desenhar o frame inteiro a cada captura concorreria com o modelo.
            # O waitKey continua atendendo ESC/q entre desenhos.
            if time.monotonic() - ultimo_desenho < 0.1:
                if cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
                continue
            ultimo_desenho = time.monotonic()

            if resultado_atual is None:
                # A janela aceita ESC/q antes da primeira inferência terminar.
                if frame is not None and show(frame) in (27, ord("q")):
                    break
                if frame is None and cv2.waitKey(1) & 0xFF in (27, ord("q")):
                    break
                continue

            (frame_analisado, _, tempo_analisado, candidatos,
             deteccoes, _, custo) = resultado_atual
            lag = (
                max(0.0, time.monotonic() - tempo_analisado)
                if tempo_analisado else 0.0
            )
            stats = {
                "fonte": fonte.nome,
                "cand": len(candidatos),
                "det": len(deteccoes),
                "ms": f"{custo:.0f}",
                "lag": f"{lag * 1000:.0f}",
                "no est": f"{time.monotonic() - maquina.state_entered:.0f}s",
                "auto": "SIM" if args.auto else "nao",
            }
            if args.compare and concordancia[1]:
                stats["vs prof"] = f"{concordancia[0] / concordancia[1]:.0%}"
            # A imagem exibida acompanha a captura; o lag informa a idade
            # das caixas. A decisão sempre usa o frame analisado acima.
            saida = draw(
                frame if frame is not None else frame_analisado,
                deteccoes, maquina.state, stats,
                candidatos if args.debug_proposals else None,
            )
            if show(saida) in (27, ord("q")):
                break

    except KeyboardInterrupt:
        print("Interrompido.")
    finally:
        if not args.headless:
            cv2.destroyAllWindows()
        visao.stop()
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

    # fora do most_common de propósito: `fly` é raro e cairia do corte de 15
    print()
    print("categorias que fecham RENOVATE:")

    for categoria in CATEGORIAS_CRITICAS:

        print(f"  {categoria:<24} {contagem.get(categoria, 0)}")

    return 0


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

    # sem `fly`/`renovate` nas classes o bot nunca sai de RENOVATE (as duas
    # únicas regras de RENOVATE_RULES) sem dar erro nenhum — só fica parado
    ausentes = [
        c for c in CATEGORIAS_CRITICAS if c not in model.labels
    ]

    if ausentes:

        print(
            f"AVISO: o modelo não conhece {', '.join(ausentes)}.\n"
            "Essas são as únicas categorias que fecham o estado "
            "RENOVATE, então o bot vai travar na tela de "
            "reforma.\n"
            "Grave dataset passando por uma reforma e retreine:\n"
            "  python IA/train_ai.py --kind category\n"
        )

    if args.demo:
        return demo(model, args)

    return live(model, args)


if __name__ == "__main__":

    raise SystemExit(main())
