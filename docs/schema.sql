-- ========================================================
-- EatVenture AI — esquema do dataset de treino
-- ========================================================
--
-- Rode UMA vez, no banco `eatventure`:
--
--     psql -h 192.168.1.100 -U admin -d eatventure -f docs/schema.sql
--
-- ou cole no pgAdmin / DBeaver com o banco `eatventure` aberto.
--
-- É seguro rodar de novo: tudo usa IF NOT EXISTS.
--
-- --------------------------------------------------------
-- O que vai aqui, e o que NÃO vai
-- --------------------------------------------------------
--
-- Vai: METADADO. Não vai: PIXEL.
--
-- As imagens ficam em arquivo (<DATASET_DIR>/images/), e a
-- coluna `image` guarda o caminho RELATIVO à raiz do dataset.
-- Guardar imagem como BLOB parece organizado e é ruim na
-- prática: o treino leria gigabytes por época através do
-- driver, e o dataset deixaria de ser copiável com um rsync.
--
-- O treino funciona SEM este banco — o samples.jsonl é a fonte
-- de verdade. Isto aqui é índice consultável.
-- ========================================================


-- --------------------------------------------------------
-- 1. SESSÃO — uma execução do bot
-- --------------------------------------------------------

CREATE TABLE IF NOT EXISTS dataset_session (
    id          TEXT PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL,
    note        TEXT
);

COMMENT ON TABLE dataset_session IS
    'Uma execução do bot. O id vem do recorder (uuid hex).';


-- --------------------------------------------------------
-- 2. AMOSTRA — um frame + o que o bot decidiu sobre ele
-- --------------------------------------------------------

CREATE TABLE IF NOT EXISTS dataset_sample (
    id                TEXT PRIMARY KEY,

    session_id        TEXT NOT NULL
                      REFERENCES dataset_session(id),

    created_at        TIMESTAMPTZ NOT NULL,

    -- Caminho RELATIVO à raiz do dataset. Mover a pasta não
    -- invalida o índice.
    image             TEXT NOT NULL,

    -- Identidade exata do arquivo: detecta duplicata entre
    -- sessões e confere integridade.
    image_sha256      TEXT NOT NULL,

    -- Hash de SIMILARIDADE (não criptográfico): telas quase
    -- iguais colidem de propósito, para achar quase-duplicatas.
    phash             TEXT,

    -- Resolução do frame gravado. As coordenadas abaixo estão
    -- NESTE espaço, então sem isto elas não têm significado.
    frame_width       INTEGER NOT NULL,
    frame_height      INTEGER NOT NULL,

    -- Estado da máquina no momento da decisão.
    state             TEXT,

    -- NULL nas amostras NEGATIVAS (tela sem alvo nenhum).
    action            TEXT,
    action_kind       TEXT,

    -- Ponto tocado, em pixel do FRAME. NULL quando a ação não
    -- tem alvo pontual (scroll) ou na negativa.
    click_x           INTEGER,
    click_y           INTEGER,

    -- A detecção que virou ação.
    target_category   TEXT,
    target_template   TEXT,
    target_confidence DOUBLE PRECISION,

    -- Idade do frame quando a decisão foi tomada. Amostra com
    -- atraso alto é decisão sobre tela velha.
    detect_lag_ms     INTEGER,

    -- 'changed'   o alvo saiu da tela: a ação funcionou
    -- 'unchanged' o alvo continua lá: não funcionou
    -- 'unknown'   não chegou frame para julgar
    -- NULL        amostra negativa
    --
    -- É esta coluna que permite treinar só no que funcionou,
    -- em vez de herdar todo erro do template matcher.
    outcome           TEXT,
    outcome_after_ms  INTEGER,

    cycle_count       INTEGER,

    -- Desnormalizado de propósito: filtrar "quantos alvos na
    -- tela" sem join é a consulta mais comum na inspeção.
    box_count         INTEGER NOT NULL DEFAULT 0
);

COMMENT ON COLUMN dataset_sample.image IS
    'Caminho relativo à raiz do dataset, não absoluto.';

COMMENT ON COLUMN dataset_sample.click_x IS
    'Pixel do FRAME (ver frame_width/frame_height), não do device.';

COMMENT ON COLUMN dataset_sample.outcome IS
    'changed | unchanged | unknown | NULL (negativa).';


-- --------------------------------------------------------
-- 3. CAIXA — uma detecção. Várias por amostra.
-- --------------------------------------------------------
--
-- São gravadas TODAS as detecções do frame, não só a que virou
-- ação: as outras são rótulo grátis, e é isso que torna o
-- dataset um dataset de detecção de objetos em vez de uma
-- regressão de um ponto por imagem.

CREATE TABLE IF NOT EXISTS dataset_box (
    id               BIGSERIAL PRIMARY KEY,

    sample_id        TEXT NOT NULL
                     REFERENCES dataset_sample(id)
                     ON DELETE CASCADE,

    category         TEXT NOT NULL,
    template         TEXT,

    confidence       DOUBLE PRECISION,
    color_similarity DOUBLE PRECISION,

    -- Em pixel do frame da amostra.
    x                INTEGER NOT NULL,
    y                INTEGER NOT NULL,
    width            INTEGER NOT NULL,
    height           INTEGER NOT NULL,

    -- Esta é a caixa em que o bot agiu?
    acted            BOOLEAN NOT NULL DEFAULT FALSE
);

COMMENT ON COLUMN dataset_box.acted IS
    'Separa "o que existe na tela" de "o que o bot escolheu".';


-- --------------------------------------------------------
-- 4. ÍNDICES
-- --------------------------------------------------------
--
-- Um por consulta que você de fato vai fazer, não um por
-- coluna.

-- "me dá os open_box que funcionaram"
CREATE INDEX IF NOT EXISTS idx_sample_action_outcome
    ON dataset_sample (action, outcome);

-- inspeção por sessão e por dia
CREATE INDEX IF NOT EXISTS idx_sample_session
    ON dataset_sample (session_id);

CREATE INDEX IF NOT EXISTS idx_sample_created
    ON dataset_sample (created_at);

-- achar quase-duplicatas entre sessões
CREATE INDEX IF NOT EXISTS idx_sample_phash
    ON dataset_sample (phash);

-- montar o lote de treino de uma categoria
CREATE INDEX IF NOT EXISTS idx_box_category
    ON dataset_box (category);

CREATE INDEX IF NOT EXISTS idx_box_sample
    ON dataset_box (sample_id);

-- só as caixas em que houve ação (índice parcial: bem menor)
CREATE INDEX IF NOT EXISTS idx_box_acted
    ON dataset_box (category) WHERE acted;


-- ========================================================
-- CONFERIR
-- ========================================================
--
-- Rode depois do script. Esperado: 3 tabelas e 7 índices.

SELECT table_name,
       (SELECT COUNT(*)
          FROM information_schema.columns c
         WHERE c.table_name = t.table_name) AS colunas
  FROM information_schema.tables t
 WHERE table_schema = 'public'
   AND table_name LIKE 'dataset_%'
 ORDER BY table_name;

-- Esperado:
--   dataset_box       11 colunas
--   dataset_sample    21 colunas
--   dataset_session    3 colunas

SELECT indexname
  FROM pg_indexes
 WHERE schemaname = 'public'
   AND tablename LIKE 'dataset_%'
 ORDER BY indexname;


-- ========================================================
-- CONSULTAS ÚTEIS (depois de ter dados)
-- ========================================================

-- Quanto tenho de cada ação, e quanto disso funcionou?
--
-- SELECT action,
--        COUNT(*)                                    AS total,
--        COUNT(*) FILTER (WHERE outcome='changed')   AS ok,
--        COUNT(*) FILTER (WHERE outcome='unchanged') AS falhou
--   FROM dataset_sample
--  GROUP BY action
--  ORDER BY total DESC;

-- Onde o dataset está fraco: categorias com poucas caixas.
--
-- SELECT category, COUNT(*) AS caixas
--   FROM dataset_box
--  GROUP BY category
--  ORDER BY caixas;

-- Os erros do professor: ações que NÃO funcionaram. Inspecione
-- à mão, ou deixe fora do treino.
--
-- SELECT s.image, s.action, s.target_template, s.target_confidence
--   FROM dataset_sample s
--  WHERE s.outcome = 'unchanged'
--  ORDER BY s.created_at DESC
--  LIMIT 50;

-- Quase-duplicatas entre sessões diferentes.
--
-- SELECT phash, COUNT(*), COUNT(DISTINCT session_id)
--   FROM dataset_sample
--  GROUP BY phash
-- HAVING COUNT(*) > 1
--  ORDER BY 2 DESC;


-- ========================================================
-- APAGAR TUDO (se precisar recomeçar)
-- ========================================================
--
-- CUIDADO: apaga os metadados. As IMAGENS ficam em disco e
-- podem ser reimportadas com tools/dataset_import.py.
--
-- DROP TABLE IF EXISTS dataset_box;
-- DROP TABLE IF EXISTS dataset_sample;
-- DROP TABLE IF EXISTS dataset_session;
