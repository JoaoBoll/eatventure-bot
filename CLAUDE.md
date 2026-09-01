# Instruções do projeto

## Regra principal: economia de contexto

- **Concisão Extrema**: Escreva apenas o código estritamente necessário. Não explique o código a menos que seja solicitado.
- **Raciocínio Direto**: Vá direto ao ponto, não crie planos longos para tarefas simples.
- **Estilo de Resposta**: Sem introduções ("Com certeza, vou ajudar...") ou conclusões. Mostre apenas o resultado ou diff.


- Seja econômico com contexto e tokens.
- Leia somente os arquivos estritamente necessários para realizar a tarefa.
- NÃO faça varreduras completas do projeto sem necessidade.
- NÃO abra arquivos só para "entender melhor" se eles não forem relevantes para a tarefa.
- Antes de ler um arquivo, determine se ele é realmente necessário.
- Prefira arquivos diretamente relacionados ao código que está sendo alterado.
- Não leia arquivos grandes inteiros quando apenas uma parte for necessária.
- Evite repetir a leitura de arquivos que já foram analisados nesta sessão.
- Não procure informações em todo o projeto quando a tarefa puder ser resolvida localmente.
- Não procure ou use nenhuma imagem para nenhum fim, apenas use o path dela, mas sem processar nada referente a ela.
- Não comente instruções a cada coisa que é criada, métodos/funções devem ser intuítivos (criarXcoisa, editarXcoisa, encontrarXcoisa)
- Não teste nenhuma parte do sistema, isso será feito pelo desenvolvedor, apenas auxilie no desenvolvimento.

## Imagens e arquivos binários

NÃO leia, abra, analise ou processe imagens, exceto se eu pedir explicitamente.

Ignore completamente:

- *.png
- *.jpg
- *.jpeg
- *.webp
- *.gif
- *.bmp
- *.tiff
- *.ico
- *.avif

Também não abra arquivos de vídeo, áudio ou outros binários sem minha autorização explícita.

## Assets

Não explore automaticamente pastas como:

- assets/
- images/
- img/
- public/images/
- static/images/
- uploads/
- media/

Se precisar verificar a existência de um asset, liste apenas os nomes/caminhos necessários, sem abrir o conteúdo.

## Código

Priorize:

1. Arquivos diretamente mencionados por mim.
2. Arquivos que importam ou são importados pelo código em questão.
3. Testes diretamente relacionados.
4. Configurações necessárias para executar ou testar a alteração.

Não leia o projeto inteiro para fazer uma alteração localizada.

## Busca

Ao procurar código:

- Faça buscas específicas.
- Evite `find` ou listagens recursivas gigantes.
- Não percorra `node_modules`, `.git`, builds ou caches.
- Não procure dentro de arquivos binários.
- Não faça múltiplas buscas equivalentes sem necessidade.

## Diretórios que devem ser ignorados

Não explore:

- .git/
- node_modules/
- dist/
- build/
- coverage/
- .cache/
- .next/
- out/
- target/
- vendor/
- tmp/
- temp/

A menos que eu peça explicitamente.

## Dependências

Não leia o conteúdo de dependências instaladas para entender o projeto.

Consulte documentação, tipos, imports ou arquivos específicos somente quando necessário.

## Git

Não faça análises completas do histórico Git.

Não execute comandos como:

- git log --all
- git log --stat
- git diff de todo o projeto
- git show de commits não relacionados

A menos que eu peça.

## Testes

Depois de alterar código:

- Execute somente os testes relevantes.
- Não execute a suíte inteira se não for necessário.
- Se eu fornecer um comando específico de teste, prefira esse comando.
- Não rode testes repetidamente sem motivo.

## Antes de agir

Para tarefas simples:

1. Identifique os arquivos necessários.
2. Leia somente esses arquivos.
3. Faça a alteração.
4. Rode apenas a verificação necessária.

Não transforme uma tarefa pequena em uma análise completa do projeto.

## Imagens

Se uma imagem parecer relevante para a tarefa, NÃO a abra automaticamente.

Pergunte antes:

"Preciso acessar uma imagem do projeto para continuar. Posso abrir?"

Só acesse imagens depois de autorização explícita.