FROM python:3.14.8-slim-trixie

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

ENV PATH=/app/.venv/bin:$PATH QE_BLOB_DIR=/data/blobs
RUN useradd --uid 10001 --no-create-home app && mkdir -p /data/blobs && chown app /data/blobs
USER app
CMD ["qe", "collect"]
