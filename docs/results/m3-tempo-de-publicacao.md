# Tempo de revisão aplicada até publicação (M3)

Medido em 2026-10-08 com `qe bench --rounds 10`, com a pilha do `compose.yaml` no ar (worker, API, PostGIS) e a carteira sintética de 10.000 ativos (2 tenants com 5.000 cada, `qe portfolio seed`, seed 42).

Cada rodada grava uma revisão FAULT nova de `us7000tiur` (epicentro deslocado; mesmo grid de 201 × 202 nós) e mede até as 4 publicações (2 tenants × 2 modos) trocarem em `serving.current_publication`. O tempo inclui a espera do worker, que olha a fila a cada 1 s.

| Medida | Mediana | Máximo | Mínimo |
|---|---:|---:|---:|
| revisão aplicada → 4 publicações, com o worker do Compose | 1,51 s | 1,51 s | 1,01 s |
| só marcação + 4 cálculos e publicações, sem a espera (10 rodadas) | 0,33 s | 0,55 s | 0,14 s |

Cada cálculo: as-of da carteira do tenant (5.000 ativos), `ST_DWithin` em geography no modo RADIUS ou amostragem do grid no modo MMI, 5.000 fatos gravados por `COPY`, resumo por moeda e troca da publicação, numa transação.

Máquina:

- AMD Ryzen 7 5700X3D, 8 núcleos e 16 threads; 31,9 GiB de RAM;
- Windows 11 Pro 10.0.26300; Docker Desktop 29.7.2, com 16 CPUs e 15,6 GiB para a VM;
- PostGIS `postgis/postgis:18-3.6`; Python 3.14.8.

Para repetir:

```bash
docker compose up -d --build worker api
docker compose exec worker qe portfolio seed
docker compose exec worker qe bench --rounds 10
```
