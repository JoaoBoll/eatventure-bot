# Painel web opcional

O painel roda no próprio servidor, sem área de trabalho, Docker ou build de frontend. O comando `python src/main.py` continua disponível independentemente dele.

## Instalar e abrir

Na raiz do projeto, com o ambiente virtual ativado e as dependências do bot instaladas:

```bash
python -m pip install -r requirements-web.txt
python src/web.py
```

Por padrão, o painel escuta em todas as interfaces (`0.0.0.0`). No próprio computador, abra `http://localhost:8765`; em outro computador ou celular na mesma rede, abra `http://IP_DO_SERVIDOR:8765`, substituindo `IP_DO_SERVIDOR` pelo IP do computador que executa o painel.

Para permitir acesso somente no próprio computador, use `python src/web.py --host 127.0.0.1`. A opção `--port` permite alterar a porta padrão `8765`.

Por padrão, o painel abre diretamente, sem token. Para exigir autenticação, inicie com:

```bash
python src/web.py --require-token
```

Nesse modo, o primeiro início cria um token de acesso. Leia-o em outro terminal:

```bash
cat local/dashboard.token
```

No PowerShell: `Get-Content local/dashboard.token`. Cole o token na tela de entrada. Ele fica somente na sessão do navegador. Também é possível fornecer um token próprio de pelo menos 24 caracteres pela variável `EATVENTURE_WEB_TOKEN`; nesse caso, não é criado o arquivo de token.

O painel permite controlar os dispositivos autorizados no ADB desse servidor. Use a rede local ou um proxy HTTPS/VPN para acesso remoto. A porta de escuta e a autenticação do painel são independentes das portas ADB do Android.

## Dispositivos e execução

- **Atualizar** busca os aparelhos conectados e os serviços de depuração Wi-Fi anunciados por mDNS. Para um aparelho já pareado, o painel tenta reconectar usando o endereço e a porta atuais, mesmo que tenham mudado. O perfil é associado ao serial físico ou ao nome mDNS estável, não ao IP:porta.
- **Conectar Android** permite informar endereço e porta da depuração Wi-Fi. O pareamento tem endereço/porta e código próprios, diferentes da porta de conexão.
- A reconexão automática requer que a Depuração sem fio continue ativada, que o pareamento não tenha sido revogado e que o servidor consiga descobrir mDNS na mesma rede. Para conferir a descoberta, execute `adb mdns services`; se o aparelho não aparecer, verifique a rede local/multicast e a configuração mDNS do ADB. Estados offline e não autorizado também aparecem no painel; a autorização USB deve ser aceita no Android.
- Selecione um ou vários aparelhos e use **Iniciar**, **Parar** ou **Reiniciar**. Cada aparelho possui processo, logs e métricas separados.
- USB e Wi-Fi do mesmo aparelho são agrupados pelo identificador físico quando o Android o disponibiliza. USB é preferido para executar. Se esse identificador não estiver disponível, o perfil usa o nome mDNS estável no Wi-Fi ou o serial ADB no USB.
- Os cards mostram estado, voos, reformas, ações, templates carregados, FPS e bateria, atualizados a cada segundo sem aguardar a descoberta do ADB. Valores ainda indisponíveis aparecem como `—`.
- Os logs recentes são limitados a 500 linhas por dispositivo e 1 MiB de texto no servidor. O navegador retém até 500 linhas e 512 KiB, e cada linha tem no máximo 4 KiB. As entradas mais antigas são descartadas; o download contém somente o trecho retido. A opção **Salvar arquivo de log** mantém também o log completo no disco.

O painel só gerencia processos que ele iniciou. Ao encerrar o servidor web, solicita a parada desses bots e dos treinos filhos. Fechar somente a aba do navegador mantém os bots ativos. Não inicie outra instância do bot pelo terminal para o mesmo aparelho simultaneamente.

## Visão IA no navegador

Clique no card do dispositivo para abrir diretamente a **Visão IA**; com o bot em execução, a transmissão começa automaticamente. A engrenagem continua abrindo as configurações. A prévia mostra a tela com as detecções e métricas desenhadas pelo bot, sem abrir janelas no servidor, tanto no Windows quanto no Linux. A transmissão usa uma conexão HTTP contínua (MJPEG), com até 20 imagens por segundo, conforme a captura e a conexão.

Desenho, redução e compressão das imagens rodam em uma thread exclusiva da prévia de cada dispositivo. Ela sempre usa o quadro mais recente e descarta os pendentes antigos, sem formar uma fila de vídeo atrasado nem fazer esse trabalho no loop de ações do bot.

A transmissão é opcional: só prepara imagens enquanto a aba de visão estiver aberta. Trocar de aba, fechar o dispositivo ou ocultar o navegador interrompe a transmissão; o bot continua ativo. Se a conexão cair, a solicitação expira automaticamente. Apenas o quadro mais recente fica temporariamente em `local/runtime/`, sem criar um histórico de imagens.

Após atualizar o código do painel, reinicie o servidor web e os bots pelo painel para carregar as alterações. Recarregue também a página do navegador.

Para continuar usando somente o terminal, os comandos permanecem os mesmos:

```bash
python src/main.py --device SERIAL
python src/main.py --device SERIAL --ai-collect --log
```

O terminal não depende das bibliotecas do painel. A gravação de log em arquivo continua opcional com `--log`.

## Configurações locais por aparelho

Abra o aparelho e ajuste as abas **Configurações** e **Itens**. É possível alterar fidelidade da forma/cor, FPS, intervalo de detecção, aprendizado, coleta, treino e tempos de ações. Ative **Personalizar** somente nos campos que deseja substituir.

A fidelidade de forma segue esta prioridade:

1. Valor personalizado para o item.
2. Valor personalizado ou padrão da categoria.
3. Fidelidade global de forma, quando a categoria não tem valor próprio.

As opções de coleta de IA, uso exclusivo dos templates da resolução e gravação de log também são individuais. **Salvar** grava o perfil; **Reiniciar** aplica os ajustes ao bot ativo. Restaurar um item, grupo ou perfil devolve os padrões do código sem editar nenhum arquivo Python. A restauração do perfil preserva dataset, modelos, templates aprendidos e logs existentes.

Os arquivos ficam em:

```text
local/
  dashboard.token                    # somente com --require-token
  devices/<hash-do-aparelho>.json
  runtime/<hash>.status.json
  runtime/<hash>.vision.*             # prévia temporária sob demanda
  device-data/<hash>/
    templates/
    logs/
dataset/
  data/<RESOLUÇÃO>/samples.jsonl      # coleta compartilhada com o terminal
  data/<RESOLUÇÃO>/images/
```

`local/` é ignorado pelo Git. Faça backup dessa pasta para preservar os perfis e dados. Os templates compartilhados do projeto são lidos como base; o aprendizado feito pelo painel grava somente na pasta do aparelho. Com `AI_AUTO_TRAIN = True` e a coleta habilitada no perfil, o treino automático acontece ao chegar a 10 mil amostras ainda não treinadas de uma mesma resolução; cada modelo fica em `IA/models/<RESOLUÇÃO>/` e pode ser versionado no Git. Durante o treino, os bots que estão coletando são encerrados e reiniciados quando ele termina. O dataset é compartilhado entre painel e terminal em `dataset/`. Alterações no perfil são aplicadas antes dos módulos do bot serem importados em cada processo.

## Serviço no Ubuntu

Exemplo de `/etc/systemd/system/eatventure-web.service`, ajustando usuário e caminhos:

```ini
[Unit]
Description=Painel EatVenture
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=sv
WorkingDirectory=/Server/eatventure-bot
ExecStart=/Server/eatventure-bot/.venv/bin/python /Server/eatventure-bot/src/web.py --host 0.0.0.0 --port 8765
Restart=on-failure
TimeoutStopSec=40
KillMode=mixed

[Install]
WantedBy=multi-user.target
```

Use o mesmo usuário que autorizou o ADB e que tem permissão de escrita em `local/`. Não configure múltiplos workers do Uvicorn: o supervisor dos dispositivos pertence a um único processo. `--state-dir CAMINHO` permite guardar os dados locais em outro diretório.

## Verificação simulada

Os testes do painel usam ADB, processos e HTTP simulados, sem servidor de rede ou dispositivos reais:

```bash
python -m pip install pytest httpx
python -m pytest -q tests/test_device_profiles.py tests/test_dashboard_supervisor.py tests/test_dashboard_api.py tests/test_dashboard_stream.py tests/test_dashboard_logs.py tests/test_web_runtime.py tests/test_web_preview.py tests/test_web_worker.py tests/test_dashboard_runtime_flow.py tests/test_web_cli.py
node --test tests/dashboard_ui.test.cjs
```
