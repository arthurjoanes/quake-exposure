# Operação

## Primeira carga

Requisitos: Docker Compose, Python 3.14 e uv. Senhas ficam em `.secrets/`.
No Windows, use `python -m uv` se o executável `uv` não estiver no PATH.

```bash
uv sync --frozen
uv run qe secrets
docker compose up -d --build cdc
docker compose run --rm worker qe bootstrap
docker compose up -d worker api
docker compose exec worker qe demo
docker compose exec worker qe status --json
```

O primeiro comando Compose sobe os bancos, migrações, Kafka, tópicos, Connect,
conector e consumidor. O conector usa `snapshot.mode=no_data`; a origem deve
estar vazia quando ele inicia. `bootstrap` espera o slot ativo, semeia por
comandos e aguarda as epochs na plataforma antes do worker. A carga padrão
tem 10.000 ativos. Repetir um bootstrap concluído mantém a carga existente.
Uma carga parcial exige o procedimento de reset abaixo.

A página fica em `http://127.0.0.1:8000`. A API somente leitura não tem login.
Só essa porta é publicada no Compose normal. O coletor fica fora desses
comandos; para consultar o USGS ao vivo:

```bash
docker compose up -d collector
```

A carteira criada pelo loader anterior não é uma origem CDC. Para conservar
um volume da v0.1, use outro projeto, por exemplo `docker compose -p qe-v03 ...`
em todos os comandos da sequência. O bootstrap recusa a mistura silenciosa
entre os dois modos de carga. O loader direto continua disponível só nos testes.

## Carteira e revisões

```bash
docker compose exec worker qe portfolio correct --tenant seguradora-a --interval "seg-00001#1" --lon 120.5 --lat -8.2 --command-id 7758304d-207d-4338-8d07-55ce65f0d4b6
docker compose exec worker qe explain us7000tiur --tenant seguradora-a --mode SHAKEMAP_MMI --scope REPLAY
docker compose exec worker qe watch add us7000tiur
docker compose exec collector qe backfill --from 2026-10-01 --to 2026-10-07
```

Substitua tenant, intervalo e evento pelos identificadores da carga. A correção
é retroativa e preserva o histórico conhecido em epochs anteriores. Repetir
o mesmo command-id e conteúdo retorna o resultado anterior. Reutilizar o id
com outro conteúdo é erro. Cada janela de backfill só avança após terminar.

`REPLAY` usa capturas reais com o tempo original. A demo cria revisões `FAULT`
rotuladas, sem consultas ao USGS. `qe explain` mostra vetor, causas e ativos
que entraram ou saíram da publicação anterior.

## Acompanhamento e recuperação

```bash
docker compose exec worker qe status
docker compose logs --tail 100 cdc connect worker collector
docker compose stop
docker compose up -d cdc worker api
```

`status` mostra idade das fontes, quarentena, transações CDC incompletas,
espera por epoch, estado do conector e WAL retido. O consumidor atualiza a
leitura do Connect e do slot a cada dez segundos. Uma leitura com mais de
30 segundos aparece desatualizada. Fonte HTTP indisponível mantém os eventos
anteriores e aparece como desatualizada.

Se o worker cair, as publicações anteriores continuam disponíveis, salvo
invalidação por exclusão. Reinicie com `docker compose up -d worker` e confira
os jobs pendentes em `qe status`. Claims abandonados voltam após a lease de
dois minutos. Cada job admite cinco tentativas; corrija a causa mostrada nos
logs antes de rearmar um job esgotado. Uma transação interrompida não publica
fatos pela metade, e o CAS recusa cálculos de uma geração anterior.

O stage aceita END antes das linhas e espera as contagens por tabela. O
consumidor confirma offsets depois do commit no banco; uma reentrega não
incrementa a epoch. `op=r`, transação de ativos sem clock, payload inválido
ou offset fora da retenção interrompem o consumidor com erro. Confira logs
antes de reiniciar. Uma epoch faltante aguarda a anterior.

Na atribuição das partições, um grupo sem offset começa em zero somente se
o início retido do tópico ainda for zero. Offset anterior à retenção ou
posterior ao fim do log exige reset manual. A validação cobre as três partições
antes de iniciar qualquer leitura.

`docker compose down` preserva os volumes. O ledger, os bancos, os blobs,
Kafka e os backups usam volumes nomeados separados. A opção `-v` remove
esses dados e só é usada automaticamente em projetos descartáveis de teste.

## Slot perdido ou offsets expirados

O reset é manual e cria uma nova geração da origem. A retenção Kafka é de
sete dias; o slot PostgreSQL depende do WAL disponível. Um reset não recupera
revisões intermediárias que já foram perdidas.

1. Pare comandos da carteira, collector, worker, API e CDC. Faça backup das
   duas bases e copie o ledger atual. Registre o maior `source_epoch` dos clocks.
2. Crie um projeto Compose novo com volumes vazios. Mantenha o projeto antigo
   parado para consultar seus dados. A nova `source_epoch` é a anterior mais um.
3. Exporte os intervalos atuais não apagados da origem antiga, preservando
   tenant, asset_id, asset_interval_id, subject_token, vigência, valor e moeda.
   Preserve também o contato cadastrado e todos os bloqueios do ledger.
4. Inicialize os esquemas da origem e da plataforma novas. Reaplique os bloqueios
   na origem e na plataforma antes da carga. Na plataforma vazia, os tenants
   recriados pelos bloqueios começam com `portfolio_epoch=0` e a nova
   `source_epoch`; preserve a `privacy_epoch` e o ledger. Inicie o conector
   vazio e o CDC.
5. Reinsira os intervalos por `execute_command` de
   `quakeexposure.portfolio.source`, em lotes de até 500, com
   `source_epoch=<nova geração>` e `retroactive=True`. Os pares de intervalos
   de um ativo ficam no mesmo lote. A função recria row_version e epochs;
   os bloqueios impedem recadastrar titulares apagados.
6. Aguarde cada clock na plataforma. Reaplique as capturas REPLAY e recalcule
   as publicações antes de iniciar a API. Confira contagens por tenant e moeda.
   Preserve os backups antigos conforme os prazos dos recibos.

Para uma nova demo fictícia sem dados a conservar, use `seed(source_epoch=N)`
na origem vazia em vez da exportação. Uma seed nova gera outros UUIDs pessoais;
ela não é restauração de uma carteira existente.

## Exclusão e backups

```bash
docker compose exec worker qe erase <subject_token>
docker compose run --rm maintenance qe backup
docker compose run --rm maintenance qe restore-verify /data/backups/<backup>
```

O UUID do titular vem do cadastro da origem. Ele não aparece na API. A exclusão
é idempotente: BLOCKED registra o bloqueio e a nova epoch de privacidade;
PURGED remove origem, revisões e fatos; CLOSED emite o recibo. Publicações
de uma epoch anterior ficam indisponíveis até o recálculo.

O ledger fica em `/data/privacy/erasure.jsonl`, no volume `privacy-ledger`.
Dumps e manifesto com SHA-256 ficam em `/data/backups`. Cada banco tem seu
snapshot próprio. O serviço `maintenance` tem clientes PostgreSQL 18 e os
segredos administrativos necessários; ele só roda quando solicitado.

`restore-verify` verifica os hashes, restaura a plataforma em `qe_restore`,
reaplica o ledger atual e confere a ausência de intervalos, revisões e fatos
do titular. A API não tem CONNECT nesse banco. O verificador só substitui
um `qe_restore` marcado por ele. Restaurar a origem exige repetir `qe erase`
para cada titular do ledger antes de religar a ingestão.

O recibo registra os resíduos em tópicos, WAL e dumps. A retenção dos tópicos
é de sete dias. Apagar backups antigos é uma operação manual do operador;
o projeto não aplica uma política automática de expiração de dumps.

## Registrar a demo

Com o worker ativo, pare o coletor durante a verificação. Substitua `<sha-40>`
pelo resultado de `git rev-parse HEAD`. A execução altera a carteira fictícia e
exclui um ativo, como `qe demo`.

```bash
docker compose stop collector
docker compose exec worker qe verify-demo --commit <sha-40> --output /data/backups/verify-demo.json
docker compose cp worker:/data/backups/verify-demo.json docs/results/verify-demo.json
```

O JSON registra commit, seed, hashes das capturas e asserções dos cinco passos.
Preserve os registros anteriores antes de copiar uma nova execução para o Git.

Grids opcionais de redes diferentes de `us` não são distribuídos no repositório.
O comando abaixo baixa esses arquivos pelo cliente de coleta e confere seus hashes:

```bash
uv run python scripts/fetch_fixtures.py pull
```

## Testes e verificações

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
cd web
npm ci
npm test
npm run build
npm audit --audit-level=low
```

Testes de integração criam projetos Compose próprios com portas loopback
efêmeras. Usam PostGIS, PostgreSQL, Kafka e Debezium reais. Os projetos e volumes
de teste são removidos ao terminar. As conexões HTTP externas ficam bloqueadas;
falhas do USGS são simuladas localmente. Docker indisponível causa skip nas
integrações, não um resultado de integração aprovado.

O workflow `.github/workflows/ci.yaml` executa Ruff, mypy, testes de domínio e
contratos, frontend, integrações, pip-audit e gitleaks. O job `postgis` recebe
o PostgreSQL como service, inicializa os papéis por psycopg e testa a plataforma
sem chamar o Docker. `integration` sobe a stack pelo Compose e também testa
origem, Debezium, exclusão e restauração. As ações usam SHAs,
as imagens e ferramentas de auditoria têm versões fixadas. As execuções remotas
ficam no [GitHub Actions](https://github.com/arthurjoanes/quake-exposure/actions/workflows/ci.yaml).

`.gitleaksignore` contém uma única impressão histórica: o UUID fixo de titular
do builder de teste, identificado como `generic-api-key`. Não exclui arquivos,
regras ou UUIDs novos da varredura.
