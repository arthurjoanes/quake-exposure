# QuakeExposure

Cruza terremotos do USGS e os mapas de intensidade deles (ShakeMap) com uma carteira fictícia de ativos e mostra quais ativos ficaram expostos, com o valor por moeda, e por que a lista mudou entre uma revisão e outra. O foco é engenharia de dados: revisões de evento sem dupla contagem, carteira temporal, publicação atômica, CDC e replay.

Dois modos, nunca misturados: RADIUS_TRIAGE (ativos até 100 km do epicentro; proximidade, não intensidade) e SHAKEMAP_MMI (MMI do grid no ponto do ativo; classificado com MMI ≥ 6,0). São regras de triagem do projeto. Não é previsão, alerta nem modelo de dano ou perda; o USGS já publica ShakeMap e ShakeCast.

Estado: v0.1. Há coleta do USGS, cálculo e publicação de exposição, API somente leitura e a página. O CDC da carteira e a exclusão de titular vêm depois. O andamento está em [docs/progress.md](docs/progress.md).

## Rodar

Precisa do [uv](https://docs.astral.sh/uv/) e do Docker com Compose. O Python vem do próprio uv (`.python-version`).

```bash
uv sync
uv run qe secrets
docker compose up -d --build
docker compose exec worker qe portfolio seed
```

O `qe secrets` cria as senhas em `.secrets/` (fora do Git). O `docker compose up` sobe o PostGIS, aplica as migrações e liga o coletor (feed a cada 60 s, FDSN a cada 10 min), o worker e a API. O `portfolio seed` carrega a carteira fictícia: 10.000 ativos em 2 tenants, BRL e USD, a maior parte em volta dos eventos de demo.

A página fica em http://127.0.0.1:8000. A API não tem login e só escuta em 127.0.0.1; o tenant é filtro, não autorização.

Outros comandos, dentro do container:

```bash
docker compose exec worker qe demo
docker compose exec worker qe explain us7000tiur --tenant seguradora-a --mode SHAKEMAP_MMI --scope REPLAY
docker compose exec worker qe bench --rounds 10
docker compose exec collector qe status
docker compose exec collector qe backfill --from 2026-10-01 --to 2026-10-07
docker compose exec collector qe watch add us7000tiur
```

- `demo`: replay das capturas reais e cálculo inicial; revisão FAULT que muda a exposição e a mesma captura de novo sem efeito; cálculo velho recusado pela geração.
- `explain`: o vetor de entrada da publicação atual e os ativos que entraram e saíram desde a anterior, com a causa.
- `bench`: tempo de revisão aplicada até publicação. O resultado medido está em [docs/results/](docs/results/).

Grids de redes diferentes de `us` não vão para o Git. Para baixá-los da fonte e conferir o sha256 (antes do `--build`, para entrarem no replay):

```bash
uv run python scripts/fetch_fixtures.py pull
```

## Testar

```bash
uv run pytest
uv run ruff check .
uv run mypy
cd web && npm ci && npm test && npm run build
```

Os testes não acessam a rede. Os de `tests/integration/` sobem um PostGIS num projeto Compose próprio e derrubam no fim; sem Docker eles são pulados.

## Limitações

- A carteira é fictícia. Os dados de terremoto são reais.
- O polling não vê toda revisão intermediária do USGS, só as que estavam publicadas quando o feed foi lido.
- Sem login nem TLS entre containers. A API serve só em 127.0.0.1.
- O LIVE calcula só eventos com ShakeMap dos últimos 7 dias e os da watchlist; os outros são ingeridos sem cálculo.
- A carteira entra por carga direta; o CDC da origem ainda não existe.

## Dados e licença

Código sob MIT. Os dados são do U.S. Geological Survey (domínio público). Créditos em [NOTICE](NOTICE). O mapa-base da página é o Natural Earth (domínio público), via `world-atlas`.
