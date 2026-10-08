"""Conexão com o PostgreSQL. Senha só por arquivo (`QE_DB_PASSWORD_FILE`)."""

import os
from pathlib import Path

import psycopg

ROOT = Path(__file__).resolve().parents[2]  # raiz do repositório (ou /app na imagem)


def connect(user: str | None = None, password_file: str | None = None) -> psycopg.Connection:
    """Conexão em autocommit; as unidades de trabalho abrem `conn.transaction()`."""
    password_path = password_file or os.environ.get("QE_DB_PASSWORD_FILE")
    return psycopg.connect(
        host=os.environ.get("QE_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("QE_DB_PORT", "5432")),
        dbname=os.environ.get("QE_DB_NAME", "qe"),
        user=user or os.environ.get("QE_DB_USER", "qe_pipeline"),
        password=Path(password_path).read_text("utf-8").strip() if password_path else None,
        autocommit=True,
        options="-c TimeZone=UTC",
        application_name="quake-exposure",
    )
