# IA do EatVentureAI

Treino e execução do modelo de visão experimental do bot.

## Requisitos

- Execute os comandos na pasta principal do projeto.
- Para treinar ou avaliar, é necessário ter amostras coletadas em `dataset/`. Veja [como coletar](../docs/dataset.md).
- Para usar a IA, execute um treino primeiro. As versões ficam em `IA/models/`, e o bot encontra a versão atual automaticamente.
- Para acompanhar ou controlar o jogo ao vivo, configure o Android, o ADB, o scrcpy e os templates conforme o [README principal](../README.pt-BR.md).

## Comandos para rodar

```powershell
python -m pip install -r requirements.txt
```

Instala as dependências do projeto.

```powershell
python src/main.py --ai-collect
```

Coleta dados usando o bot por templates. A cada **10 mil registros novos**, somando todas as resoluções, inicia um treino automático. Durante o treino, mantém os processos e janelas abertos, mas pausa as ações do bot e a coleta. A execução retoma automaticamente quando o treino termina ou falha.

```powershell
python IA/train_ai.py
```

Faz um treino manual sem esperar 10 mil registros. Publica o modelo novo, salva o relatório e remove os registros e imagens aproveitados nesse treino. O cache mantém os dados dos treinos anteriores, mesmo após a limpeza das imagens.

```powershell
python IA/train_ai.py --keep-data
```

Faz o treino manual preservando os registros e as imagens.

```powershell
python IA/bot_ai.py --demo --demo-limit 30
```

Avalia o modelo em até 30 imagens coletadas, sem precisar conectar um aparelho.

```powershell
python IA/bot_ai.py
```

Mostra as detecções da IA no jogo ao vivo, sem enviar comandos ao aparelho. Por padrão, usa os templates para localizar os elementos que o modelo vai reconhecer.

```powershell
python IA/bot_ai.py --auto
```

Permite que o bot controle o jogo usando as detecções da IA. Os comandos ao vivo carregam os modelos novos automaticamente entre os frames, sem reiniciar. Uma atualização inválida mantém o modelo anterior em uso.

Nos comandos ao vivo, adicione `--device SERIAL` para escolher o aparelho ou `--headless` para executar sem janela. Encerre com `Ctrl+C`.

## Relatórios e limpeza

Cada treino gera um JSON próprio em `IA/training_reports/`, com data, duração, resultado, parâmetros e métricas de validação. As contagens incluem ações, tipos, estados, resultados e resoluções, separando o lote atual dos dados reaproveitados. Cada arquivo descreve somente aquela execução.

A limpeza só ocorre depois de salvar o modelo e o relatório com sucesso. Remove apenas os registros e imagens aproveitados; dados novos ou não aproveitados permanecem. Se a limpeza for interrompida, ela é retomada na próxima verificação da coleta ou no próximo treino.

O automático funciona enquanto a coleta está ligada. `AI_AUTO_TRAIN` e `AI_TRAIN_BATCH_SIZE`, em [config.py](../src/core/config.py), controlam sua ativação e o tamanho do lote. A pausa durante o treino vem ativada por padrão; use `AI_PAUSE_BOT_WHILE_TRAINING = False` para continuar jogando e coletando em paralelo. Ao encerrar a coleta, um treino automático ainda em andamento é interrompido; os dados não consumidos permanecem para a próxima execução.

## Planejamento futuro

- Ampliar a coleta e a validação nas resoluções abaixo. São metas de cobertura, ainda sem garantia de funcionamento em todas as telas.
- Coletar aproximadamente **100 mil amostras por resolução**, totalizando **900 mil amostras** nas nove configurações iniciais, com situações e sessões variadas.
- Validar depois **1080 × 1920** e telas internas de celulares dobráveis.
- Implementar a continuação do treino a partir do modelo anterior, preservando o desempenho nas resoluções já aprendidas. Atualmente, os dados são reaproveitados, mas cada treino cria um modelo novo.
- Aprimorar e validar a detecção experimental por cor e contorno para reduzir a dependência de templates.

Resoluções planejadas, em pixels, no formato **lado menor × lado maior**:

- 720 × 1600
- 1080 × 2340
- 1080 × 2400
- 1220 × 2712
- 1440 × 3120
- 800 × 1340
- 1200 × 1920
- 1600 × 2560
- 2136 × 3200

A coleta deve considerar a resolução efetiva recebida pelo bot, que pode diferir da anunciada pelo aparelho.
