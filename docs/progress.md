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
