# QuakeExposure

Cruza terremotos reais do USGS e grids ShakeMap com uma carteira fictícia de 10.000 ativos em dois tenants. Mostra ativos classificados, valores por moeda e por que o resultado mudou. Inclui revisões sem dupla contagem, carteira bitemporal, CDC PostgreSQL → Debezium → Kafka, publicação transacional e exclusão de titular contra replay.

RADIUS_TRIAGE usa distância de até 100 km. SHAKEMAP_MMI amostra o grid no ponto do ativo e classifica MMI ≥ 6,0. São critérios de triagem; não são previsão, alerta nem estimativa de dano ou perda. O USGS já oferece ShakeMap e ShakeCast.

Versão local: v0.3. [English](README.en.md) · [Operação](docs/operacao.md) · [Progresso](docs/progress.md) · [Resultados medidos](docs/results/)

## Rodar

Requer Docker Desktop/Compose, uv e Node.js para desenvolver a página. O uv instala o Python 3.14 fixado no projeto. No Windows, `python -m uv` funciona quando `uv` não está no PATH.

```bash
uv sync --frozen
uv run qe secrets
docker compose up -d --build cdc
docker compose run --rm --no-deps worker qe bootstrap
docker compose up -d worker api
```

O bootstrap espera o slot ativo, grava a carteira por comandos na origem, aguarda o CDC e carrega as capturas reais como REPLAY. Não consulta o USGS. Uma carga completa pode ser retomada; uma carga parcial exige reset manual. Carteiras do loader antigo devem ficar em outro projeto Compose.

Página: [127.0.0.1:8000](http://127.0.0.1:8000). Selecione REPLAY e o evento `us7000tiur`. A API somente leitura não tem login; tenant é filtro. Apenas a porta 8000 é publicada, no loopback.

A instância de desenvolvimento criada nesta entrega usa `docker compose -p quake-exposure-v03` nos comandos. Os volumes da v0.1 foram preservados.

```bash
docker compose exec worker qe demo
docker compose exec worker qe status --json
docker compose exec worker qe explain us7000tiur --tenant seguradora-a --mode SHAKEMAP_MMI --scope REPLAY
docker compose exec worker qe portfolio correct --tenant seguradora-a --interval "seg-00001#1" --lon 120.5 --lat -8.2
docker compose exec worker qe erase <subject_token>
docker compose run --rm maintenance qe backup
docker compose run --rm maintenance qe restore-verify /data/backups/<backup>
```

A demo executa cinco passos: replay real; revisão FAULT e duplicata; correção da carteira via CDC com comparação as-of; cálculo antigo recusado; exclusão com reentrega CDC antiga e recibo. Cada execução altera a carteira fictícia e exclui um ativo.

Para gravar as asserções, use `qe verify-demo --commit <git-rev-parse-HEAD> --output /data/backups/verify-demo.json` no worker. Copie o resultado para `docs/results/` com `docker compose cp worker:/data/backups/verify-demo.json docs/results/verify-demo.json`.

Coleta ao vivo é explícita:

```bash
docker compose up -d collector
docker compose exec collector qe backfill --from 2026-10-01 --to 2026-10-07
docker compose exec worker qe watch add us7000tiur
```

Grids de redes diferentes de `us` não vão ao Git. `uv run python scripts/fetch_fixtures.py pull` baixa esses arquivos pelo cliente de coleta e confere seus hashes. Eles são opcionais para a demo principal.

## Testar

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
cd web
npm ci
npm test
npm run typecheck
npm run build
```

Os testes usam apenas a rede local. Integrações sobem bancos, Kafka e Debezium reais em projetos descartáveis. Sem Docker, essas integrações são puladas. O CI inclui unitários, propriedades, contratos, PostGIS como service, integração completa, frontend, gitleaks, pip-audit e npm audit. O workflow está pronto para o GitHub; nenhuma execução remota foi feita.

## Limitações

- Carteira fictícia, BRL e USD sem conversão. Valores monetários são inteiros e strings no JSON; não se somam carteiras entre terremotos.
- Polling registra as revisões observadas, não todas as revisões intermediárias do USGS.
- LIVE calcula eventos com ShakeMap dos últimos sete dias e watchlist. REPLAY e FAULT são separados e rotulados.
- Sem autenticação e sem TLS entre containers. Uso local em 127.0.0.1.
- Slot perdido, offset expirado ou seed parcial exigem reset manual. Não há alta disponibilidade.
- Kafka pode manter mensagens por sete dias; WAL e dumps têm resíduos descritos no recibo. A restauração reaplica o ledger atual, guardado fora do volume dos bancos.
- A exclusão invalida publicações do tenant até o recálculo. Dados de um titular bloqueado não voltam ao cálculo por CDC antigo.

## Dados e licença

Código MIT, Arthur Joanes. Dados do U.S. Geological Survey, domínio público; ComCat DOI 10.5066/F7MS3QZH. Mapa-base Natural Earth, via world-atlas. Créditos em [NOTICE](NOTICE).
