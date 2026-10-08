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

## M1: domínio puro (2026-10-08)

Concluído.

Feito, em `src/quakeexposure/domain/`:

- `canonical.py`: JSON canônico e SHA-256; instantes em ISO 8601 UTC com milissegundos; `updated` e `time` convertidos de milissegundos sem passar por float.
- `events.py`: `EventRevision` a partir da feature, `semantic_sha256`, `revision_id`, `Lifecycle` e `EventHistory.apply` (atual, histórico, duplicata ou conflito).
- `aliases.py`: `ingest` resolve o `earthquake_uid`, funde por id preferido (`merged_into`), registra fusões e separações e manda para quarentena o id disputado e a fusão com informação velha.
- `shakemap.py`: `latest_detail` e a seleção que já existia.
- `rule.py`: modos e `Rule`, com a versão igual ao hash do conteúdo.
- `money.py`: soma por moeda em inteiro, string no JSON e formatação.
- `portfolio.py`: `AssetVersion`, `assets_as_of` (epoch K e instante T) e a leitura de `asset-interval-change`.
- `exposure.py`: `InputVector` e `calculation_id`, fatos por ativo nos dois modos, `summarize` e `without_values`.
- Contratos `contracts/asset-interval-change.schema.json` e `contracts/exposure-result.schema.json`.

Testes: 163 (unitários e de propriedade com Hypothesis), mypy strict limpo em 33 arquivos.

| Gate | Testes |
|---|---|
| F02 | `test_f02_out_of_order_revisions`, `test_f02_any_order_ends_in_the_newest_with_full_history` (permutações) |
| F05 (seleção) | `test_f05_aka2026tuxgky_selects_ak_by_weight`, `test_f05_selection_and_map_that_disappears` |
| F06 | `test_f06_same_version_with_other_content_is_a_conflict`, `test_f01_f06_repeated_or_conflicting_revision_changes_nothing` |
| F13 (domínio) | `test_f13_move_after_event_and_retroactive_correction`, `test_f13_as_of_matches_replayed_source` (oráculo: foto da origem por epoch) |
| F14 | `test_f14_nearest_node_matches_geodesic` (geographiclib, 71°–72° e 179°–181°), colunas por nome, antimeridiano |
| F15 | `test_f15_outside_extent_has_no_value`, `test_f15_outside_real_grid_is_not_eligible`, `test_f15_asset_on_the_edge_is_inside` |
| F26 | `test_f26_totals_per_currency_above_2_pow_53`, `test_f26_totals_are_exact_and_per_currency`, `test_f26_result_json_carries_money_as_string` |

Mutações conferidas à mão (cada uma derruba pelo menos um teste): índice truncado em vez de arredondado, revisão por ordem de chegada, as-of sem epoch, `valid_to` inclusivo, fusão sem tirar o uid absorvido, soma em float.

Medição: em 3.000 pontos do grid de teste, o nó do índice foi sempre o mais próximo pelo geographiclib.

Fica para o M3: a parte do F14 sobre o raio (99.999 m e 100.001 m) depende do `ST_DWithin` e é testada contra o PostGIS.

Pendências: nenhuma.
