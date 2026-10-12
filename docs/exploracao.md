# Explorar a carteira

Abra a [demonstração](https://arthurjoanes.github.io/quake-exposure/) ou o ambiente local em `http://127.0.0.1:8000`. A interface usa a mesma publicação para resumo, gráficos e ativos.

## Área no mapa

Selecione uma carteira e um terremoto. Clique em Selecionar área e marque pelo menos três pontos no mapa; Concluir área fecha o polígono. Desfazer ponto remove o último vértice e Cancelar recupera a seleção anterior.

Arraste um vértice para ajustar a área. Pelo teclado, use Tab para chegar ao mapa, as setas para movê-lo e Espaço para marcar o ponto central. Enter conclui, Backspace desfaz e Esc cancela. Um vértice focado aceita as setas para ajuste.

O resultado da área mostra ativos, elegíveis, classificados e valores em BRL e USD. A lista contém os ativos dentro do polígono, incluindo a borda. Busca, situação, moeda e paginação continuam disponíveis. Limpar área restaura a carteira inteira. O resumo e os gráficos gerais mantêm o resultado completo da publicação.

O mapa consulta o enquadramento e o zoom. A interface pede até 1.200 pontos ou grupos; a API aceita até 2.000. Os grupos conservam contagens e valores por moeda. Ampliar revela os pontos. O carregamento regional limita o desenho do mapa; as listas e os gráficos ainda usam o conjunto completo de fatos da publicação.

## Comparar publicações

Comparar no mapa abre os resumos anterior e atual, causas e posições dos ativos alterados. O contorno tracejado identifica a posição anterior; o ponto preenchido mostra a atual; uma linha liga os deslocamentos. A primeira página enquadra as mudanças no mapa. Cada página contém até 100 alterações, com navegação para as demais.

A comparação usa publicações da mesma carteira, evento, escopo e critério. Mudanças de posição, valor, moeda, intensidade ou classificação entram na lista. Se uma exclusão invalidar a versão anterior, suas posições e seus valores ficam indisponíveis.

Na demonstração inicial, `ope-00001` passa de MMI 4,0 para 6,7 após uma correção retroativa da carteira fictícia. Os classificados passam de 45 para 46. O total USD aumenta exatamente US$ 16.131,63; BRL mantém o mesmo valor. No evento das Kurilas, as duas versões da carga também permitem consultar mudanças de posição e vigência.

## Processamento

Operação mostra a versão da carteira, conector e tarefa CDC, transações pendentes, fila de cálculos e publicações. O detalhe inclui slot, WAL retido e horários persistidos. A fila e as publicações pertencem à carteira selecionada; a saúde do CDC e das fontes é compartilhada pela plataforma.

O intervalo cálculo → publicação usa a diferença entre os horários gravados. O tempo completo da origem até o navegador não é medido.

## Demonstração pública

A página pública lê arquivos REPLAY exportados. O snapshot inclui seed, commit de origem, hashes das capturas e cenário. A exportação a partir das fixtures calcula MMI pelo domínio e identifica `pipeline_executed: false`: o CDC não é executado no navegador. A opção de raio está disponível no ambiente local, onde o cálculo usa PostGIS.

A correção pertence à carteira fictícia; os dados de terremoto e o grid capturado permanecem intactos. A página identifica a data do snapshot e não apresenta seus resultados como uma coleta atual.
