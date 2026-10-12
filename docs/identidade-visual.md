# Identidade visual

A marca reúne um Q e duas ondas sísmicas interrompidas. O ponto coral indica o epicentro; a abertura dá espaço à cauda do Q. O desenho é vetorial e não depende de imagens externas.

## Arquivos

- [Símbolo](../web/public/brand/quake-exposure-mark.svg): ícone da página e favicon, com fundo próprio.
- [Marca horizontal](../web/public/brand/wordmark.svg): símbolo e nome, para fundos claros.
- [Banner](assets/banner.svg): apresentação do repositório, em 1.280 × 360 pixels.

O símbolo usa uma grade de 64 × 64. Na interface, usar 36 pixels ou mais; no favicon, 16 pixels. Preservar a proporção e uma margem livre de pelo menos um quarto da largura do símbolo. Não acrescentar sombras, gradientes ou contornos.

## Cores

| Cor | Hex | Uso |
| --- | --- | --- |
| Marinho | `#14344B` | Marca, títulos e texto principal |
| Petróleo | `#087F8C` | Ativos elegíveis e ações |
| Coral | `#BF4B35` | Ativos classificados e epicentro da marca |
| Ardósia | `#A7B8C0` | Ativos não elegíveis e contornos |
| Violeta | `#67549A` | Eventos no mapa |
| Gelo | `#EFF5F6` | Fundo da página e traço da marca |
| Branco | `#FFFFFF` | Superfícies de leitura |

Cor acompanha rótulos, contagens e legendas. Valores de moedas diferentes ficam separados. Coral indica classificação pela regra selecionada; não representa uma estimativa de perda.

## Tipografia e composição

Bahnschrift, com Trebuchet MS e sans-serif como alternativas, no nome e nos títulos. Aptos, Segoe UI e sans-serif no texto e nos controles. As fontes vêm do sistema, sem requisições externas.

O nome QuakeExposure mantém uma só cor. Títulos e conteúdo ficam alinhados à esquerda. O mapa ocupa a área principal, com resumo ao lado; resultado da área e comparação ficam próximos ao mapa, antes dos gráficos. As listas permanecem visíveis. Operação começa recolhida. As ondas ficam restritas à marca e ao banner.

## Navegação e controles

Os links Mapa, Gráficos, Ativos e Operação seguem a ordem da página. No celular, os filtros podem ser recolhidos para deixar o mapa acessível. REPLAY e a data do snapshot aparecem no cabeçalho. O contexto da captura fica na identificação expansível.

As ações do mapa usam ícones SVG e rótulos curtos: Evento, Carteira, Área e Comparar. `aria-label` e `title` mantêm o nome completo da ação. O enquadramento passa de Carteira para Área quando há recorte. Concluir, Desfazer e Cancelar aparecem durante o desenho. A quantidade mínima de vértices define quando Concluir fica disponível.

Foco de teclado deve permanecer visível. Controles possuem rótulos próprios; ícones e cores acompanham texto. A movimentação da página respeita a preferência por movimento reduzido.

## Leitura dos resultados

Na comparação, Anterior e Atual têm a mesma estrutura: situação, contagens e valores por moeda. A variação usa sinal e moeda. Um resultado indisponível permanece identificado como indisponível. Versões e identificadores ficam em Dados das publicações. Causas e alterações usam rótulos de domínio, como Versão da carteira, Posição e Entrou no critério.

O contorno tracejado marca a posição anterior; o ponto preenchido marca a atual. Uma linha indica deslocamento. A tabela mantém o estado de classificação em texto junto às coordenadas. Abrir a comparação desloca o foco para o painel; Ver no mapa enquadra os ativos. O cabeçalho da comparação identifica seu contexto de carteira inteira. Como ler o mapa reúne a legenda de comparação.

Gráficos ocupam a leitura principal; explicações de critérios, revisões e detalhes do evento ficam em controles expansíveis. Operação usa quatro etapas numeradas e abre pela navegação. Pendências, falhas e estado capturado recebem rótulos distintos. O snapshot público identifica o CDC não executado. Identificadores, estado bruto do conector e WAL ficam em detalhes recolhidos.

Carregamento, resultado vazio e falha ocupam o painel correspondente. Falhas de análise e comparação oferecem Tentar novamente; a comparação pode ser fechada mesmo durante a consulta. Mensagens de estado usam texto curto e descrevem a ação disponível.
