# 0002: Kafka somente para a carteira

Status: Aceito. Registra a decisão de escopo de 2026-10-07.

Os eventos do USGS chegam por polling HTTP. O coletor grava capturas e projeta
revisões no PostgreSQL. Kafka transporta apenas as alterações da carteira,
capturadas pelo Debezium do WAL de uma origem PostgreSQL separada.

As três tabelas de privacidade e deduplicação ficam fora da publicação.
Os dois tópicos de linhas e o de transações têm uma partição e sete dias de
retenção. O destino espera END e todas as linhas, aplica a transação inteira
e só então confirma offsets. O stage absorve reentregas.

Essa divisão mantém a captura de revisões do USGS sem um segundo transporte
e permite testar CDC, ordenação por epoch e retomada na carteira. Kafka não
substitui o histórico bitemporal do PostgreSQL.
