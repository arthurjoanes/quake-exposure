# QuakeExposure

Combines real USGS earthquakes and ShakeMap grids with a fictional portfolio of 10,000 assets across two tenants. Shows classified assets, registered values by currency, and why exposure changed. Includes revision deduplication, a bitemporal portfolio, PostgreSQL → Debezium → Kafka CDC, atomic publication, and erasure that survives replay.

RADIUS_TRIAGE uses a distance of up to 100 km. SHAKEMAP_MMI samples the grid at each asset and classifies MMI ≥ 6.0. These are project triage rules, not earthquake predictions, alerts, damage estimates, or losses. USGS already provides ShakeMap and ShakeCast.

Local version: v0.3. [Português](README.md) · [Operations, pt-BR](docs/operacao.md) · [Progress](docs/progress.md) · [Measured results](docs/results/)

## Run

Requires Docker Desktop/Compose, uv, and Node.js for frontend development. uv installs the pinned Python 3.14 runtime. On Windows, use `python -m uv` if uv is missing from PATH.

```bash
uv sync --frozen
uv run qe secrets
docker compose up -d --build cdc
docker compose run --rm --no-deps worker qe bootstrap
docker compose up -d worker api
```

Bootstrap waits for the replication slot, seeds the source through commands, waits for CDC, and imports captured USGS data as REPLAY. It makes no USGS requests. Completed bootstrap can be resumed; a partial seed requires manual reset. Keep older directly loaded portfolios in a separate Compose project.

Open [127.0.0.1:8000](http://127.0.0.1:8000), select REPLAY and `us7000tiur`. The read-only API has no authentication; tenant is a filter. Only port 8000 is published, on loopback.

The development instance created for this delivery uses `docker compose -p quake-exposure-v03`. The v0.1 volumes were preserved.

```bash
docker compose exec worker qe demo
docker compose exec worker qe status --json
docker compose exec worker qe explain us7000tiur --tenant seguradora-a --mode SHAKEMAP_MMI --scope REPLAY
docker compose exec worker qe portfolio correct --tenant seguradora-a --interval "seg-00001#1" --lon 120.5 --lat -8.2
docker compose exec worker qe erase <subject_token>
docker compose run --rm maintenance qe backup
docker compose run --rm maintenance qe restore-verify /data/backups/<backup>
```

The five-step demo replays real captures, applies a labelled FAULT revision and duplicate, corrects the portfolio through CDC with an as-of comparison, rejects an outdated calculation, and erases one subject before replaying old CDC. Each run changes the fictional portfolio and erases one asset.

Run `qe verify-demo --commit <git-rev-parse-HEAD> --output /data/backups/verify-demo.json` in the worker to record assertions. Copy the result into `docs/results/` with `docker compose cp worker:/data/backups/verify-demo.json docs/results/verify-demo.json`.

Live collection is explicit:

```bash
docker compose up -d collector
docker compose exec collector qe backfill --from 2026-10-01 --to 2026-10-07
docker compose exec worker qe watch add us7000tiur
```

Grids from contributing networks other than `us` are excluded from Git. `uv run python scripts/fetch_fixtures.py pull` downloads them through the collection client and checks their hashes. They are optional for the main demo.

## Test

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

Tests only use local networking. Integration tests start real databases, Kafka, and Debezium in disposable projects; they are skipped when Docker is unavailable. CI includes unit/property/contract tests, a PostGIS service job, full integration, frontend checks, gitleaks, pip-audit, and npm audit. The workflow is ready for GitHub; it has not run remotely.

## Limitations

- Fictional portfolio; BRL and USD are never converted or combined. Money uses integers and JSON strings. Do not add the same portfolio across earthquakes.
- Polling captures observed USGS revisions, not every intermediate revision.
- LIVE calculates recent ShakeMap events from the last seven days and watchlisted events. REPLAY and FAULT remain separate and labelled.
- No authentication or TLS between containers. Intended for local use on 127.0.0.1.
- Lost slots, expired offsets, and partial seeds require manual reset. No high availability.
- Kafka messages may remain for seven days. WAL and dump residuals are listed in the erasure receipt. Restore reapplies the current ledger, stored outside database volumes.
- Erasure invalidates the tenant's publications until recalculation. Old CDC cannot return blocked subjects to calculations.

## Data and license

MIT, Arthur Joanes. U.S. Geological Survey public-domain data; ComCat DOI 10.5066/F7MS3QZH. Natural Earth basemap via world-atlas. See [NOTICE](NOTICE).
