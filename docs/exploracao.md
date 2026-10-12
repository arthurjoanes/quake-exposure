# Explorar a carteira

Abra a [demonstração](https://arthurjoanes.github.io/quake-exposure/) ou o ambiente local em `http://127.0.0.1:8000`. A interface usa a mesma publicação para resumo, gráficos e ativos.

## Navegar pelo painel

Escolha escopo, carteira, terremoto e critério nos filtros. No celular, abra os filtros para alterar a seleção. Os links Mapa, Gráficos, Ativos e Operação levam à seção correspondente.

O cabeçalho identifica REPLAY e a data do snapshot. Abra a identificação para consultar o contexto da captura. Avisos de fonte desatualizada aparecem quando há falha de atualização. Se a consulta da análise falhar, Tentar novamente repete a consulta com a seleção atual.

Busca, situação, moeda e página da lista são preservadas nas atualizações da mesma seleção. Ao trocar carteira, terremoto, escopo ou critério, a lista começa uma nova consulta.

A URL guarda escopo, carteira, terremoto, critério, busca, situação, moeda, página e tamanho da página. Ao recarregar ou compartilhar o endereço, a lista recupera esses filtros e a comparação é reaberta se estava aberta. A área desenhada não é guardada na URL.

## Área no mapa

Selecione uma carteira e um terremoto. Clique em Área e marque pelo menos três pontos no mapa; Concluir fecha o polígono. Desfazer remove o último vértice e Cancelar recupera a seleção anterior. Como usar reúne as instruções do mapa e os atalhos de teclado.

Arraste um vértice para ajustar a área. Pelo teclado, use Tab para chegar ao mapa, as setas para movê-lo e Espaço para marcar o ponto central. Enter conclui, Backspace desfaz e Esc cancela. Um vértice focado aceita as setas para ajuste.

O resultado da área aparece junto ao mapa, antes dos gráficos, e mostra ativos, elegíveis, classificados e valores em BRL e USD. A lista contém os ativos dentro do polígono, incluindo a borda. Busca, situação, moeda e paginação continuam disponíveis. A ação de enquadramento no topo do mapa passa de Carteira para Área quando há recorte. Limpar restaura a carteira inteira. O resumo e os gráficos gerais mantêm o resultado completo da publicação.

Selecionar um ativo na lista centraliza sua posição e abre seus detalhes no mapa. Seu marcador aparece mesmo quando o ativo estava fora do enquadramento consultado.

O mapa consulta o enquadramento e o zoom. A interface pede até 1.200 pontos ou grupos; a API aceita até 2.000. Os grupos conservam contagens e valores por moeda. Ampliar revela os pontos. O carregamento regional limita o desenho do mapa; as listas e os gráficos ainda usam o conjunto completo de fatos da publicação.

## Comparar publicações

Comparar abre e focaliza o painel junto ao mapa, antes dos gráficos. Ele mostra as causas, contagens, valores anteriores e atuais e a variação por moeda. A lista indica deslocamento, entrada ou saída do critério e mudanças de valor ou intensidade.

O contorno tracejado identifica a posição anterior; o ponto preenchido mostra a atual; uma linha liga os deslocamentos. Como ler o mapa reúne essa legenda. Ver no mapa enquadra os ativos da página em consulta. Cada página contém até 100 alterações, com navegação para as demais.

Fechar comparação está disponível durante a consulta e após uma falha. Tentar novamente repete a consulta da página que falhou. Fechar remove as posições históricas do mapa.

A comparação usa publicações da mesma carteira, evento, escopo e critério e considera a carteira inteira, mesmo quando há uma área selecionada. Mudanças de posição, valor, moeda, intensidade ou classificação entram na lista. Versões e identificadores ficam em Dados das publicações. Se uma exclusão invalidar a versão anterior, suas posições e seus valores ficam indisponíveis. Publicações sem resultado calculado mantêm contagens e valores indisponíveis, inclusive na variação.

Na demonstração inicial, `ope-00001` passa de MMI 4,0 para 6,7 após uma correção retroativa da carteira fictícia. Os classificados passam de 45 para 46. O total USD aumenta exatamente US$ 16.131,63; BRL mantém o mesmo valor. No evento das Kurilas, as duas versões da carga também permitem consultar mudanças de posição e vigência.

## Processamento

Operação começa recolhida; o link da navegação abre a seção. O fluxo aparece em Carteira, Atualizações, Cálculo e Publicação, com rótulos de conexão, pendência ou falha. Dados técnicos reúne os identificadores, conector, tarefa, slot, WAL retido e horários persistidos. No snapshot, Publicações mostra os resultados do exemplo. A fila e as publicações pertencem à carteira selecionada; a saúde do CDC e das fontes é compartilhada pela plataforma.

O intervalo cálculo → publicação usa a diferença entre os horários gravados. Quando o intervalo não foi medido, a tabela informa não medido. O tempo completo da origem até o navegador não é medido.

## Demonstração pública

A página pública lê arquivos REPLAY exportados. O snapshot inclui seed, commit de origem, hashes das capturas e cenário. A exportação a partir das fixtures calcula MMI pelo domínio e identifica `pipeline_executed: false`: a etapa Atualizações mostra CDC no ambiente local e Não executado neste exemplo. A opção de raio está disponível no ambiente local, onde o cálculo usa PostGIS.

A correção pertence à carteira fictícia; os dados de terremoto e o grid capturado permanecem intactos. A página identifica a data do snapshot e não apresenta seus resultados como uma coleta atual.
