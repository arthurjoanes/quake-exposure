# Consulta por período e cobertura MMI

A consulta começa pela data do terremoto, pela carteira e pelo critério. O mapa e os gráficos continuam mostrando uma publicação do evento escolhido. Ativos expostos a dois terremotos não viram um total acumulado.

## Período dos eventos

De e Até incluem o dia inteiro em UTC. É possível informar só uma das datas. Os atalhos de 7 e 30 dias terminam no dia do evento mais recente do catálogo; essa data aparece ao lado dos controles. No snapshot atual, a referência é 06/10/2026, e 7 dias cobre 30/09 a 06/10.

Um recorte vazio remove a seleção e os gráficos de exposição. Datas invertidas apresentam uma orientação para corrigir o intervalo. O endereço conserva `from` e `to`, junto dos filtros existentes. Mudar o período também recorta os terremotos no mapa, sem recalcular nem somar publicações.

## Como ler a cobertura

O gráfico de intensidade destaca a fração de ativos com MMI amostrado. No evento `us7000tiur`, a Operadora B tem 1.972 de 5.000 ativos com amostra elegível (39,4%). Os 3.028 restantes ficam separados; ausência de amostra não significa intensidade zero. Os 46 classificados pertencem a essa mesma publicação, com valores em BRL e USD separados.

A carteira é fictícia. A análise não contém população, previsão de dano ou estimativa de perda financeira. Esses limites continuam no README e nos detalhes do critério.

## Interface e verificação

Mantive o mapa como foco, as cores de elegibilidade e a tipografia existente. A cobertura fica junto do histograma para dar contexto à distribuição. Barras entram em 240 ms por transformação e opacidade; `prefers-reduced-motion: reduce` desativa a animação.

Foram executados 143 testes Vitest, typecheck e build de demonstração. A validação em Edge/Playwright usou 1440 × 1050 e 390 × 844: período de 7 dias, recorte vazio, restauração pela URL, volta ao catálogo, leitura da cobertura, animação normal/reduzida e ausência de overflow. Axe WCAG A/AA não apontou violações nessa página. As imagens são capturas do navegador local, sem edição.

O build conserva o aviso de tamanho do chunk Natural Earth, que já era carregado sob demanda. Não houve coleta nova, execução de CDC, mudança de dependência, publicação de Pages ou deploy nesta revisão.
