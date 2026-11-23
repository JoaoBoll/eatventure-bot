# IA do EatVentureAI

Treino e execução local da visão do bot. O modo padrão é contínuo: não envia
imagens nem usa serviços pagos.

## Requisitos

- Execute os comandos na pasta principal do projeto.
- Para treinar ou avaliar, é necessário ter amostras coletadas em `dataset/`. Veja [como coletar](../docs/dataset.md).
- Para usar o modo contínuo, basta ter os templates iniciais. Ele começa por
  eles enquanto ainda não existe um modelo aprovado.
- Para acompanhar ou controlar o jogo ao vivo, configure o Android, o ADB, o scrcpy e os templates conforme o [README principal](../README.pt-BR.md).

## Comandos para rodar

```powershell
python -m pip install -r requirements.txt
```

Instala as dependências do projeto.

```powershell
python src/main.py
```

Inicia a **IA contínua local**. Ela usa templates como professor, grava apenas
ações cuja alteração foi confirmada e telas vazias confirmadas, treina quando
há um lote novo suficiente e só ativa o candidato se passar na validação em
telas recentes. O modelo publicado fica em `IA/models/<RESOLUÇÃO>/`; resoluções
não são combinadas. Enquanto não houver promoção, o bot continua seguro pelos
templates.

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

A limpeza só ocorre depois de publicar e promover o candidato. Remove apenas
registros e imagens presentes no snapshot e aproveitados; dados novos ou não
aproveitados permanecem. A publicação mantém as gerações configuradas (padrão:
ativa, anterior e candidata recente).

No painel web, o treino automático verifica o dataset ao iniciar um bot com a opção ativada, mesmo sem coleta ativa, e depois a cada 10 minutos (`AI_TRAIN_CHECK_INTERVAL`); isso também processa lotes que já estavam acumulados. `AI_AUTO_TRAIN` e `AI_TRAIN_BATCH_SIZE`, em [config.py](../src/core/config.py), controlam sua ativação e o mínimo de amostras novas por resolução para iniciar o treino. Para não propagar erros do matcher, o agendador usa somente ações com resultado `changed`, a caixa que motivou essa ação e as telas negativas; `--batch-size` limita o lote apenas quando informado explicitamente. No terminal, o treino automático funciona durante a coleta, com a mesma verificação periódica. A pausa durante o treino no terminal vem ativada por padrão; use `AI_PAUSE_BOT_WHILE_TRAINING = False` para continuar jogando e coletando em paralelo. Ao encerrar a coleta, um treino automático ainda em andamento é interrompido; os dados não consumidos permanecem para a próxima execução. Os modelos e ponteiros publicados em `IA/models/` não são ignorados pelo Git.

## Teste pós-treino

Antes de cada treino, até 200 imagens elegíveis são movidas para
`dataset/post_training_test/<RESOLUÇÃO>/`. Esse conjunto não entra no replay,
na limpeza ou no ajuste do modelo: todo candidato é avaliado nele antes de ser
ativado. No treino contínuo, a reserva prioriza classes críticas como `fly`
quando há exemplos distintos suficientes para deixar pelo menos dois no lote
de treino. A avaliação exclui telas com o mesmo hash perceptual de uma tela
usada no ajuste e informa quantos recortes de cada classe crítica estavam
disponíveis. Use `--post-training-test-size N` para ajustar a reserva; `0` a
desativa em um treino manual.

## Memória de treino

No modo contínuo, o banco em `dataset/cache/continuous/<RESOLUÇÃO>/` guarda
o histórico de vetores extraídos e seus metadados nas tabelas `archive_crops`
e `archive_frames`. Esse histórico não tem limite automático e é separado por
assinatura das features. Na primeira abertura de um cache antigo, os recortes
que ainda estão no replay são copiados para o histórico; recortes descartados
antes dessa atualização não podem ser recuperados dessa forma.

O treino frequente usa o lote novo e uma seleção limitada do histórico, cujo
padrão é 30.000 recortes por resolução. A seleção distribui a capacidade entre
categorias e combina grupos recentes e históricos. Nas classes comuns,
o replay usa um representante por hash perceptual da tela e estado. Classes
com poucos recortes preservam os exemplos individuais, inclusive quando o hash
da tela é igual. O índice de grupos é criado a partir do histórico existente
e atualizado a cada lote. Quando há limite explícito no lote automático,
exemplos confirmados de classes críticas têm prioridade. O painel permite
ajustar o limite do replay até 100.000.

O candidato é comparado ao modelo ativo nos dados recentes e na reserva de
teste. Além das métricas gerais e classes críticas, uma queda de recall em
qualquer categoria com pelo menos cinco exemplos impede sua promoção. Os dados
de um candidato rejeitado não são limpos nem marcados como processados. No
painel, o agendador aguarda mudanças na quantidade de dados ou nas configurações
antes de tentar novamente; essa espera é reiniciada junto com o painel.

A reserva de teste do modo contínuo não recebe frames já presentes no histórico
contínuo. Isso não elimina a possibilidade de imagens visualmente semelhantes
entre treino e teste. A reserva é mantida e ampliada a cada execução, portanto
seu custo de disco e avaliação também cresce.

As imagens reservadas em `post_training_test/` permanecem fora do histórico de
treino. Preserve também imagens brutas com `--keep-data` se quiser poder mudar
a extração de features no futuro: vetores antigos não substituem as imagens
quando a assinatura ou o método de extração muda. Faça backup do histórico e
da reserva de teste, pois ambos podem crescer com o tempo.

## Planejamento futuro

- Ampliar a coleta e a validação nas resoluções abaixo. São metas de cobertura, ainda sem garantia de funcionamento em todas as telas.
- Coletar aproximadamente **100 mil amostras por resolução**, totalizando **900 mil amostras** nas nove configurações iniciais, com situações e sessões variadas.
- Validar depois **1080 × 1920** e telas internas de celulares dobráveis.
- Avaliar um modo incremental separado para substituir a floresta por um classificador compatível com `partial_fit`. A floresta atual é reajustada com o cache, pois Random Forest não aprende novos exemplos incrementalmente.
- Melhorar a seleção do replay usando o histórico e contextos como cidade ou etapa do jogo, quando disponíveis.
- Criar um treino global opcional depois de amadurecer os modelos por resolução: combinar os caches de replay, equilibrar categoria e resolução, normalizar os dados espaciais e validar separadamente cada resolução. Publicar o global somente se não regredir em nenhuma e manter os modelos específicos como fallback.
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
