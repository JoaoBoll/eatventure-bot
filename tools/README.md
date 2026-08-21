Dataset tools
=============

Este diretório contém utilitários para inspecionar e gerenciar o dataset gravado pelo bot (pasta `dataset/`):

- dataset_manager.py  - ferramenta única com vários subcomandos (recomendada)
- delete_session_images.py - utilitário menor (legacy) que apaga imagens por session id

Ambos esperam que `DATASET_DIR` esteja configurado em `src/core/config.py` (padrão: `dataset/`). Execute os comandos a partir da raiz do repositório.

Requisitos
---------

- Python 3.x (venv recomendado)
- Dependências do projeto (seu ambiente de desenvolvimento já deve ter o virtualenv ativado)

Exemplos de uso (Windows PowerShell)
------------------------------------

# Listar sessions e contagens
python tools\dataset_manager.py list-sessions

# Ver o que seria removido para uma session (dry-run - seguro)
python tools\dataset_manager.py remove-session --session <SESSION_ID> --dry-run

# Remover de fato uma session e mover as imagens para backup (recomendado)
python tools\dataset_manager.py remove-session --session <SESSION_ID> --backup-images --yes

# Remover de fato uma session e apagar as imagens (irreversível)
python tools\dataset_manager.py remove-session --session <SESSION_ID> --yes

# Comando legado: apagar imagens por session (sem reescrever o index salvo automaticamente)
python tools\delete_session_images.py --session <SESSION_ID> --dry-run

# Listar imagens órfãs (presentes em dataset/images mas não referenciadas em samples.jsonl)
python tools\dataset_manager.py list-orphans

# Mover órfãs para backup (criando dataset/backup_orphans_<ts>)
python tools\dataset_manager.py move-orphans --yes

# Apagar órfãs (opção de primeiro mover para backup)
python tools\dataset_manager.py delete-orphans --backup --yes

Comportamento importante e segurança
-----------------------------------

- Todos os comandos que reescrevem o índice `samples.jsonl` criam um backup com sufixo `.bak` antes da operação.
- `remove-session` (o novo comando solicitado) faz as duas coisas: remove as imagens correspondentes e reescreve o índice removendo as linhas da session. Por padrão apaga as imagens; passe `--backup-images` para movê-las em vez de apagá-las.
- Use `--dry-run` para confirmar o que será afetado antes de executar ações destrutivas.
- Recomenda-se sempre fazer backup da pasta `dataset/` (ou do repositório) antes de operações em massa.

Arquivos gerados por operações
------------------------------

- `dataset/samples.jsonl.bak` — backup automático do índice antes de reescrever
- `dataset/backup_orphans_<ts>/...` — pasta de backup para imagens órfãs movidas
- `dataset/backup_session_<session>_<ts>/...` — pasta de backup para imagens movidas por `remove-session`
- `moved_list.txt` dentro da pasta de backup — lista dos arquivos movidos

Notas finais
-----------

- As ferramentas operam sobre os arquivos no disco; não fazem commits git.
- Se quiser que eu adapte o comportamento (por exemplo: registrar as ações em um log, ou suportar execução remota/SSH), diga qual fluxo prefere.
