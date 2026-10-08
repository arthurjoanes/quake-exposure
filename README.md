# QuakeExposure

Cruza terremotos do USGS e os mapas de intensidade deles (ShakeMap) com uma carteira fictícia de ativos e mostra quais ativos ficaram expostos. O foco é engenharia de dados: revisões de evento sem dupla contagem, carteira temporal, CDC e replay.

Não é previsão, alerta nem modelo de dano ou perda. O USGS já publica ShakeMap e ShakeCast.

Estado: há o coletor do USGS (feed, details, grids e incremental FDSN) gravando em PostgreSQL + PostGIS, o replay das capturas reais e o domínio em código puro. Ainda não há cálculo de exposição, API nem página. O andamento está em [docs/progress.md](docs/progress.md).

## Rodar

Precisa do [uv](https://docs.astral.sh/uv/) e do Docker com Compose. O Python vem do próprio uv (`.python-version`).

```bash
uv sync
uv run qe secrets
docker compose up -d --build
docker compose exec collector qe status
```

O `qe secrets` cria as senhas em `.secrets/` (fora do Git). O `docker compose up` sobe o PostGIS, aplica as migrações e liga o coletor, que lê o feed a cada 60 s e o FDSN a cada 10 min. O banco não publica porta; os comandos rodam dentro do container:

```bash
docker compose exec collector qe replay
docker compose exec collector qe backfill --from 2026-10-01 --to 2026-10-07
docker compose exec collector qe watch add us7000tiur
```

O `replay` grava as capturas de `tests/fixtures/` como REPLAY, separadas do LIVE. O `backfill` busca no FDSN um dia por vez e não repete dia já feito. O `watch add` busca o detail do evento e, se ele tiver ShakeMap, o grid, mesmo fora da janela de 7 dias.

Grids de redes diferentes de `us` não vão para o Git. Para baixá-los da fonte e conferir o sha256 (antes do `--build`, para entrarem no replay):

```bash
uv run python scripts/fetch_fixtures.py pull
```

## Testar

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

Os testes não acessam a rede. Os de `tests/integration/` sobem um PostGIS num projeto Compose próprio e derrubam no fim; sem Docker eles são pulados. Sem o `pull`, os testes do grid `ak` também são pulados.

## Limitações

- A carteira é fictícia. Os dados de terremoto são reais.
- O polling não vê toda revisão intermediária do USGS, só as que estavam publicadas quando o feed foi lido.
- Banco e coletor sem login nem TLS entre containers; nada publica porta ainda.

## Dados e licença

Código sob MIT. Os dados são do U.S. Geological Survey (domínio público). Créditos em [NOTICE](NOTICE).
