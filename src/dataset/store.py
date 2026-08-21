"""
Índice do dataset no PostgreSQL.

O que vai para o banco: METADADO. As imagens ficam em arquivo.

Guardar pixels como BLOB parece organizado e é ruim na prática:
o treino leria gigabytes por época através do driver, e o dataset
deixaria de ser copiável com um `rsync`. O banco ganha o lugar
dele como ÍNDICE consultável:

    -- todo open_box que funcionou, com boa confiança
    SELECT image FROM dataset_sample
     WHERE action = 'open_box'
       AND outcome = 'changed'
       AND target_confidence > 0.9;

O treino funciona SEM banco: o samples.jsonl é a fonte de
verdade. O banco é conveniência de consulta, e por isso nenhuma
falha dele derruba a gravação.

O DDL está em docs/dataset.md.
"""

import json

from core import log

logger = log.get("dataset.db")


class PostgresStore:
    """
    Inserção em lote, com reconexão.

    O lote existe porque uma ida ao banco por amostra colocaria
    latência de rede na thread de gravação — e ela também
    codifica PNG.
    """

    def __init__(self, dsn, batch_size=20, schema="public"):

        self.dsn = dsn
        self.batch_size = max(1, batch_size)
        self.schema = schema

        self.connection = None

        self.buffer = []

        # Sessão registrada só uma vez, na primeira amostra.
        self.session_registered = set()

    # =====================================================
    # CONEXÃO
    # =====================================================

    def connect(self):
        """
        Abre a conexão. Erro aqui é do chamador — é o momento
        de descobrir DSN errado, não no meio da sessão.
        """

        # Import tardio: quem não usa banco não precisa do
        # psycopg instalado.
        try:

            import psycopg

        except ImportError as error:

            raise RuntimeError(
                "psycopg não instalado. "
                "Rode: pip install 'psycopg[binary]'"
            ) from error

        self.connection = psycopg.connect(self.dsn)

        self.connection.autocommit = False

        logger.info("Conectado ao Postgres para indexar o dataset.")

        return self

    def close(self):

        try:

            self.flush()

        except Exception as error:

            logger.warning(
                "Não conseguiu gravar o último lote: %s",
                error,
            )

        if self.connection:

            try:
                self.connection.close()

            except Exception:
                pass

            self.connection = None

    # =====================================================
    # INSERÇÃO
    # =====================================================

    def insert(self, registro):

        self.buffer.append(registro)

        if len(self.buffer) >= self.batch_size:

            self.flush()

    def flush(self):

        if not self.buffer:
            return

        if self.connection is None:

            self.connect()

        lote = self.buffer

        self.buffer = []

        tabela_sessao = f"{self.schema}.dataset_session"
        tabela_amostra = f"{self.schema}.dataset_sample"
        tabela_caixa = f"{self.schema}.dataset_box"

        try:

            with self.connection.cursor() as cursor:

                # -----------------------------------------
                # SESSÃO
                # -----------------------------------------

                for sessao in {r["session"] for r in lote}:

                    if sessao in self.session_registered:
                        continue

                    cursor.execute(
                        f"INSERT INTO {tabela_sessao} "
                        "(id, started_at) VALUES (%s, %s) "
                        "ON CONFLICT (id) DO NOTHING",
                        (sessao, lote[0]["created_at"]),
                    )

                    self.session_registered.add(sessao)

                # -----------------------------------------
                # AMOSTRAS
                # -----------------------------------------

                cursor.executemany(
                    f"""
                    INSERT INTO {tabela_amostra} (
                        id, session_id, created_at,
                        image, image_sha256, phash,
                        frame_width, frame_height,
                        state, action, action_kind,
                        click_x, click_y,
                        target_category, target_template,
                        target_confidence,
                        detect_lag_ms,
                        outcome, outcome_after_ms,
                        cycle_count, box_count
                    ) VALUES (
                        %s, %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s, %s,
                        %s, %s,
                        %s, %s,
                        %s,
                        %s,
                        %s, %s,
                        %s, %s
                    )
                    ON CONFLICT (id) DO NOTHING
                    """,
                    [
                        (
                            r["id"],
                            r["session"],
                            r["created_at"],
                            r["image"],
                            r["image_sha256"],
                            r["phash"],
                            r["frame_width"],
                            r["frame_height"],
                            r["state"],
                            r["action"],
                            r["action_kind"],
                            r["click_x"],
                            r["click_y"],
                            r["target_category"],
                            r["target_template"],
                            r["target_confidence"],
                            r["detect_lag_ms"],
                            r["outcome"],
                            r["outcome_after_ms"],
                            r["cycle_count"],
                            len(r["boxes"]),
                        )
                        for r in lote
                    ],
                )

                # -----------------------------------------
                # CAIXAS
                # -----------------------------------------

                caixas = [
                    (
                        r["id"],
                        b["category"],
                        b["template"],
                        b["confidence"],
                        b["color_similarity"],
                        b["x"],
                        b["y"],
                        b["width"],
                        b["height"],
                        b["acted"],
                    )
                    for r in lote
                    for b in r["boxes"]
                ]

                if caixas:

                    cursor.executemany(
                        f"""
                        INSERT INTO {tabela_caixa} (
                            sample_id, category, template,
                            confidence, color_similarity,
                            x, y, width, height, acted
                        ) VALUES (
                            %s, %s, %s,
                            %s, %s,
                            %s, %s, %s, %s, %s
                        )
                        """,
                        caixas,
                    )

            self.connection.commit()

        except Exception:

            # Sem rollback a conexão fica inutilizável para
            # todo lote seguinte.
            try:
                self.connection.rollback()

            except Exception:
                self.connection = None

            raise


# =========================================================
# IMPORTAÇÃO DE UM JSONL EXISTENTE
# =========================================================

def importar_jsonl(caminho, dsn, batch_size=200, schema="public"):
    """
    Carrega no banco um samples.jsonl já gravado.

    É o caminho normal de uso: o bot grava arquivos, e o banco
    entra depois — inclusive para sessões antigas, gravadas
    antes de existir banco.
    """

    store = PostgresStore(
        dsn,
        batch_size=batch_size,
        schema=schema,
    )

    store.connect()

    total = 0

    try:

        with open(caminho, "r", encoding="utf-8") as arquivo:

            for linha in arquivo:

                linha = linha.strip()

                if not linha:
                    continue

                store.insert(json.loads(linha))

                total += 1

        store.flush()

    finally:

        store.close()

    return total
