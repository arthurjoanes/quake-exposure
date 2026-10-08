# QuakeExposure

Cruza terremotos do USGS e os mapas de intensidade deles (ShakeMap) com uma carteira fictícia de ativos e mostra quais ativos ficaram expostos. O foco é engenharia de dados: revisões de evento sem dupla contagem, carteira temporal, CDC e replay.

Não é previsão, alerta nem modelo de dano ou perda. O USGS já publica ShakeMap e ShakeCast.

Estado: há o cliente HTTP do USGS, as fixtures reais, os contratos dos dados e o domínio em código puro (revisões, aliases, seleção de ShakeMap, grid, regra, carteira as-of e vetor de entrada). Ainda não há banco, coletor, API nem página. O andamento está em [docs/progress.md](docs/progress.md).

## Rodar

Precisa do [uv](https://docs.astral.sh/uv/). O Python vem do próprio uv (`.python-version`).

```bash
uv sync
uv run qe --version
```

Grids de redes diferentes de `us` não vão para o Git. Para baixá-los da fonte e conferir o sha256:

```bash
uv run python scripts/fetch_fixtures.py pull
```

`capture` refaz todas as capturas a partir do USGS e reescreve `tests/fixtures/manifest.json`.

## Testar

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

Os testes não acessam a rede. Sem o `pull`, os testes do grid `ak` são pulados.

## Limitações

- A carteira é fictícia. Os dados de terremoto são reais.
- O polling não vê toda revisão intermediária do USGS, só as que estavam publicadas quando o feed foi lido.

## Dados e licença

Código sob MIT. Os dados são do U.S. Geological Survey (domínio público). Créditos em [NOTICE](NOTICE).
