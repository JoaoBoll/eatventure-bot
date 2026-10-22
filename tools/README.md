# Ferramentas do dataset

[Voltar ao README do projeto](../README.pt-BR.md)

Execute os comandos na raiz do projeto, com o ambiente Python e as dependências instalados conforme o README principal.

## Comandos para rodar

```bash
python tools/dataset_manager.py list-orphans
```

Lista as imagens sem referência nos registros da coleta. Procura automaticamente em `dataset/data/<resolução>/` e no formato antigo, caso exista.

```bash
python tools/dataset_manager.py list-orphans --jsonl dataset/data/RESOLUCAO/samples.jsonl
```

Consulta somente uma resolução. Substitua `RESOLUCAO` pelo nome da pasta desejada. A opção `--jsonl` também pode limitar os dois comandos abaixo à mesma coleta.

```bash
python tools/dataset_manager.py move-orphans
```

Move as imagens sem referência para uma pasta de backup ao lado da pasta `images`. Pede confirmação antes de mover.

```bash
python tools/dataset_manager.py delete-orphans --backup
```

Move as imagens sem referência para backup. Sem `--backup`, apaga essas imagens e limpa os registros cujas imagens não existem mais.

## Sessões de coletas antigas

Os comandos abaixo usam o formato antigo, com `dataset/samples.jsonl` e `dataset/images/`. A listagem e a remoção por sessão ainda não estão adaptadas às coletas por resolução.

```bash
python tools/dataset_manager.py list-sessions
```

Lista as sessões e a quantidade de amostras de cada uma.

```bash
python tools/dataset_manager.py remove-session --session ID_DA_SESSAO --dry-run
```

Mostra quais imagens e registros seriam removidos, sem alterar arquivos. Substitua `ID_DA_SESSAO` por um identificador da listagem.

```bash
python tools/dataset_manager.py remove-session --session ID_DA_SESSAO --backup-images
```

Remove os registros da sessão e move suas imagens para backup após confirmação. Também cria uma cópia do índice em `samples.jsonl.bak`. Sem `--backup-images`, apaga as imagens da sessão.
