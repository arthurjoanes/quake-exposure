# Desenvolvimento

Guia de desenvolvimento e manutenção do repositório.

## Comandos

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run mypy
uv run qe --version
uv run python scripts/fetch_fixtures.py pull      # grids de outras redes, fora do Git
uv run python scripts/fetch_fixtures.py capture   # recaptura tudo (requisições reais ao USGS)
```

Não há Makefile. Comando novo entra como `uv run qe ...` ou `docker compose ...` e é documentado aqui e no README.

## Regras do repositório

- Git: commits em inglês, no formato Conventional Commits. Push só com o OK do Arthur.
- Rede: chamada ao USGS só pelo `UsgsClient` (`src/quakeexposure/ingestion/http.py`). Teste automatizado não acessa a rede externa; o `tests/conftest.py` bloqueia conexão fora do loopback.
- Testes: não enfraquecer teste para ficar verde. Oráculo independente nunca chama a função testada. Relógio injetado, sem `sleep` fixo para sincronizar. Falha injetada só no perfil de teste (failpoint ou servidor HTTP local). Teste de gate leva o F-ID no nome, por exemplo `test_f14_...`.
- Fixtures: captura real não é editada. Mutação de teste é FAULT e fica rotulada no manifesto (`fault: true` e `parent_sha256`). Grids de redes diferentes de `us` não vão para o Git, só URL e sha256 no manifesto.
- Docker: dados só em volumes nomeados, sem bind mount de dados. Senhas em `.secrets/`, lidas por variáveis `*_FILE`.
- Stack: Python com uv, httpx, psycopg 3, jsonschema, defusedxml, FastAPI, confluent-kafka (CDC), pytest, Hypothesis e geographiclib (só oráculo de teste); Ruff e mypy strict; PostgreSQL + PostGIS, Kafka KRaft e Kafka Connect com Debezium PostgreSQL; frontend TypeScript strict + Vite + Leaflet com npm. Versões fixadas em lockfile e por tag de imagem, nunca `latest`. Ferramenta fora dessa lista só com ADR aceito em `docs/adr/`.
- Texto de README, docs e interface: pt-BR, curto e seco. Sem "evidência", "prova", "ensaio", "atestar", "assegurar", negrito no meio de parágrafo, slogan ou aviso defensivo. Código e identificadores em inglês.
- `docs/progress.md` traz o estado real de cada milestone.

## Domínio

Código em `domain/` é puro: sem I/O, com tempo e configuração passados explicitamente.

Modos, nunca misturados:

- `RADIUS_TRIAGE`: ativos até 100.000 m do epicentro (`ST_DWithin` em geography). É proximidade, não intensidade. Testes a ±1 m da borda.
- `SHAKEMAP_MMI`: MMI amostrado do grid no ponto do ativo; classificado se MMI ≥ 6,0. Nunca usar o MMI máximo do resumo para todos os ativos.

Revisões de evento:

- `semantic_sha256` = SHA-256 do JSON canônico de lon, lat, time, mag, event_type e status.
- `revision_id` = SHA-256 do JSON canônico de `[provider_event_id, source_updated_at, semantic_sha256]`.
- JSON canônico: chaves ordenadas, separadores compactos, UTF-8, instantes em ISO 8601 UTC com milissegundos.
- Mesma `revision_id`: duplicata, sem efeito. Mesmo `source_updated_at` com `semantic_sha256` diferente: quarentena. `source_updated_at` mais antigo que o atual: só histórico.
- `status = deleted` vira RETRACTED e invalida as publicações do evento. Ausência no `all_day` não é retirada. 409 no detail leva a uma reconsulta por `eventid` com `includedeleted=true`.
- Sem filtro por magnitude ou tipo na ingestão; `event_type` diferente de `earthquake` sai do cálculo pela regra. `mag`, `depth`, `type` e `types` podem vir nulos ou vazios. Coordenadas sempre lon, lat.

Identidade:

- `earthquake_uid` por evento, seguindo o `ids` da revisão mais recente.
- Dois uids que passam a dividir ids: fica o do id preferido, o outro vira `merged_into` e tem as publicações invalidadas. Cada fusão ou separação grava uma linha em `event_alias_revision`.
- Nunca juntar eventos por distância ou tempo: réplicas são eventos distintos.

ShakeMap (`domain/shakemap.py`):

- Do detail com maior `updated`, o produto `shakemap` com maior `preferredWeight`; empate, o `updateTime` mais recente. A preferida nem sempre é a mais recente (`aka2026tuxgky`: `ak` com peso 227 contra `us` com peso 82).
- `product_revision_id` = `urn:usgs-product:<source>:shakemap:<code>:<updateTime>`.
- Evento sem ShakeMap: publicação MMI com status `NO_MAP`.

Grid (`domain/grid.py`):

- Colunas pelo nome em `grid_field`, nunca por posição.
- Linhas = nlon × nlat; primeira em (lon_min, lat_max); última em (lon_max, lat_min); valores finitos. Senão, `UNSUPPORTED_GRID`.
- Passo = (max − min) / (n − 1), não o espaçamento nominal (que vem arredondado). Índice = round((x − min) / passo), só dentro da extensão. A longitude do ativo vai para o domínio do grid antes (antimeridiano).
- Fora da extensão não há valor. Nada de extrapolar nem de MMI zero.

Regra e resultado:

- `rule_version` é imutável: raio, limiar de MMI, tipos elegíveis, política de seleção e método de amostragem. Mudou um valor, é versão nova.
- Dinheiro: inteiro em unidade menor (`bigint`, soma em `numeric`, string no JSON, nunca float). Soma só por moeda (BRL e USD). Não somar a mesma carteira entre terremotos.
- `exposure-result.status`: COMPLETE, NO_MAP, UNSUPPORTED_GRID, RETRACTED ou REDACTED_BY_ERASURE. NO_MAP e UNSUPPORTED_GRID publicam contagens e valores nulos.

Carteira e publicação:

- Vigência `[valid_from, valid_to)` sem sobreposição por ativo. As-of: versões conhecidas na epoch K e vigentes no instante T do evento, nunca o cadastro de hoje.
- `calculation_id` = SHA-256 do vetor de entrada canônico. O worker publica só se `desired_generation` não mudou (CAS); job velho não publica.
- `stream_scope`: LIVE, REPLAY (captura real com horário original) e FAULT (mutação de teste, sempre rotulada).
- `subject_token` é dado pessoal pseudonimizado: não vai para fatos, serving nem API.

## Coleta do USGS

- Allowlist `earthquake.usgs.gov`, só HTTPS, TLS verificado, redirect é erro.
- Timeouts: conexão 3 s, leitura 20 s, FDSN 90 s.
- Limites em bytes decodificados: 10 MiB para JSON, 100 MiB para grid (o `length` declarado é checado antes), 2 milhões de pontos.
- Concorrência 2; retry em 429 e 5xx com `Retry-After` ou backoff com jitter.
- XML com `defusedxml`, DTD proibido.
- Produto baixado tem que bater com o `length` e o `sha256` (base64) declarados no detail.
- A URL do grid vem de `contents["download/grid.xml"].url`; não montar caminho.
- Falha de rede aparece como fonte desatualizada, nunca como ausência de terremotos.
