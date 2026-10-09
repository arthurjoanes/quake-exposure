# Smoke do CDC — 2026-10-09

Projeto Compose descartável `qe-cdc-smoke`, sem coletor nem chamadas ao USGS.
Imagens: `postgres:18.6-trixie`, `apache/kafka:4.3.1` e
`quay.io/debezium/connect:3.6.2.Final`. PostgreSQL com `wal_level=logical`,
`pgoutput`, publicação manual, snapshot `no_data` e metadata de transação.
Kafka e Connect iniciaram com heaps de 512 e 768 MiB.

Três transações sequenciais:

| XID | Escritas | Saída no Kafka |
|---|---|---|
| 781 | somente `ops.command_dedup`, fora da publicação | nenhuma mensagem |
| 782 | deduplicação + 2 intervalos + 1 clock | BEGIN, 3 linhas e END |
| 783 | 1 atualização de clock | BEGIN, 1 linha e END |

O END de 782 trouxe `event_count=3` e `data_collections` com
`public.asset_interval:2`, `public.portfolio_clock:1`. A escrita fora da publicação
não entra nas contagens. A chegada entre os três tópicos não conserva a ordem:
o END foi consumido antes da linha de clock.

Nesta versão, `transaction.id` tem formato `xid:lsn`, e o LSN muda dentro da
mesma transação. Valores observados para 782: BEGIN `782:31007824`, primeira
linha `782:31007824`, segunda `782:31008088`, clock `782:31008256` e END
`782:31008536`. `total_order` é 1, 2, 3. `source.txId` é 782 e `source.ts_ms`
é igual ao `ts_ms` do END. O consumidor precisa agrupar pelo XID e pelo instante
de commit; comparar o identificador bruto deixa as transações incompletas.

Os payloads JSON chegam sem envelope de schema. Timestamp de `timestamptz`
é uma string ISO; bigint permanece inteiro JSON. Os testes de integração
repetem a coleta pelo Debezium em um projeto isolado, sem fixture de Kafka fabricada.
