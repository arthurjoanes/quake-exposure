#!/bin/sh
# Roda uma vez, na criação do volume. Papéis, banco `qe` e extensões (precisam de superusuário).
set -eu

secret() { cat "/run/secrets/$1"; }

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  -v owner_pw="$(secret qe_owner)" \
  -v pipeline_pw="$(secret qe_pipeline)" \
  -v api_pw="$(secret qe_api_read)" <<'SQL'
CREATE ROLE qe_owner LOGIN PASSWORD :'owner_pw';
CREATE ROLE qe_pipeline LOGIN PASSWORD :'pipeline_pw';
CREATE ROLE qe_api_read LOGIN PASSWORD :'api_pw';
ALTER ROLE qe_pipeline SET statement_timeout = '60s';
ALTER ROLE qe_pipeline SET idle_in_transaction_session_timeout = '60s';
ALTER ROLE qe_api_read SET default_transaction_read_only = on;
ALTER ROLE qe_api_read SET statement_timeout = '10s';
CREATE DATABASE qe OWNER qe_owner;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname qe <<'SQL'
CREATE EXTENSION postgis;
CREATE EXTENSION btree_gist;
SQL
