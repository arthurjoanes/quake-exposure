# ADR 0005: exploração regional e demonstração REPLAY

Status: aceito, 2026-10-11.

## Contexto

O painel precisava permitir seleção geográfica, comparação de posições e leitura do processamento. Uma demonstração no navegador também facilita consultar o projeto sem subir a stack.

## Decisão

Manter TypeScript, Leaflet e FastAPI. A consulta regional usa limites de longitude, latitude e zoom; devolve pontos ou células agregadas, com no máximo 2.000 elementos. Um índice novo em `exposure.fact` atende cálculo e coordenadas. Contagens e somas usam os fatos completos dentro da região; os valores monetários são strings inteiras.

A seleção por polígono usa a projeção Web Mercator do mapa, com borda inclusiva e tratamento do antimeridiano. O resultado da área fica separado do resumo da carteira. Listas e gráficos continuam lendo todos os fatos da publicação.

A comparação consulta a publicação anterior da mesma chave e pagina os ativos alterados. A guarda de tenant e o lock de privacidade abrangem as leituras. Uma versão anterior invalidada por exclusão não devolve posições nem valores.

O painel de operação lê estados existentes, epochs, filas e datas persistidas. Métricas de duração sem registro ficam indisponíveis.

A demonstração pública usa o mesmo frontend, compilado com `VITE_DEMO=1`, e um adaptador de leitura dos arquivos exportados. GitHub Pages hospeda o build. O exportador permite somente REPLAY e uma lista explícita de campos públicos. A geração a partir de fixtures usa o domínio, seed 42 e grids originais; identifica o CDC como não executado. A correção do exemplo altera apenas a carteira fictícia.

## Consequências

O mapa desenha um número limitado de elementos por consulta. O carregamento das listas ainda cresce com o total de ativos da publicação. Tiles vetoriais podem ser avaliados quando esse limite for medido como gargalo.

A página pública é um snapshot MMI, com data e origem. Operação ao vivo e o critério de raio dependem da stack local. Uma nova publicação estática requer exportação, build e deploy.
