"""
Testes da compactação de numeração dos templates.

    python tests/test_renumerar.py

Roda em pastas temporárias — nunca toca em
src/vision/templates.

Renomear arquivo é irreversível, então a ordem das operações
importa: se o destino de um arquivo for o nome de outro que
ainda não foi processado, um sobrescreve o outro e o template
some.
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "tools"))

from renumerar import (                          # noqa: E402
    numerados,
    planejar,
    proximo_numero,
    renumerar,
)


# =========================================================
# HELPERS
# =========================================================

def monta(numeros, extras=()):
    """
    Cria uma pasta temporária com item_NNN.png nos números
    dados. O conteúdo é o próprio número, para dar de rastrear
    quem virou quem.
    """

    pasta = Path(tempfile.mkdtemp())

    for numero in numeros:

        caminho = pasta / f"item_{numero:03d}.png"

        caminho.write_text(str(numero), encoding="utf-8")

    for nome in extras:

        (pasta / nome).write_text("x", encoding="utf-8")

    return pasta


def sequencia(pasta):

    return [n for n, _ in numerados(pasta)]


def conteudos(pasta):
    """
    {numero_do_arquivo: conteudo_original}
    """

    return {
        n: p.read_text(encoding="utf-8")
        for n, p in numerados(pasta)
    }


# =========================================================
# TESTES
# =========================================================

def test_fecha_lacuna_no_meio():
    """
    O caso pedido: falta o 3 entre 2 e 4, então 4 vira 3 e
    5 vira 4.
    """

    pasta = monta([1, 2, 4, 5])

    renumerar(pasta, aplicar=True, log=lambda *a: None)

    assert sequencia(pasta) == [1, 2, 3, 4], sequencia(pasta)

    # E o conteúdo acompanhou: o que era 4 agora é 3.
    assert conteudos(pasta) == {
        1: "1",
        2: "2",
        3: "4",
        4: "5",
    }, conteudos(pasta)


def test_varias_lacunas():

    pasta = monta([1, 2, 3, 4, 6, 7, 10])

    renumerar(pasta, aplicar=True, log=lambda *a: None)

    assert sequencia(pasta) == [1, 2, 3, 4, 5, 6, 7]

    assert conteudos(pasta)[5] == "6"
    assert conteudos(pasta)[7] == "10"


def test_nao_perde_arquivo():
    """
    A garantia que importa: a quantidade de arquivos não muda.
    Se a ordem de renomeio estivesse errada, um sobrescreveria
    o outro e o template desapareceria em silêncio.
    """

    pasta = monta([2, 5, 9, 12, 13, 20])

    antes = set(conteudos(pasta).values())

    renumerar(pasta, aplicar=True, log=lambda *a: None)

    depois = set(conteudos(pasta).values())

    assert antes == depois, (antes, depois)

    assert sequencia(pasta) == [1, 2, 3, 4, 5, 6]


def test_ja_compacto_nao_mexe():

    pasta = monta([1, 2, 3])

    assert planejar(pasta) == []

    renumerar(pasta, aplicar=True, log=lambda *a: None)

    assert conteudos(pasta) == {1: "1", 2: "2", 3: "3"}


def test_sem_aplicar_nao_renomeia():
    """
    O padrão do CLI é só mostrar.
    """

    pasta = monta([1, 5, 9])

    mudancas = renumerar(pasta, aplicar=False)

    assert len(mudancas) == 2, mudancas

    # Nada mudou no disco.
    assert sequencia(pasta) == [1, 5, 9], sequencia(pasta)


def test_ignora_nome_fora_do_padrao():

    pasta = monta(
        [1, 4],
        extras=("referencia.png", "item_abc.png"),
    )

    renumerar(pasta, aplicar=True, log=lambda *a: None)

    assert sequencia(pasta) == [1, 2]

    # Os intrusos continuam lá, intactos.
    assert (pasta / "referencia.png").exists()
    assert (pasta / "item_abc.png").exists()


def test_ordem_crescente_nunca_gera_colisao():
    """
    A ordem crescente torna colisão impossível: o alvo de cada
    arquivo é <= o número dele, e quem ainda não foi processado
    tem número MAIOR que a origem. Então o alvo está sempre
    livre.

    Aqui isso é verificado por força bruta, em vez de por
    argumento.
    """

    import itertools

    for tamanho in range(1, 6):

        for numeros in itertools.combinations(range(1, 12), tamanho):

            pasta = monta(list(numeros))

            for origem, destino in planejar(pasta):

                # No momento de renomear, o destino nunca pode
                # ser um arquivo que ainda não foi processado.
                assert not destino.exists() or destino == origem, (
                    numeros,
                    origem.name,
                    destino.name,
                )

                origem.rename(destino)

            assert sequencia(pasta) == list(
                range(1, tamanho + 1)
            ), (numeros, sequencia(pasta))


def test_guarda_de_sobrescrita(monkeypatch=None):
    """
    O guarda defensivo: se um plano chegasse com destino
    ocupado, pula e avisa em vez de destruir o arquivo.

    Colisão não acontece pelo caminho normal (ver teste acima),
    então o plano é injetado à mão — um ramo defensivo que nunca
    roda pode estar quebrado sem ninguém saber.
    """

    import renumerar as mod

    pasta = monta([1, 2])

    original = mod.planejar

    # Plano inválido de propósito: manda o 2 virar o 1.
    mod.planejar = lambda d: [
        (d / "item_002.png", d / "item_001.png")
    ]

    avisos = []

    try:

        feitas = mod.renumerar(
            pasta,
            aplicar=True,
            log=avisos.append,
        )

    finally:

        mod.planejar = original

    assert feitas == [], feitas

    assert avisos, "deveria ter avisado"

    # Os dois arquivos sobreviveram, com o conteúdo original.
    assert conteudos(pasta) == {1: "1", 2: "2"}, conteudos(pasta)


def test_proximo_numero_depois_de_compactar():

    pasta = monta([1, 2, 4, 5])

    renumerar(pasta, aplicar=True, log=lambda *a: None)

    # 4 arquivos compactos -> o novo é o 5.
    assert proximo_numero(pasta) == 5, proximo_numero(pasta)


def test_pasta_vazia():

    pasta = monta([])

    assert planejar(pasta) == []

    assert proximo_numero(pasta) == 1


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
