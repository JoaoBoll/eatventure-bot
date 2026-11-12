# IA do EatVentureAI

Treino e execução do modelo de visão experimental do bot.

## Requisitos

- Execute os comandos na pasta principal do projeto.
- Para treinar ou avaliar, é necessário ter amostras coletadas em `dataset/`. Veja [como coletar](../docs/dataset.md).
- Para usar a IA, execute um treino primeiro. As versões ficam em `IA/models/<RESOLUÇÃO>/`; selecione o modelo da resolução do aparelho com `--model`.
- Para acompanhar ou controlar o jogo ao vivo, configure o Android, o ADB, o scrcpy e os templates conforme o [README principal](../README.pt-BR.md).

## Comandos para rodar

```powershell
python -m pip install -r requirements.txt
```

Instala as dependências do projeto.

```powershell
python src/main.py --ai-collect
```

Coleta dados usando o bot por templates. Com `AI_AUTO_TRAIN = True`, quando há pelo menos **10 mil registros novos de uma mesma resolução**, o treino independente usa todos os registros novos disponíveis daquela resolução. O modelo publicado fica em `IA/models/<RESOLUÇÃO>/`; diferentes resoluções não são combinadas. No painel web, somente bots que coletam essa resolução são pausados durante o treino e reiniciados ao final.

```powershell
python IA/train_ai.py
```

Faz um treino manual sem esperar 10 mil registros. Salva um candidato, atualiza o modelo ativo apenas quando ele passa nos critérios de validação e remove os registros e imagens aproveitados somente depois dessa promoção. O cache mantém os dados dos treinos anteriores, mesmo após a limpeza das imagens.

```powershell
python IA/train_ai.py --keep-data
```

Faz o treino manual preservando os registros e as imagens.

```powershell
python IA/bot_ai.py --demo --demo-limit 30
```

Avalia o modelo em até 30 imagens coletadas, sem precisar conectar um aparelho.

```powershell
python IA/bot_ai.py --model IA/models/1080x2400/category.joblib
```

Mostra as detecções da IA no jogo ao vivo, sem enviar comandos ao aparelho. Por padrão, usa os templates para localizar os elementos que o modelo vai reconhecer.

```powershell
python IA/bot_ai.py --auto
```

Permite que o bot controle o jogo usando as detecções da IA. Os comandos ao vivo carregam os modelos novos automaticamente entre os frames, sem reiniciar. Uma atualização inválida mantém o modelo anterior em uso.

Nos comandos ao vivo, adicione `--device SERIAL` para escolher o aparelho ou `--headless` para executar sem janela. Encerre com `Ctrl+C`.

No **painel web**, abra as configurações do aparelho e escolha **Modelo de IA**
em **Modo de reconhecimento**. Selecione o modelo (ou automático por resolução),
a fonte de regiões e a confiança mínima. Salve e reinicie. A aba **Visão**
transmite as detecções do modelo com o mesmo controle de pausa e logs do bot
por templates. O modelo é carregado pela resolução real da captura no modo
automático e atualizado entre frames quando houver uma nova publicação.

## Relatórios e limpeza

Cada treino gera um resumo JSON em `IA/training_reports/`, com data, duração, resultado, parâmetros, imagens usadas, categorias/classes e contagens, extração e métricas de validação por classe. O relatório inclui macro F1, recall por classe e a decisão de promoção. Não lista IDs individuais nem despeja tabelas extensas no terminal. Os metadados do modelo guardam o necessário para carregá-lo e consultar seu resumo de treino.

A limpeza só ocorre depois de publicar e promover o candidato. Remove apenas registros e imagens presentes no snapshot e aproveitados; dados novos ou não aproveitados permanecem. Se a limpeza for interrompida, ela é retomada na próxima verificação da coleta ou no próximo treino.

No painel web, o treino automático verifica o dataset ao iniciar um bot com a opção ativada, mesmo sem coleta ativa, e depois a cada 10 minutos (`AI_TRAIN_CHECK_INTERVAL`); isso também processa lotes que já estavam acumulados. `AI_AUTO_TRAIN` e `AI_TRAIN_BATCH_SIZE`, em [config.py](../src/core/config.py), controlam sua ativação e o mínimo de amostras novas por resolução para iniciar o treino. Para não propagar erros do matcher, o agendador usa somente ações com resultado `changed`, a caixa que motivou essa ação e as telas negativas; `--batch-size` limita o lote apenas quando informado explicitamente. No terminal, o treino automático funciona durante a coleta, com a mesma verificação periódica. A pausa durante o treino no terminal vem ativada por padrão; use `AI_PAUSE_BOT_WHILE_TRAINING = False` para continuar jogando e coletando em paralelo. Ao encerrar a coleta, um treino automático ainda em andamento é interrompido; os dados não consumidos permanecem para a próxima execução. Os modelos e ponteiros publicados em `IA/models/` não são ignorados pelo Git.

## Planejamento futuro

- Ampliar a coleta e a validação nas resoluções abaixo. São metas de cobertura, ainda sem garantia de funcionamento em todas as telas.
- Coletar aproximadamente **100 mil amostras por resolução**, totalizando **900 mil amostras** nas nove configurações iniciais, com situações e sessões variadas.
- Validar depois **1080 × 1920** e telas internas de celulares dobráveis.
- Avaliar um modo incremental separado para substituir a floresta por um classificador compatível com `partial_fit`. A floresta atual é reajustada com o cache, pois Random Forest não aprende novos exemplos incrementalmente.
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
