# Referências das melhorias

Consultadas em 11/10/2026. As adaptações usam a stack do QuakeExposure; os projetos abaixo servem como referências de interface e arquitetura.

| Projeto | Recurso consultado | Aplicação no QuakeExposure |
| --- | --- | --- |
| [USGS Earthquake Map](https://github.com/pixelpawnshop/usgs_earthquake_map) | Camadas, filtros e seleção por polígonos para contar edifícios | Área selecionável com ativos e valores por moeda; listas preservadas |
| [EarthquakePH](https://github.com/jpvitan/earthquakeph) | Demonstração acessível pelo navegador, telas de mapa, estatísticas e histórico | Demonstração REPLAY no GitHub Pages e percurso de consulta |
| [Kafbat UI](https://github.com/kafbat/kafka-ui) | Estado de conectores, tópicos, consumidores e atraso de consumo | Painel do CDC, fila de cálculos e horários de publicação, usando métricas disponíveis |
| [TiMVT](https://github.com/developmentseed/timvt) | Consulta geográfica e tiles servidos pelo PostGIS/FastAPI | Carregamento por região e zoom, com pontos ou agregações limitadas em JSON |
| [PostGIS Kafka CDC, 52°North](https://github.com/52North/postgis-kafka-cdc) | Integração entre PostGIS, Debezium e Kafka | Fluxo visível da carteira ao resultado e comparação de posições entre publicações |

TiMVT e o exemplo da 52°North estão arquivados. O README do TiMVT aponta o [TiPG](https://github.com/developmentseed/tipg) como continuação. Não foram copiadas configurações antigas nem adicionadas dependências desses projetos.

Os rótulos, o desenho dos controles e o código das melhorias pertencem ao QuakeExposure. Fontes e licenças dos dados e símbolos permanecem em [NOTICE](../NOTICE) e [stack/README.md](stack/README.md).
