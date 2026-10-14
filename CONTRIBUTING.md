# Desenvolvimento

Guia de desenvolvimento e manutenção do repositório.

## Comandos

```bash
uv sync
uv run pytest                                     # unit, property e integration (precisa do Docker)
uv run pytest tests/unit tests/property           # sem Docker
uv run ruff check .
uv run ruff format .
uv run mypy
uv run qe secrets                                 # senhas em .secrets/ (as que faltarem)
docker compose up -d --build                      # PostGIS, migrações e coletor
docker compose exec collector qe status
docker compose exec collector qe replay
docker compose exec collector qe backfill --from 2026-10-01 --to 2026-10-07
docker compose exec collector qe watch add <eventid>
docker compose up -d --build cdc                   # origem, Kafka, Connect e consumidor
docker compose run --rm --no-deps worker qe bootstrap # seed via CDC e REPLAY
docker compose exec worker qe portfolio correct --tenant <t> --interval <i> --lon <x> --lat <y>
docker compose exec worker qe demo
docker compose exec worker qe erase <subject_token>
docker compose run --rm --no-deps maintenance qe backup
docker compose run --rm --no-deps maintenance qe restore-verify /data/backups/<backup>
docker compose exec worker qe verify-demo --commit <sha-40> --output /data/backups/verify-demo.json
docker compose exec worker qe explain <eventid> --tenant <t> --mode <m> --scope <s>
docker compose exec worker qe bench --rounds 10    # precisa do worker rodando
cd web && npm ci && npm test && npm run build      # página: TS strict + Vite + Leaflet
uv run python scripts/fetch_fixtures.py pull      # grids de outras redes, fora do Git
uv run python scripts/fetch_fixtures.py capture   # recaptura tudo (requisições reais ao USGS)
```

Banco: `sql/platform/NNNN_*.sql`, aplicadas pelo `qe migrate` (serviço `migrate` do Compose, papel `qe_owner`). Migração aplicada não se edita; mudança entra numa migração nova. Papéis: `qe_owner` (migrações), `qe_pipeline` (coletor e worker), `qe_api_read` (somente leitura), criados por `docker/platform-db-init.sh` na criação do volume.

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
- Mensagens de commit registram a mudança e a autoria. Manter a redação direta da documentação e os créditos das bibliotecas e fontes.

## Domínio

Código em `domain/` é puro: sem I/O, com tempo e configuração passados explicitamente. Os testes de propriedade (Hypothesis) ficam em `tests/property/`; os construtores de dados sintéticos, em `tests/builders.py`.

Modos, nunca misturados:

- `RADIUS_TRIAGE`: ativos até 100.000 m do epicentro (`ST_DWithin` em geography). É proximidade, não intensidade. Testes a ±1 m da borda.
- `SHAKEMAP_MMI`: MMI amostrado do grid no ponto do ativo; classificado se MMI ≥ 6,0. Nunca usar o MMI máximo do resumo para todos os ativos.

Revisões de evento (`domain/events.py`, `domain/canonical.py`):

- `semantic_sha256` = SHA-256 do JSON canônico de lon, lat, time, mag, event_type e status.
- `revision_id` = SHA-256 do JSON canônico de `[provider_event_id, source_updated_at, semantic_sha256]`.
- JSON canônico: chaves ordenadas, separadores compactos, UTF-8, instantes em ISO 8601 UTC com milissegundos.
- Mesma `revision_id`: duplicata, sem efeito. Mesmo `source_updated_at` com conteúdo diferente: quarentena. `source_updated_at` mais antigo que o atual: só histórico.
- Números normalizados antes do hash (`5` e `5.0`, `-0.0` e `0.0` dão o mesmo hash); `type` vazio vira nulo. Em evento deletado o `type` é sempre nulo: o USGS manda vazio ou não para o mesmo `updated`.
- `status = deleted` vira RETRACTED e invalida as publicações do evento. Ausência no `all_day` não é retirada. 409 no detail leva a uma reconsulta por `eventid` com `includedeleted=true`.
- Sem filtro por magnitude ou tipo na ingestão; `event_type` diferente de `earthquake` sai do cálculo pela regra. `mag`, `depth`, `type` e `types` podem vir nulos ou vazios. Coordenadas sempre lon, lat.

Identidade (`domain/aliases.py`):

- `earthquake_uid` por evento. A identidade sai só dos conjuntos atuais (o `ids` da revisão mais recente de cada uid ativo), nunca do histórico.
- Mesma `revision_id` em qualquer histórico: duplicata. Mesmo id e mesmo `updated` com outro conteúdo: quarentena.
- Revisão que não é mais nova que nenhum dos uids que toca: só histórico, o mapeamento não muda.
- Revisão mais nova que todos: se o `ids` contém o id preferido de outro uid, os dois se fundem (fica o uid que já tinha o id preferido da revisão; o outro vira `merged_into` e tem as publicações invalidadas). Cada fusão ou separação grava uma linha em `event_alias_revision`.
- Quarentena e reconsulta por `eventid`: revisão que divide com outro uid um id que não é o preferido dele, ou que é mais nova que uns uids e mais velha que outros. Um id nunca fica em dois conjuntos atuais.
- O `Catalog` passado ao `ingest` precisa trazer os uids que a revisão toca, com os históricos, os históricos com a mesma `revision_id` ou o mesmo id e `updated`, e a cadeia `merged_into` deles.
- Nunca juntar eventos por distância ou tempo: réplicas são eventos distintos.

ShakeMap (`domain/shakemap.py`):

- Do detail com maior `updated`, o produto `shakemap` com maior `preferredWeight`; empate, o `updateTime` mais recente. A preferida nem sempre é a mais recente (`aka2026tuxgky`: `ak` com peso 227 contra `us` com peso 82).
- `product_revision_id` = `urn:usgs-product:<source>:shakemap:<code>:<updateTime>`.
- Evento sem ShakeMap: publicação MMI com status `NO_MAP`.

Grid (`domain/grid.py`):

- Colunas pelo nome em `grid_field`, nunca por posição.
- Linhas = nlon × nlat, com valores finitos. Cada célula deve seguir a ordem da grade: longitude crescente dentro da linha e latitude decrescente entre linhas, do canto (lon_min, lat_max) ao canto (lon_max, lat_min). As coordenadas são conferidas com tolerância de 1e-4 grau e longitude equivalente no antimeridiano. Senão, `UNSUPPORTED_GRID`.
- Passo = (max − min) / (n − 1), não o espaçamento nominal (que vem arredondado). Índice = round((x − min) / passo), só dentro da extensão. A longitude do ativo vai para o domínio do grid antes (antimeridiano).
- Fora da extensão não há valor. Nada de extrapolar nem de MMI zero.

Regra e resultado (`domain/rule.py`, `domain/money.py`, `domain/exposure.py`):

- `rule_version` é imutável: raio, limiar de MMI, tipos elegíveis, política de seleção e método de amostragem. A versão é o hash desse conteúdo, então mudar um valor gera versão nova.
- MMI: elegível = vigente em T e dentro da extensão do grid; classificado = MMI ≥ limiar (inclusivo). RADIUS: elegível = vigente em T; classificado = devolvido pelo `ST_DWithin`.
- Dinheiro: inteiro em unidade menor (`bigint`, soma em `numeric`, string no JSON, nunca float). Soma só por moeda (BRL e USD). Não somar a mesma carteira entre terremotos.
- `exposure-result.status`: COMPLETE, NO_MAP, UNSUPPORTED_GRID, RETRACTED ou REDACTED_BY_ERASURE. NO_MAP e UNSUPPORTED_GRID publicam contagens e valores nulos.

Carteira e publicação (`domain/portfolio.py`, `domain/exposure.py`):

- Vigência `[valid_from, valid_to)` sem sobreposição por ativo. As-of: versões conhecidas na epoch K e vigentes no instante T do evento, nunca o cadastro de hoje.
- `calculation_id` = SHA-256 do vetor de entrada canônico. No modo RADIUS o `product_revision_id` é `NONE`: mapa novo não recalcula o raio. O worker publica só se `desired_generation` não mudou (CAS); job velho não publica.
- `stream_scope`: LIVE, REPLAY (captura real com horário original) e FAULT (mutação de teste, sempre rotulada).
- `subject_token` é dado pessoal pseudonimizado: não vai para fatos, serving nem API.

## Coleta do USGS

Código em `ingestion/`: `http.py` (cliente), `collector.py` (agenda e tarefas), `projection.py` (captura para revisões, mapas e grids), `store.py` (SQL), `replay.py` (REPLAY e FAULT), `blobs.py` (gzip endereçado por sha256).


- Allowlist `earthquake.usgs.gov`, só HTTPS, TLS verificado, redirect é erro.
- Timeouts: conexão 3 s, leitura 20 s, FDSN 90 s.
- Limites em bytes decodificados: 10 MiB para JSON, 100 MiB para grid (o `length` declarado é checado antes), 2 milhões de pontos.
- Concorrência 2; retry em 429 e 5xx com `Retry-After` ou backoff com jitter.
- XML com `defusedxml`, DTD proibido.
- Produto baixado tem que bater com o `length` e o `sha256` (base64) declarados no detail.
- A URL do grid vem de `contents["download/grid.xml"].url`; não montar caminho.
- Falha de rede aparece como fonte desatualizada, nunca como ausência de terremotos.
- Agenda: feed a cada 60 s; detail quando o `updated` muda e o evento tem ShakeMap (ou já tinha mapa); grid só do produto escolhido de evento dos últimos 7 dias ou da watchlist; incremental FDSN a cada 600 s com `includedeleted=true`, watermark só avança se a consulta terminar, janela dividida ao meio se o FDSN recusar por excesso.
- Captura: blob gravado (temporário, fsync, rename) antes da linha em `control.capture`. O feed só vira captura se trouxer revisão nova. A projeção roda logo depois, em outra transação; erro vai para `control.quarantine` (motivo e hash, sem payload).
- 409, 404 ou 410 no detail: reconsulta por `eventid` com `includedeleted=true`.
- Conflito de alias: reconsulta uma vez, nesta ordem: o dono do id disputado, o id e a revisão. A revisão parada fica em `control.alias_conflict`; quando outra revisão solta o id, ela é reconsultada.
- Detail é sempre gravado e projetado (o mesmo detail pode valer de novo). Detail mais velho que a revisão que o pediu (cache) não fecha o pedido; conta tentativa.
- Cada feature roda num savepoint. Erro de dado vai para a quarentena do item (revision_id, ou sha256 da feature) uma vez só; conflito de versão e erro não voltam ao domínio, conflito de alias volta. Erro do ambiente (timeout, lock, deadlock, disco, conexão) deixa a captura pendente e não avança watermark nem cursor.
- Pedidos de detail e grids com falha transitória têm limite de 5 tentativas; o `qe status` mostra os desistidos.

## Cálculo e publicação

Código em `exposure/`: `jobs.py` (escopo e marcação), `worker.py` (cálculo e CAS), `explain.py`. Carteira em `portfolio/`. API em `api/`, página em `web/`.

- Escopo: LIVE calcula eventos com ShakeMap dos últimos 7 dias e a watchlist; REPLAY e FAULT, todos. Evento de tipo fora da regra é ingerido sem cálculo.
- O marcador (`jobs.mark`) calcula o vetor de entrada de cada (tenant, evento, escopo, modo) e sobe `desired_generation` quando o `calculation_id` desejado muda. MMI sem detail ainda, ou com mapa escolhido e grid ainda não baixado, fica sem job até chegar.
- A projeção sobe `desired_generation` dos jobs do evento na mesma transação em que grava a revisão ou os produtos (`jobs.touch`). É isso que faz o CAS recusar um cálculo em andamento. Quem mudar entrada de cálculo (no M4, o CDC da carteira) faz o mesmo.
- Job cujo vetor já não bate com o estado é acertado pelo worker na hora (vetor novo e geração nova, ou em dia se não há vetor). Por isso evento com job acompanha retirada e mapa que some mesmo fora do escopo.
- O worker pega o job com `FOR UPDATE SKIP LOCKED` e uma lease de 2 min, carrega o grid antes da transação e faz tudo numa transação: as-of da carteira (domínio), `ST_DWithin` em geography (RADIUS) ou amostragem do grid (MMI), fatos, resumo por moeda, CAS em `desired_generation` e troca de `serving.current_publication`. Geração mudou durante o cálculo: nada é publicado e o job volta para a fila.
- `calculation_id` = `publication_id`. A mesma entrada não gera cálculo nem publicação nova.
- `/api/events` traz primeiro os eventos com publicação; o limite (até 2.000) corta só os sem cálculo.
- Evento retirado publica RETRACTED; fundido ou fora da regra sai de `serving.current_publication` (linha nula em `serving.publication_log`).
- Fatos e serving nunca têm `subject_token`; o papel `qe_api_read` não lê `portfolio.asset_revision`.
- Página: mapa, gráficos, listas e valores sempre da mesma publicação; dinheiro só como string, agregação monetária em BigInt. Filtros e paginação da lista não alteram o total dos gráficos.
- Mapa: Canvas, agrupamentos por zoom e cache por publicação; polling sem mudança não recria camadas. OpenStreetMap para ruas e lugares, Natural Earth 1:50m como alternativa offline. Manter atribuição visível e não fazer pré-carga de tiles.
- Identidade visual: ativos SVG em `web/public/brand/`, apresentação em `docs/assets/`; regras de uso em `docs/identidade-visual.md`. Cores de mapa, gráficos e legenda devem corresponder.
- Exploração: área e resumo global têm denominadores próprios; região limita o desenho do mapa. Comparações históricas passam pela mesma guarda de privacidade. O snapshot público aceita somente REPLAY e identifica o CDC não executado quando calculado a partir de fixtures.

## CDC da carteira

- `portfolio/source.py` escreve na origem por funções SECURITY DEFINER; o writer não faz DML direto. Cada comando tem UUID idempotente, trava o clock do tenant e avança uma epoch.
- `source_epoch` é inteiro. Origem carregada parcialmente exige reset manual; `ops.seed_status()` distingue seed terminado de interrupção entre lotes.
- Debezium PostgreSQL 3.6.2, `pgoutput`, `snapshot.mode=no_data`, publicação manual de `public.asset_interval` e `public.portfolio_clock`. Contatos e deduplicação ficam fora.
- `cdc/store.py`: stage por transação e ordem total; END pode chegar antes das linhas. Só aplica com contagens exatas e epoch seguinte. Sem clock ou `op=r`: erro explícito.
- O ID bruto Debezium traz XID:LSN, com LSN diferente por linha. A chave interna usa XID e timestamp de commit (`source.ts_ms` = END `ts_ms`), conferidos no smoke real.
- Offset Kafka só é confirmado depois dos commits de stage e aplicação. Reentrega não avança epoch nem cria publicação nova. Payload aplicado é descartado, a chave e o hash ficam.
- `control.cdc_stage` e `privacy` não são legíveis pela API. `cdc/monitor.py` consulta Connect e WAL do slot, sem expor dados da carteira.
- O loader `portfolio.store.load` é só para testes. A CLI semeia pela origem.

## Exclusão e restauração

- `privacy/erasure.py`: bloqueio na origem, ledger com fsync, BLOCKED na plataforma, purge e recibo CLOSED. Cada fase pode ser retomada sem subir outra privacy_epoch.
- O ledger fica em volume separado dos bancos. Guarda identificadores mínimos para reaplicar bloqueios; não armazena contato, localização ou valor.
- A aplicação CDC e a exclusão compartilham o lock `cdc-apply`. Payload de titular bloqueado é sanitizado antes de entrar no stage; exclusão sanitiza também o stage pendente.
- A API mantém uma transação por requisição e lock compartilhado por tenant. O bloqueio de exclusão usa o lock exclusivo correspondente. Publicação de privacy_epoch antiga responde 409.
- O worker filtra titulares bloqueados e verifica privacy_epoch no CAS. Exclusão invalida todos os jobs do tenant, inclusive fora da janela LIVE.
- A página só aceita respostas do ciclo de atualização mais recente; um erro 409 atrasado não limpa uma seleção nova.
- `privacy/backup.py` usa pg_dump/pg_restore 18. `restore-verify` verifica os hashes e reaplica o ledger atual em `qe_restore`, sem CONNECT para a API. Nunca substitui um banco sem sua marca de verificação.
- Restore e exclusão serializam o acesso ao ledger pelo lock `erase-ledger`. Dump e WAL podem manter resíduos; o recibo registra essa limitação e a retenção Kafka de sete dias.

## Verificação final

- `qe verify-demo` valida os hashes das capturas, executa os cinco passos e grava commit, seed e asserções em JSON. O coletor deve ficar parado nessa execução.
- CI: unitários, propriedades, contratos, PostGIS como service, integração Compose, frontend e auditorias. `QE_TEST_EXTERNAL_DATABASE=1` usa o service PostgreSQL descartável do CI; não aponta para banco de uso real.
- O runbook está em `docs/operacao.md`; medições e resultados em `docs/results/`. Não apresentar workflow ainda não executado no GitHub como aprovado remotamente.

