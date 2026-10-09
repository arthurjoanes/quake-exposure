FROM node:24.19.0-trixie-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM postgres:18.6-trixie AS pgclient
# Mantém cliente e servidor na mesma versão; o diretório independe da arquitetura.
RUN mkdir /pg-client && cp /usr/lib/*/libpq.so.5.18 /pg-client/libpq.so.5

FROM python:3.14.8-slim-trixie

RUN apt-get update && apt-get install -y --no-install-recommends \
    libgssapi-krb5-2 libldap2 liblz4-1 libzstd1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_dump /usr/local/bin/pg_dump
COPY --from=pgclient /usr/lib/postgresql/18/bin/pg_restore /usr/local/bin/pg_restore
COPY --from=pgclient /pg-client/libpq.so.5 /usr/local/lib/libpq.so.5
RUN ldconfig && pg_dump --version && pg_restore --version

COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY sql ./sql
COPY contracts ./contracts
COPY tests/fixtures ./tests/fixtures
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./web/dist

ENV PATH=/app/.venv/bin:$PATH QE_BLOB_DIR=/data/blobs
RUN useradd --uid 10001 --no-create-home app \
    && mkdir -p /data/blobs /data/privacy /data/backups \
    && chown app /data/blobs /data/privacy /data/backups
USER app
CMD ["qe", "collect"]
