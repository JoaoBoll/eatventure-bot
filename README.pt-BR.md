# EatVenture AI/BOT

[Read in English](README.md)

Bot para automatizar ações no EatVenture em um dispositivo Android. O uso padrão reconhece elementos por templates (imagens de referência); o modelo de IA ainda é experimental.

Este projeto é **exclusivamente para uso local/pessoal**, incluindo a rede local.
Não foi pensado como produto, serviço público ou plataforma para usuários.
Por esse motivo, o painel dispensa autenticação por padrão; o token opcional
continua disponível para quem quiser utilizá-lo.

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

### Painel web

Com o ambiente virtual ativado, instale as dependências do painel e inicie-o:

```powershell
python -m pip install -r requirements-web.txt
```

Abra `http://localhost:8765` no mesmo computador. Para permitir acesso apenas local, use `nohup .venv/bin/python src/web.py --host 127.0.0.1 > /dev/null 2>&1 < /dev/null &`. O painel detecta os dispositivos autorizados pelo ADB e permite iniciar, parar e configurar os bots. Veja [docs/painel-web.md](docs/painel-web.md) para token de acesso, outra porta e operação pela rede local.

Em um servidor Linux, use este comando para iniciar o painel em segundo plano:

```bash
nohup .venv/bin/python src/web.py > /dev/null 2>&1 < /dev/null &
```

O painel continua executando após encerrar a sessão SSH, sem salvar saída em arquivo. Use o botão **Encerrar painel** na interface para pará-lo. Para mantê-lo ativo também após reinicializações do servidor, use o serviço `systemd` descrito em [docs/painel-web.md](docs/painel-web.md).

Para encerrar pelo terminal:

```bash
python src/stop-web.py
```

Em Debian ou Ubuntu, se o `pip` informar `externally-managed-environment`, instale o suporte a ambientes virtuais e use `.venv`; não use `--break-system-packages`:

```bash
sudo apt update
sudo apt install -y python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.txt
nohup .venv/bin/python src/web.py > /dev/null 2>&1 < /dev/null &
```

Se a versão do Python for instalada separadamente e o comando `venv` ainda falhar, instale o pacote correspondente, por exemplo `sudo apt install -y python3.14-venv`.

Para controlar vários aparelhos pelo navegador, iniciar/parar bots e salvar ajustes locais por dispositivo, use o [painel web opcional](docs/painel-web.md). Ele roda no servidor sem interface gráfica; os comandos de terminal abaixo continuam disponíveis.

O painel permite escolher **Bot por templates** ou **Modelo de IA** em
**Configurações → Modo de reconhecimento**. No modo de IA, selecione um modelo
treinado e a fonte de regiões, salve o perfil e reinicie. A aba **Visão** mostra
as detecções do modo em execução nos dois casos, incluindo o modelo ativo
quando usar IA. A seleção automática exige um modelo para a resolução da
captura. Veja os detalhes no [guia do painel](docs/painel-web.md).

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

Para impedir o reconhecimento de templates em partes da tela, abra o seletor
visual de BLOCKs:

```bash
python tools/block_selector.py
```

Arraste sobre quantas áreas quiser no mesmo frame. Use `Enter` ou `s` para
salvar, `u`, `Backspace` ou o botão direito para desfazer a última área e
`r` ou `F5` para atualizar a captura. No painel web, abra **Visão IA**, use
**Adicionar BLOCK**, selecione várias áreas e confirme. Depois, salve o perfil
e reinicie o bot. Os BLOCKs acompanham a resolução do dispositivo.

O BLOCK é aplicado **somente a `box`, `food`, `new_point` e `up_food`**.
As outras categorias, incluindo `engine`, ignoram os BLOCKs e mantêm suas
áreas normais de busca. As posições bloqueadas são excluídas antes do
template matching, inclusive quando há sobreposição parcial do item.

Painel e seletor local compartilham os BLOCKs no perfil em
`local/devices/<hash-do-aparelho>.json`, usando a identidade do aparelho para
agrupar USB e Wi-Fi quando ela estiver disponível. O seletor também aceita
`--state-dir CAMINHO` para usar a mesma pasta de um painel personalizado.
Arquivos antigos em `local/blocks/` são importados quando o perfil ainda não
tem o campo `block_regions`; os arquivos originais são preservados.

Se o bot falhar, tenta se recuperar até **3 vezes**, com **3 segundos** entre
tentativas. Cada falha, tentativa e esgotamento do limite aparece no log.
Isso inclui processos encerrados com erro no painel e no terminal.
Cinco falhas consecutivas do detector ou a interrupção inesperada
da captura também acionam a recuperação. Paradas manuais não reiniciam o bot.
Os limites ficam em `src/core/config.py`: `BOT_RESTART_LIMIT`,
`BOT_RESTART_DELAY` e `VISION_FAILURE_LIMIT`.

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

Executa o bot por templates e coleta imagens e registros em `dataset/data/<resolução>/`. A cada 10 mil registros novos de uma mesma resolução, treina a IA e salva um relatório JSON por execução. Durante o treino no terminal, pausa ações e coleta e retoma automaticamente ao terminar ou falhar. Após a promoção do candidato, remove somente os registros e imagens aproveitados no snapshot, preservando o cache de treinamento. Veja os [comandos da IA](IA/README.md) para treinar manualmente e usar o modelo atualizado.

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
