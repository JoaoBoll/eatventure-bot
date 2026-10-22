# EatVenture AI/BOT

[Read in English](README.md)

Bot para automatizar ações no EatVenture em um dispositivo Android. O uso padrão reconhece elementos por templates (imagens de referência); o modelo de IA ainda é experimental.

## Requisitos

- Python 3.12 ou superior.
- [scrcpy](https://github.com/Genymobile/scrcpy) e ADB instalados.
- Android com depuração USB ou sem fio ativada.

## Preparação

Execute os comandos na raiz do projeto, no PowerShell:

```powershell
py -3.12 -m venv .venv
```

Cria o ambiente Python do projeto.

```powershell
.\.venv\Scripts\Activate.ps1
```

Ativa o ambiente. Repita ao abrir um novo terminal. No Linux ou macOS, crie o ambiente com `python3 -m venv .venv` e ative com `source .venv/bin/activate`.

```powershell
python -m pip install -r requirements.txt
```

Instala as dependências para executar o bot.

```powershell
.\tools\install_scrcpy.ps1
```

Instala o scrcpy e o ADB no Windows e os adiciona ao `PATH`. Em outros sistemas, instale-os pelo gerenciador de pacotes disponível.

No Ubuntu, instale `scrcpy`, `scrcpy-server` e `adb` pelo apt. O bot encontra o servidor junto ao executável ou no diretório `share/scrcpy` ou `lib/scrcpy` da instalação, sem copiar o JAR para o projeto. A versão é detectada automaticamente, com suporte a scrcpy 1.23 ou superior; arquivos avulsos em `tools/scrcpy` continuam usando a versão configurada em `SCRCPY_SERVER_VERSION`.

No Linux sem `DISPLAY` ou `WAYLAND_DISPLAY`, as janelas são desativadas automaticamente para execução em servidor.

## Conectar o Android

Ative a **Depuração USB** nas **Opções do desenvolvedor**, conecte o cabo e aceite a autorização no celular.

```bash
adb devices
```

Lista os dispositivos conectados. O celular deve aparecer com o estado `device`.

Para usar Wi-Fi no Android 11 ou posterior, mantenha computador e celular na mesma rede. Ative **Depuração sem fio** e abra **Parear dispositivo com código de pareamento**.

```bash
adb pair IP:PORTA_DE_PAREAMENTO
```

Pareia o celular com o computador. Informe o código exibido no Android.

```bash
adb connect IP:PORTA_DE_CONEXAO
```

Conecta ao celular pareado. Use o endereço da tela principal de **Depuração sem fio**; a porta pode ser diferente da usada no pareamento.

Para Android 10 ou anterior, conecte e autorize primeiro por USB:

```bash
adb tcpip 5555
adb connect IP_DO_CELULAR:5555
```

Habilita a conexão pela mesma rede Wi-Fi. Confirme com `adb devices` antes de remover o cabo.

## Comandos para rodar

Para controlar vários aparelhos pelo navegador, iniciar/parar bots e salvar ajustes locais por dispositivo, use o [painel web opcional](docs/painel-web.md). Ele roda no servidor sem interface gráfica; os comandos de terminal abaixo continuam disponíveis.

Mantenha o EatVenture aberto, com a tela ligada e desbloqueada.

```bash
python src/main.py
```

Inicia o bot com toques automáticos. Também aprende e atualiza templates para a resolução do dispositivo durante o uso.

Por padrão, as mensagens aparecem somente no terminal. Para salvar o log completo em arquivo:

```bash
py src/main.py --log
```

Com `--log`, cada execução salva um arquivo UTF-8 em `logs/eatventure_<data>_<hora>_<microssegundos>_<pid>.log`, sem sobrescrever os anteriores. O arquivo inclui os logs desde `DEBUG`, as saídas de texto e erros do Python, além da saída do scrcpy e do treino automático. `LOG_LEVEL` controla somente o console; `LOG_DIR`, em `src/core/config.py`, define a pasta dos arquivos.

```bash
python src/test_main.py
```

Mostra a captura do celular, sem realizar toques. Use para conferir a imagem antes de iniciar o bot.

```bash
python src/main.py --device SERIAL_DO_DISPOSITIVO
```

Escolhe o dispositivo pelo serial exibido em `adb devices`. Sem um dispositivo definido pela opção ou pela configuração, o programa pergunta quando há mais de um conectado. Os demais comandos abaixo e o visualizador de captura também aceitam `--device`.

```bash
python src/main_layouts.py
```

Aprende e atualiza templates da resolução atual, sem realizar toques. Os elementos precisam estar visíveis no jogo para serem reconhecidos.

```bash
python src/main.py --layout-only
```

Executa o bot usando somente os templates já aprendidos para a resolução atual. Categorias sem esses templates não serão detectadas.

```bash
python src/main.py --ai-collect
```

Executa o bot por templates e coleta imagens e registros em `dataset/data/<resolução>/`. A cada 10 mil registros novos, somando as resoluções, treina a IA e salva um relatório JSON por execução. Durante o treino, mantém os processos e janelas abertos, pausa as ações e a coleta e retoma automaticamente ao terminar ou falhar. Após o sucesso, remove os registros e imagens aproveitados, preservando o cache de treinamento. Veja os [comandos da IA](IA/README.md) para treinar manualmente e usar o modelo atualizado.

Para continuar jogando e coletando durante o treino automático, defina `AI_PAUSE_BOT_WHILE_TRAINING = False` em [src/core/config.py](src/core/config.py).

```bash
python tools/template_selector.py
```

Permite cadastrar um template manualmente. Arraste para selecionar a área e pressione `Enter` para escolher a categoria e salvar. Use `r` ou `F5` para atualizar a captura e `Esc` para sair.

Para encerrar pelo terminal, use `Ctrl+C`. No visualizador de captura, use `Esc`. No bot e no atualizador, `Esc` também encerra quando a janela de visão está habilitada.

As opções de exibição ficam em [src/core/config.py](src/core/config.py): `SHOW_AI_VISION` mostra as detecções; `SHOW_SCRCPY` abre o espelho que permite interagir manualmente com o jogo.

## Planejamento futuro

Ampliar a coleta e validar o funcionamento nas resoluções abaixo. Os valores estão em **lado menor × lado maior**, em pixels, e representam metas de cobertura ainda sujeitas a validação.

| Dispositivos | Resoluções previstas |
|---|---|
| Celulares | 720 × 1600, 1080 × 2340, 1080 × 2400, 1220 × 2712, 1440 × 3120 |
| Tablets | 800 × 1340, 1200 × 1920, 1600 × 2560, 2136 × 3200 |

- Validar posteriormente 1080 × 1920 e telas internas de celulares dobráveis.
- Implementar a continuação do treinamento a partir do modelo anterior, mantendo o desempenho nas resoluções já aprendidas.
- Aprimorar e validar o detector experimental de IA para reduzir a dependência de templates.
