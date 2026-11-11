# Treinamentos no painel

O botão **Treinamentos**, no topo do painel, abre uma janela independente dos dispositivos. Ela apresenta os dados por resolução, o modelo ativo, o histórico recente e a execução atual.

## Dados disponíveis

- **Amostras:** frames da coleta ainda disponíveis em disco.
- **Novas:** amostras ainda não marcadas como processadas pelo treinamento.
- **Já processadas:** registros marcados pelo treinamento anterior, incluindo dados mantidos no cache.
- **Itens anotados:** recortes com categoria e dimensões válidas informados nos índices.

Essas contagens são prévias. O treino verifica as imagens, descarta entradas inválidas e pode reaproveitar amostras anteriores do cache. O relatório mostra os números efetivamente utilizados e os resultados da validação.

## Treino manual

Selecione uma resolução e o limite de amostras da coleta; zero significa todas. São exigidas pelo menos 20 amostras para solicitar o treino. Os arquivos da coleta são preservados por padrão. A opção de remoção só elimina os dados consumidos depois que o modelo passa na validação e é ativado.

Cada resolução publica seu modelo em `IA/models/<resolução>/category.joblib`, por meio do ponteiro versionado já utilizado pelo bot. Um modelo que não passa na validação permanece como candidato; o modelo ativo é mantido.

## Agendamento e parâmetros

O treino automático da web é centralizado nesta janela. O intervalo define a frequência de verificação, e o mínimo de amostras novas determina quando um lote está apto. O limite do lote pode ser zero, sem limite.

Processos de treino, quantidade de árvores e interrupção dos bots valem para os próximos treinos manuais e automáticos. Uma execução em andamento mantém a configuração com que começou.

Quando a interrupção está habilitada, os bots selecionados são encerrados e reiniciados ao terminar. A opção de abrangência permite parar apenas os bots da resolução treinada (padrão) ou todos os bots do painel. O automático mantém a limpeza após ativação que já existia no projeto.

As configurações são persistidas em `local/training.json`, respeitando o diretório de estado do painel. Os comandos de terminal continuam utilizando seus parâmetros e padrões próprios.

## Execução e acompanhamento

O painel executa um treino por vez e permite solicitar seu cancelamento. Fechar a janela não cancela a execução. O histórico exibe até 30 relatórios recentes; a saída da execução mantém até 200 linhas.

Textos da janela e mensagens próprias de validação usam os catálogos JSON em português e inglês. A saída do processo mantém o idioma escolhido ao solicitar o treino; o agendamento usa o idioma padrão do servidor.

## Permissão global

**Permitir treinamentos de IA (global)** tem prioridade sobre o automático e o manual no painel. Ao desabilitar e salvar, novos treinos são bloqueados e o cancelamento da execução atual é solicitado. O modelo já ativo continua sendo utilizado pelos bots. A interface avisa quando o automático está marcado com a permissão global desabilitada; a API também rejeita solicitações manuais bloqueadas. Configurações antigas mantêm a permissão habilitada e a pausa restrita à resolução. Comandos iniciados fora do painel não são controlados por essa permissão.
