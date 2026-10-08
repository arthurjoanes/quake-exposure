# Progresso

## M0: base e fixtures reais (2026-10-07)

Concluído.

Feito:

- Projeto Python 3.14 com uv, Ruff, mypy strict e pytest. A CLI `qe` só tem `--version` por enquanto.
- `ingestion/http.py`: cliente do USGS com allowlist, só HTTPS, redirect como erro, timeouts de 3/20/90 s, limites em bytes decodificados (gzip aberto com saída limitada), concorrência 2, retry com `Retry-After` ou backoff com jitter, conferência do `length` e do `sha256` declarados.
- `domain/grid.py`: parser do `grid.xml` com `defusedxml` (DTD proibido), colunas pelo nome, validação de linhas, cantos e valores finitos, amostragem do nó mais próximo com antimeridiano.
- `domain/shakemap.py`: seleção da ShakeMap por `preferredWeight` e `updateTime`, e o `product_revision_id`.
- `contracts/event.schema.json` e `contracts/shakemap.schema.json`, validados contra todas as capturas.
- `scripts/fetch_fixtures.py` e `tests/fixtures/manifest.json`, com capturas de 2026-10-08 00:02 UTC.
- 87 testes unitários, sem rede.

Capturas:

| Fixture | Bytes | No Git |
|---|---:|---|
| `all_day` | 171.380 | sim |
| FDSN `producttype=shakemap&minmmi=6`, 90 dias | 22.017 | sim |
| detail `us6000u0hy` | 40.636 | sim |
| grid `us6000u0hy` (`us`, 201 × 202) | 2.619.204 | sim |
| detail `aka2026tuxgky` | 73.789 | sim |
| grid `aka2026tuxgky` (`ak`, 573 × 299) | 10.740.440 | não |
| detail `us7000tiur` (escolhido pelo FDSN) | 39.789 | sim |
| grid `us7000tiur` (`us`, 201 × 202) | 2.687.406 | sim |

O que as capturas mostraram:

- `all_day` com 240 eventos (121 `automatic`, 119 `reviewed`); 3 com ShakeMap; tipos `earthquake`, `explosion` e `quarry blast`.
- FDSN `minmmi=6` nos últimos 90 dias: 27 eventos.
- `aka2026tuxgky`: a ShakeMap escolhida é a `ak` (peso 227), não a `us` (peso 82), que é mais recente.
- O sha256 declarado no detail é dos bytes do `grid.xml` já sem o gzip de transporte; os três grids bateram com `length` e `sha256`.
- O passo real de `us6000u0hy` é 0,0135545, contra o nominal de 0,0136.
- O grid tem namespace XML (`http://earthquake.usgs.gov/eqcenter/shakemap`); o parser compara só o nome local.
- A URL do grid muda de forma entre produtos (`/pdl/products/urn:...` e `/product/shakemap/...`); por isso ela vem sempre do detail.

Pendências: nenhuma.

## M1: domínio puro (2026-10-07)

Concluído.

Feito, em `src/quakeexposure/domain/`:

- `canonical.py`: JSON canônico e SHA-256; instantes em ISO 8601 UTC com milissegundos; `updated` e `time` convertidos de milissegundos sem passar por float.
- `events.py`: `EventRevision` a partir da feature, `semantic_sha256`, `revision_id`, `Lifecycle` e `EventHistory.apply` (atual, histórico, duplicata ou conflito).
- `aliases.py`: `ingest` resolve o `earthquake_uid` só pelos conjuntos atuais, funde por id preferido (`merged_into`), registra fusões e separações, manda para o histórico a revisão velha e para quarentena o id disputado e a revisão mais nova que uns uids e mais velha que outros.
- `shakemap.py`: `latest_detail` e a seleção que já existia.
- `rule.py`: modos e `Rule`, com a versão igual ao hash do conteúdo.
- `money.py`: soma por moeda em inteiro, string no JSON e formatação.
- `portfolio.py`: `AssetVersion`, `assets_as_of` (epoch K e instante T) e a leitura de `asset-interval-change`.
- `exposure.py`: `InputVector` e `calculation_id`, fatos por ativo nos dois modos, `summarize` e `without_values`.
- Contratos `contracts/asset-interval-change.schema.json` e `contracts/exposure-result.schema.json`.

Testes: 168 (unitários e de propriedade com Hypothesis), mypy strict limpo em 33 arquivos.

| Gate | Testes |
|---|---|
| F02 | `test_f02_out_of_order_revisions`, `test_f02_any_order_ends_in_the_newest_with_full_history` (permutações), `test_f02_through_ingest_with_preferred_id_change`, `test_f02_one_event_in_any_order` (pelo `ingest`, com troca de id preferido) |
| F05 (seleção) | `test_f05_aka2026tuxgky_selects_ak_by_weight`, `test_f05_selection_and_map_that_disappears` |
| F06 | `test_f06_same_version_with_other_content_is_a_conflict`, `test_f01_f06_repeated_or_conflicting_revision_changes_nothing` |
| F13 (domínio) | `test_f13_move_after_event_and_retroactive_correction`, `test_f13_as_of_matches_replayed_source` (oráculo: foto da origem por epoch) |
| F14 | `test_f14_nearest_node_matches_geodesic` (geographiclib, 71°–72° e 179°–181°), colunas por nome, antimeridiano |
| F15 | `test_f15_outside_extent_has_no_value`, `test_f15_outside_real_grid_is_not_eligible`, `test_f15_asset_on_the_edge_is_inside` |
| F26 | `test_f26_totals_per_currency_above_2_pow_53`, `test_f26_totals_are_exact_and_per_currency`, `test_f26_result_json_carries_money_as_string` |

Mutações conferidas à mão (cada uma derruba pelo menos um teste): índice truncado em vez de arredondado, revisão por ordem de chegada, as-of sem epoch, `valid_to` inclusivo, fusão sem tirar o uid absorvido, soma em float, e a versão anterior do `ingest` (que mandava para quarentena a revisão antiga de um evento cujo id preferido tinha mudado). Uma busca aleatória com 40.000 sequências de revisões não achou violação de: um id em um só evento ativo, `revision_id` em um só histórico, reenvio como duplicata, resultado igual com o catálogo restrito ao que a docstring pede e com outra ordem de linhas.

Medição: em 3.000 pontos sorteados no grid de teste, o nó do índice foi o mais próximo pelo geographiclib. Rente à fronteira entre dois nós a diferença chega a 0,35 m, porque no elipsoide a fronteira não fica exatamente no meio do passo; o teste aceita até 1 m.

Fica para o M3: a parte do F14 sobre o raio (99.999 m e 100.001 m) depende do `ST_DWithin` e é testada contra o PostGIS.

Pendências: nenhuma.

## M2: ingestão do USGS e replay (2026-10-08)

Concluído.

Feito:

- `compose.yaml`: `platform-db` (PostGIS 18/3.6) em volume nomeado, sem porta publicada; `migrate` (papel `qe_owner`) antes do `collector` (papel `qe_pipeline`); senhas de `.secrets/` por `*_FILE`; healthcheck que exige o banco `qe` e os papéis. `docker/platform-db-init.sh` cria os papéis e as extensões.
- Migrações `sql/platform/0001` a `0003`: capturas, quarentena, estado das fontes, pedidos de detail, watchlist, cursor do backfill, eventos, aliases, revisões, produtos ShakeMap, mapa escolhido, grids, conflitos de alias pendentes e tentativas de grid. Tudo com `stream_scope`.
- `ingestion/collector.py`: feed a cada 60 s, incremental FDSN a cada 600 s (`includedeleted=true`, timeout de 90 s, watermark só avança com a consulta inteira aplicada, janela dividida ao meio se o FDSN recusar ou passar de 10 MiB), details e reconsultas, grids de eventos em escopo, backfill por dia.
- `ingestion/projection.py` e `store.py`: captura (blob gzip, fsync, rename, depois a linha) e projeção pelo domínio do M1, uma feature por savepoint, quarentena com motivo e hash, uma linha por item.
- `ingestion/replay.py`: REPLAY das capturas do manifesto e FAULT rotulado com `parent_sha256`.
- `qe secrets`, `migrate`, `collect`, `backfill`, `replay`, `status`, `watch`.
- 223 testes: 171 unitários e de propriedade, 52 de integração com PostGIS real num projeto Compose próprio (sobe e derruba).

| Gate | Testes |
|---|---|
| F01 | `test_f01_same_feed_twice_adds_nothing`, `test_f01_replay_twice_adds_nothing` |
| F03 | `test_f03_deleted_event_in_incremental_is_retracted`, `test_f03_detail_409_is_reconsulted_with_includedeleted` |
| F04 | `test_f04_two_events_that_start_sharing_ids_become_one` |
| F07 | `test_f07_grid_that_does_not_match_the_declared_is_rejected` |
| F08 | `test_f08_retry_after_then_stale_source_keeps_events` |
| F21 | `test_f21_detail_url_outside_allowlist`, `test_f21_grid_with_dtd_is_unsupported`, `test_f21_gzip_bomb_in_feed` |
| Watermark | `test_failed_incremental_does_not_advance_watermark`, `test_useless_200_does_not_count_as_success`, `test_lock_timeout_keeps_the_capture_pending_and_the_watermark` |

Também com PostGIS: F05 (`test_f05_preferred_map_is_not_the_newest`), F06 (`test_f06_same_version_with_other_content_goes_to_quarantine`), mapa que some vira NO_MAP, escopo de 7 dias e watchlist, divisão de janela, backfill com cursor, separação LIVE/REPLAY/FAULT, papéis, migração editada recusada, e os casos de recuperação em `tests/integration/test_quarantine.py`, `test_recovery.py` e `test_rules.py`.

Coleta real:

| Janela (UTC, 2026-10-08) | Código | Requisições | Resultado |
|---|---|---:|---|
| 02:35–03:09 | antes das correções da revisão | 50, todas 200 | sem erro no log, quarentena vazia |
| 03:09–03:39 | `d8739b7` | 33, todas 200 | sem erro no log |
| 03:39–05:01 | `7e6a6d3` e `543b94b` | não contadas (o log se perdeu ao recriar o container) | banco sem quarentena em 05:01 |
| 05:01–06:01 | `c08ba95` | 85: 59 feed, 16 FDSN, 9 grids, 1 com 409 | sem erro, aviso ou traceback no log; 1 linha de quarentena (abaixo) |

Na última janela também rodaram `qe backfill` de 2026-10-06 (322 revisões novas em 3,7 s), `qe replay` (8 capturas como REPLAY) e `qe watch add us7000tiur` (detail e grid de um evento fora dos 7 dias). No fim: 622 eventos LIVE ativos, 5 retirados, 660 revisões, 15 mapas, 17 grids, banco com 18 MB.

O que a fonte mostrou:

- O FDSN respondeu em 1 a 4 s, bem abaixo dos 19 a 35 s vistos antes; o incremental de 10 min trouxe de 2 a 7 KB.
- O maior grid baixado tem 392.805 pontos e 21,8 MB (`a2026tvbgrt`, rede `ak`).
- Um 409 real (`aka2026tuxiyh`, deletado) seguiu o caminho do F03: reconsulta com `includedeleted=true` e o evento ficou RETRACTED.
- Para o mesmo `updated`, a coleção do FDSN trouxe esse evento deletado com `type: "earthquake"` e a consulta por `eventid` com `type: ""`. Isso deu um CONFLICT falso. Corrigido depois da janela: em evento deletado o tipo não entra no hash.

Revisão: quatro rodadas de revisão adversarial acharam e corrigiram, entre outros, janela do FDSN acima de 10 MiB que travava o watermark, resposta 200 inútil contada como sucesso, revisão em conflito regravada a cada minuto, feature que derrubava a captura inteira, detail de cache que fechava o pedido, grid com falha que travava os outros e erro do ambiente que virava quarentena permanente. Cada correção tem teste que falha sem ela.

Pendências: nenhuma.

