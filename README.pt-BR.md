# EatVenture AI/BOT

[Read in English](README.md)

Bot de visão para EatVenture. Ele recebe o vídeo do Android pelo scrcpy-server, encontra elementos por comparação de templates e envia toques pelo ADB.

> O detector por templates é o modo estável. A coleta e o modelo em `IA/` são experimentais.

## Requisitos

- Python 3.12 ou superior;
- [scrcpy](https://github.com/Genymobile/scrcpy) e `adb` disponíveis no `PATH` — no Windows, o scrcpy também pode ficar em `tools/scrcpy/`;
- Um Android com Depuração USB ou Depuração sem fio ativada.

## Instalação

Na raiz do projeto, crie um ambiente virtual e instale as dependências:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

No Linux ou macOS, ative o ambiente com:

```bash
source .venv/bin/activate
```

### scrcpy e ADB

No Windows, o projeto oferece um instalador do scrcpy:

```powershell
.\tools\install_scrcpy.ps1
```

Ele instala em `tools/scrcpy/`, local encontrado automaticamente pelo projeto. Em outros sistemas, instale o scrcpy pela forma recomendada pela sua distribuição e confirme que `scrcpy` e `adb` estão no `PATH`:

```bash
scrcpy --version
adb version
```

### Preparar o Android

1. Abra **Configurações > Sobre o telefone** e toque sete vezes em **Número da versão**. Em alguns aparelhos, a opção está em **Informações do software**.
2. Volte para Configurações e abra **Opções do desenvolvedor**.
3. Ative **Depuração USB**. Para usar Wi-Fi no Android 11 ou posterior, ative também **Depuração sem fio**.

## Conectar o dispositivo

### USB

Conecte o dispositivo por cabo e aceite a autorização de depuração exibida no Android. Para confirmar a conexão:

```bash
adb devices
```

O dispositivo deve aparecer com o estado `device`, e não `unauthorized` ou `offline`.

### Wi-Fi: Android 11 ou posterior

Computador e celular devem estar na mesma rede. Em **Opções do desenvolvedor > Depuração sem fio**, toque em **Parear dispositivo com código de pareamento** e execute:

```bash
adb pair IP:PORTA_DE_PAREAMENTO
```

Informe o código exibido pelo Android. Em seguida, na tela principal de **Depuração sem fio**, use o IP e a porta de conexão (ela pode ser diferente da porta de pareamento):

```bash
adb connect IP:PORTA_DE_CONEXAO
adb devices
```

### Wi-Fi: Android 10 ou anterior

Primeiro conecte e autorize o dispositivo por USB. Com os dois aparelhos na mesma rede Wi-Fi, execute:

```bash
adb tcpip 5555
adb connect IP_DO_CELULAR:5555
adb devices
```

O IP do celular aparece nos detalhes da rede Wi-Fi. Depois de confirmar o estado `device`, o cabo pode ser removido. Uma reinicialização do Android normalmente exige repetir a conexão USB e o `adb tcpip 5555`.

## Uso

Mantenha o jogo aberto, desbloqueado e com a tela ligada. Antes de permitir toques, confira a captura:

```bash
python src/test_main.py
```

Feche a janela com `Esc` e inicie o bot:

```bash
python src/main.py
```

Se houver mais de um dispositivo conectado, o programa pergunta qual usar. Para escolher um diretamente:

```bash
python src/main.py --device SERIAL_DO_DISPOSITIVO
```

Também é possível definir `DEVICE_SERIAL` em [src/core/config.py](src/core/config.py). O mesmo `--device` funciona nos utilitários que usam o Android.

`Esc` encerra o bot somente quando `SHOW_AI_VISION` está ativado; em qualquer configuração, `Ctrl+C` encerra pelo terminal.

## Configuração

As opções do bot ficam em [src/core/config.py](src/core/config.py). As mais úteis são:

| Opção | Finalidade |
|---|---|
| `SHOW_AI_VISION` | Mostra a janela com detecções e status. |
| `SHOW_SCRCPY` | Abre o espelho interativo do scrcpy. Não é necessário para o bot funcionar. |
| `VISION_FILTER_BY_STATE` | Pesquisa somente as categorias relevantes ao estado atual. |
| `SHOW_DETECTION_LABELS`, `SHOW_FPS`, `SHOW_DETECTION_LAG` | Controlam informações exibidas na janela de visão. |
| `DEVICE_SERIAL` | Define o dispositivo ADB padrão. |

O bot usa o stream direto do scrcpy-server; a janela do scrcpy é apenas um espelho para acompanhamento ou intervenção manual.

Para interagir manualmente com o jogo, ative `SHOW_SCRCPY`. A janela de visão (`SHOW_AI_VISION`) é apenas informativa e não recebe toques.

### Templates por resolução

Por padrão, o detector aprende templates específicos para a resolução do dispositivo enquanto usa os templates padrão. Para executar o bot somente com esses templates específicos, mantenha o atualizador aberto em um terminal:

```bash
python src/main_layouts.py --device SERIAL_DO_DISPOSITIVO
```

E, em outro terminal, execute:

```bash
python src/main.py --layout-only --device SERIAL_DO_DISPOSITIVO
```

Use `--layout-only` apenas depois que houver templates aprendidos para a resolução; sem eles, as categorias correspondentes não serão detectadas.

## Templates

Quando um elemento não for detectado corretamente, registre um template novo:

```bash
python tools/template_selector.py
```

Arraste para selecionar a área e pressione `Enter` para salvar. Use `R` ou `F5` para capturar a tela novamente.

Para verificar e compactar a numeração dos templates:

```bash
python tools/renumerar.py
python tools/renumerar.py --aplicar
```

O primeiro comando apenas mostra as alterações; `--aplicar` renomeia os arquivos.

## Dataset e IA

Para gravar dados durante uma sessão, use:

```bash
python src/main.py --ai-collect
```

Ou ative `DATASET_SAVE` na configuração. O banco PostgreSQL é opcional; detalhes do formato e da importação estão em [docs/dataset.md](docs/dataset.md).

A coleta grava imagens no diretório `dataset/`. Verifique o espaço disponível antes de ativá-la; o limite padrão é 20 GB (`DATASET_MAX_DISK_MB`).

O fluxo de treino e de execução do modelo está documentado em [IA/README.md](IA/README.md). O plano para substituir templates por detector treinado está em [docs/detector-ia.md](docs/detector-ia.md).

## Testes

Os testes são scripts Python e não exigem dispositivo:

```bash
python tests/test_detection.py
python tests/test_state_machine.py
python tests/test_pipeline.py
python tests/test_renumerar.py
python tests/test_selector_layout.py
python tests/test_devices.py
python tests/test_ciclo.py
python tests/test_dataset.py
python tests/test_ia.py
```

Após alterar templates ou limiares, valide a regressão de detecção:

```bash
python tests/test_detection.py
```

`python tests/test_detection.py --update` altera os resultados esperados; use-o somente para aceitar uma mudança intencional e revise o diff antes de salvar.

## Estrutura

| Caminho | Responsabilidade |
|---|---|
| [src/capture/screen.py](src/capture/screen.py) | Stream de vídeo do scrcpy-server. |
| [src/vision/detector.py](src/vision/detector.py) | Detecção por templates. |
| [src/vision/worker.py](src/vision/worker.py) | Processamento de visão em segundo plano. |
| [src/core/state_machine.py](src/core/state_machine.py) | Prioridades e transições de estado. |
| [src/actions/](src/actions) | Toques e comandos ADB. |
| [src/core/config.py](src/core/config.py) | Configuração central. |
| [tools/](tools) | Utilitários para templates, dataset e manutenção. |
