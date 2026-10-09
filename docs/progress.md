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

## M3: cálculo, página e v0.1 (2026-10-08)

Concluído.

Feito:

- `sql/platform/0004`: esquemas `portfolio`, `exposure` e `serving`. Carteira com vigência `[valid_from, valid_to)` e exclusion constraint (`btree_gist`), cálculos, totais por moeda em `numeric`, fatos com PK `(calculation_id, asset_id)`, `control.exposure_job`, `serving.current_publication` e o log de trocas.
- `portfolio/synthetic.py` e `qe portfolio seed`: 10.000 ativos em 2 tenants (seed 42), BRL e USD, a maior parte em volta dos eventos de demo, 5% mudam de lugar numa epoch 2.
- `exposure/jobs.py`: escopo e marcação pelo vetor de entrada. A projeção sobe a geração dos jobs do evento na mesma transação em que grava a revisão (`touch`).
- `exposure/worker.py`: claim com `FOR UPDATE SKIP LOCKED` e lease de 2 min, grid carregado antes da transação, cálculo e publicação numa transação com CAS na geração. Job atrasado em relação ao estado é acertado na hora.
- `exposure/explain.py` e `qe explain`: vetor, causas e ativos que entraram e saíram.
- `api/`: FastAPI somente leitura com o papel `qe_api_read`, em 127.0.0.1:8000, servindo também a página.
- `web/`: TypeScript, Vite e Leaflet com mapa-base Natural Earth embutido (sem tiles externos). Mapa com eventos e ativos, revisões, modo, contagens e valores por moeda, antes e depois, faixa de desatualização, REPLAY e FAULT rotulados. Mapa e valores sempre da mesma publicação.
- `qe demo` (passos 1, 2 e 4) e `qe bench`. Serviços `worker` e `api` no Compose.
- 241 testes Python (171 unitários e de propriedade, 70 de integração, 18 deles de cálculo e API) e 33 da página (vitest).

| Gate | Testes |
|---|---|
| F04 | `test_f04_merge_leaves_one_publication_and_the_value_once` |
| F05 | `test_f05_publication_uses_the_preferred_map_and_no_map_has_null_counts`, `test_f05_live_map_that_disappears_turns_the_mmi_publication_into_no_map` |
| F11 | `test_f11_worker_that_dies_before_commit_leaves_nothing` (com a API) |
| F12 | `test_f12_stale_calculation_is_refused_by_the_generation` (com a API; a revisão nova chega só pela projeção) |
| F14 | `test_f14_radius_edge_matches_geographiclib` (8 azimutes a 99.999 e 100.001 m de 4 epicentros, inclusive ±180 e 78,5° N) |
| F26 | `test_f26_api_returns_exact_money_per_currency` (2^53 + 1 e 2^62) |
| API | `test_api_is_coherent_and_never_shows_the_subject_token`, `test_api_role_cannot_read_the_raw_portfolio`, `test_api_lists_events_with_publication_first` |

Também: F01 com publicação, totais iguais à soma dos fatos, as-of com o cálculo rodando depois da mudança do ativo, causa no `explain`, `explain` e `/diff` com o mesmo anterior depois de uma retirada, RETRACTED com contagens nulas, escopo LIVE (janela, watchlist e retirada fora da janela) e job atrasado que não volta igual para a fila.

Demo na pilha real (10.000 ativos, worker do Compose no ar):

```
1. replay da captura real e cálculo inicial
   REPLAY us7000tiur operadora-b RADIUS_TRIAGE: COMPLETE, elegíveis 5000, classificados 1931; BRL 5067820026, USD 29352865208 (unidade menor)
   REPLAY us7000tiur operadora-b SHAKEMAP_MMI: COMPLETE, elegíveis 1972, classificados 45; BRL 86434666, USD 514787670 (unidade menor)
   ...
2. revisão FAULT que muda a exposição
   operadora-b RADIUS: epicentro 0,5° a leste; entraram 1572, saíram 1; causa: revisão do evento
   seguradora-a RADIUS: epicentro 0,5° a leste; entraram 1545, saíram 0; causa: revisão do evento
   ...
   revisão duplicada, nenhum cálculo novo; publication_id igual: sim
4. job velho recusado pela geração
   cálculo da geração 35: recusado; geração atual 36
   publicada a geração 37
```

O passo 2 parte da última revisão FAULT de uma demo anterior (1,5° a leste); por isso entram ativos.

Tempo de revisão aplicada até as 4 publicações (2 tenants × 2 modos): mediana de 1,56 s com o worker do Compose, que olha a fila a cada 1 s; 0,60 s só de marcação, cálculo e publicação. Detalhes e máquina em [results/m3-tempo-de-publicacao.md](results/m3-tempo-de-publicacao.md).

A página foi aberta na pilha real: `us7000tiur` no REPLAY e no LIVE com valores por moeda e antes e depois; só requisições para 127.0.0.1; evento a −177,6° desenhado ao lado de um a 179,9°.

Revisão: uma rodada adversarial em cinco frentes (requisitos, worker, API, página e testes), cada achado conferido por um segundo agente. Corrigido:

- job cujo vetor mudou entre a marcação e o claim voltava igual para a fila, e a rodada do worker não terminava;
- a mudança de entrada só subia a geração quando o worker marcava, então o CAS nunca recusava nada na pilha real (o teste do F12 marcava à mão);
- no LIVE, mapa que some e retirada depois de 7 dias não mudavam a publicação;
- `/api/events` cortava em 500 por data, e eventos publicados (a watchlist, boa parte dos 7 dias) sumiam da página; `limit` negativo dava 500;
- `explain` e `/diff` discordavam do anterior depois de uma retirada;
- marcadores do outro lado de ±180 ficavam a 360° do mapa-base;
- a demo podia perder a corrida para o worker do Compose;
- o `bench` repetia posições e media cálculos que já existiam (os números acima são da versão corrigida);
- o teste de as-of passava com o cálculo usando o cadastro da hora do cálculo, e nenhum teste cobria o escopo.

Cada correção tem teste que falha sem ela (conferido com mutação).

Observações:

- `exposure.fact` guarda todos os ativos do tenant em cada cálculo (cerca de 1 MB por cálculo de 5.000 ativos). Com demos e benches repetidos, o banco da máquina de teste chegou a 1,2 GB.
- O default privilege do esquema `control` dá SELECT ao `qe_api_read` em toda tabela nova. A tabela de staging do CDC no M4 terá `subject_token` e precisa ficar fora disso.
- Sem requisições ao USGS neste milestone: o coletor ficou parado e a demo usou REPLAY e FAULT.

Pendências: nenhuma.

## M4: CDC real da carteira e v0.2 (2026-10-09)

Concluído.

- Origem PostgreSQL 18.6, Kafka 4.3.1 e Debezium 3.6.2. Publicação e tópicos manuais; somente intervalos e clock saem da origem.
- Funções de escrita serializam o tenant, incrementam uma epoch por comando e deduplicam o command_id. Correção retroativa é explícita. Seed em lotes com estado de conclusão e UUIDs aleatórios por ativo.
- Stage durável; END pode chegar antes das linhas. Aplicação integral somente após contagens exatas e epoch seguinte. Offset confirmado depois do banco, em lotes de até 250 mensagens ou 1 s. Payload aplicado é descartado.
- API exibe transações pendentes, estado do Connect e WAL do slot. O papel da API não lê o stage.
- Smoke real em [results/m4-cdc-smoke.md](results/m4-cdc-smoke.md). O identificador bruto do Debezium muda de LSN dentro da transação; a chave interna combina XID e instante de commit.

Validação executada por grupos: 171 testes unitários/propriedade anteriores, 70 integrações anteriores, 15 unitários CDC, 11 integrações CDC, 10 da origem, 3 gates com Debezium real (F01/F09/F13) e 2 da infraestrutura (incluindo F23). Ruff e mypy limpos. Interface: 33 testes, typecheck e build passaram.

Demo na stack com 10.000 ativos: duplicata preservou publication_id; correção via CDC mudou operadora-b de epoch 12 para 13, mantendo lon -152,16068 em K12 e -152,14068 em K13; geração 9 recusada após chegar geração 10, publicação final geração 11. Zero requisições ao USGS.

RAM por `docker stats`, durante a demo: plataforma 354,30 MiB; origem 58,30 MiB; Kafka 571,70 MiB; Connect 1,215 GiB; CDC 49,58 MiB; API 56,41 MiB; worker 38,87 MiB. Total aproximado 2,32 GiB. Docker com 15,56 GiB, outros projetos também ativos.

O primeiro bootstrap excedeu 180 s com commit Kafka por mensagem. Após agrupar confirmações, o consumidor drenou cerca de 500 linhas a cada 3 s; a retomada verificou o seed completo e terminou sem semear novamente. A medição a frio fica no M6.

A stack nova usa o projeto Compose `quake-exposure-v03`; os volumes da v0.1 foram preservados. Pendência externa: push depende do Arthur.

## M5: exclusão, backfill e v0.3 (2026-10-09)

Concluído.

- `privacy/erasure.py` e `ledger.py`: BLOCKED, PURGED e CLOSED retomáveis; bloqueio da origem, ledger com fsync em volume separado, privacy_epoch e recibo com resíduos. O ledger contém apenas identificadores necessários à reaplicação.
- CDC sanitiza payloads pendentes e reentregas de titulares bloqueados. A exclusão remove contato, intervalos, revisões, fatos e totais antigos, e invalida todos os jobs do tenant.
- API mantém a leitura sob lock compartilhado do tenant; a exclusão usa lock exclusivo. Publicações de outra privacy_epoch respondem 409. O worker verifica a epoch e a geração antes de publicar.
- `privacy/backup.py`: dumps PostgreSQL 18, manifesto SHA-256 e restauração em `qe_restore`. O ledger atual é reaplicado; o papel da API não tem CONNECT. A restauração da origem continua manual, com reaplicação das exclusões.
- `qe demo` executa os cinco passos. A correção da carteira via CDC preserva as-of; exclusão seguida de CDC antigo não recria o ativo.
- `status.py` distingue cálculos pendentes, com lease ativa e esgotados. A quinta tentativa em curso não aparece como falha. Na página, resposta ou 409 de uma seleção antiga não substitui a seleção atual.

Gates F12 (backfill chegando durante o cálculo), F27 (segunda metade da janela falha), F18 (exclusão durante cálculo e CDC antigo) e F19 (dump anterior à exclusão, ledger posterior) passaram com os bancos reais. Os testes de exclusão também cobrem retomada após cada fase e falha de escrita no ledger.

A suíte geral encontrou uma corrida no helper de captura dos testes: um END atrasado de outra transação podia encerrar a captura antes da operação pedida. O helper agora delimita a ação por XIDs reservados na origem. Uma regressão pausa o Connect, acumula uma transação antiga e exige as duas transações novas completas. Os oito testes de captura e exclusão passaram juntos em 61,84 s após a correção.

Demo completa em `bb230de`: oito asserções passaram, incluindo soma dos fatos, API sem acesso ao stage e CDC antigo bloqueado. Resultado em [results/verify-demo.json](results/verify-demo.json). O backup e a restauração também foram executados pela CLI na stack de 10.000 ativos: titular ausente e acesso da API negado, em [results/restore-verification.json](results/restore-verification.json). O teste F19, separadamente, removeu da restauração linhas presentes no dump antigo.

Página conferida no build atualizado: `us7000tiur`, REPLAY, Operadora B e SHAKEMAP_MMI; 1.972 elegíveis, 45 classificados, BRL 864.346,66 e USD 5.147.876,70. Sem erros no console. [Captura da página](results/ui-v0.3.png).

Requisições ao USGS: zero. A coleta ao vivo ficou parada. O recibo informa retenção Kafka de sete dias e resíduos em WAL/dumps; não há limpeza automática de backups.

## M6: CI, operação e verificação final (2026-10-09)

Concluído.

- `.github/workflows/ci.yaml`: Ruff, formatação, mypy, unitários, propriedades, contratos, frontend, PostGIS como service, integrações Compose e auditorias. Ações fixadas por SHA. O modo de service PostgreSQL passou nos 86 testes existentes; o teste de status foi acrescentado depois, totalizando 87 selecionados nesse job.
- `README.md` e `README.en.md`: produto, execução, testes e limitações. `docs/operacao.md`: carga inicial, fonte atrasada, CDC/slot/offset perdido, worker parado, exclusão e restauração. Três ADRs registram as decisões de Kafka, CAS e exclusão contra replay.
- `qe verify-demo`: commit, seed 42, hashes das capturas e oito asserções em [results/verify-demo.json](results/verify-demo.json). Resultado da execução em `bb230de`; as correções posteriores tratam sincronização da interface, helper de testes e contadores de status.
- Bootstrap com volumes vazios e imagens em cache: 183,814 s até carteira/REPLAY; 356,907 s até as quatro publicações da demo; 377,862 s até drenar os 998 jobs. RAM observada: aproximadamente 2,10 GiB. Máquina, carga concorrente e método em [results/m6-bootstrap-cold.md](results/m6-bootstrap-cold.md).
- `pip-audit` 2.10.1 verificou todas as dependências do lockfile; `npm audit` verificou o lockfile da página. Nenhuma vulnerabilidade conhecida reportada. Gitleaks 8.30.1 não encontrou credenciais após a exclusão exata de um UUID fixo histórico do builder, documentada em `.gitleaksignore`. Actionlint 1.7.12 passou.

Validação final: `uv run pytest -q --durations=10` terminou com 307 passed em 609,93 s, sem skips. Houve um aviso de depreciação do Starlette sobre o transporte httpx do TestClient. As duas falhas da primeira rodada foram corrigidas no helper de captura, mantendo as asserções dos testes. Ruff passou; 98 arquivos com formatação conferida; mypy sem erros em 86 arquivos. Na página, 45 testes passaram, typecheck e build concluíram. A imagem Docker final foi construída e os serviços da aplicação foram atualizados.

Commits locais desta continuação: `e4aaa1c` (CDC, tag v0.2), `bb230de` (exclusão, restauração e demo) e `34637ec` (captura dos testes, concorrência da interface e status). O commit de fechamento inclui CI, documentos e resultados; a tag v0.3 marca a entrega completa.

A instância entregue está em `http://127.0.0.1:8000`, projeto Compose `quake-exposure-v03`, com os bancos, Kafka, Connect, CDC, worker e API. O coletor não foi iniciado. Ao fechar a verificação: zero transações CDC pendentes, zero cálculos pendentes/com lease/esgotados e zero quarentenas. Os volumes da v0.1 foram preservados; os projetos temporários de teste e medição são removidos ao terminar.

Pendência externa: criar o repositório remoto e autorizar o push dos commits e tags. O CI está configurado; nenhuma execução no GitHub foi feita. Nenhuma requisição ao USGS nesta continuação. Nenhuma nova decisão de escopo.
