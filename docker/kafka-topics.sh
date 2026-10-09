#!/bin/sh
# A criação automática está desligada no broker e no Connect.
set -eu

for topic in connect-configs connect-offsets connect-status; do
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --create \
    --if-not-exists --topic "$topic" --partitions 1 --replication-factor 1 \
    --config cleanup.policy=compact
done
for topic in portfolio.public.asset_interval portfolio.public.portfolio_clock portfolio.transaction; do
  /opt/kafka/bin/kafka-topics.sh --bootstrap-server kafka:9092 --create \
    --if-not-exists --topic "$topic" --partitions 1 --replication-factor 1 \
    --config cleanup.policy=delete --config retention.ms=604800000
done
