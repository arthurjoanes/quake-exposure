#!/bin/sh
# Papéis e extensões da origem; executado apenas na criação do volume.
set -eu

secret() { cat "/run/secrets/$1"; }

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$(secret qe_source_owner)" \
  -v writer_pw="$(secret qe_source_writer)" \
  -v debezium_pw="$(secret qe_debezium)" <<'SQL'
CREATE ROLE qe_source_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE qe_source_writer LOGIN PASSWORD :'writer_pw';
CREATE ROLE qe_debezium LOGIN REPLICATION PASSWORD :'debezium_pw';
ALTER ROLE qe_source_writer SET statement_timeout = '60s';
ALTER ROLE qe_source_writer SET idle_in_transaction_session_timeout = '60s';
CREATE DATABASE qe_source OWNER qe_source_owner;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname qe_source <<'SQL'
CREATE EXTENSION btree_gist;
SQL
