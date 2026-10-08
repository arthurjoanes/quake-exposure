# 0001: escopo da primeira versão

Status: Aceito (Arthur Joanes, 2026-10-07)

## Contexto

O projeto cruza terremotos do USGS com uma carteira fictícia. Para uma máquina com 16 GiB de RAM e uma pessoa só, o escopo precisa caber em poucas peças.

## Decisão

- Kafka só no CDC da carteira. Os eventos do USGS vão do coletor direto ao PostgreSQL.
- A v0.1 sai sem CDC. O CDC real (Debezium) entra logo depois, na v0.2.
- Sem login, RLS e SSE: a API é somente leitura e escuta em 127.0.0.1.
- Fora desta versão: Dagster, Prometheus/Grafana, export Parquet, TLS/ACL entre containers, OpenTofu/Azure.
- O repositório fica em `C:\dev\quake-exposure`, fora do OneDrive.

## Consequências

- Um caminho só para os eventos (coletor → PostgreSQL) e um broker só para a carteira.
- A API não serve para exposição pública enquanto não houver login.
- Cada item da lista "fora desta versão" volta só com um ADR novo.
