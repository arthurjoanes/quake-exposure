"""Status compartilhado pela CLI e API: fila de cálculo sem expor dados dos jobs."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi.testclient import TestClient

from quakeexposure.api import create_app
from quakeexposure.ingestion.store import Conn
from quakeexposure.status import format_report, report

from .conftest import Database


def test_status_distinguishes_pending_active_final_attempt_exhausted_and_finished(
    conn: Conn,
    database: Database,
) -> None:
    now = datetime.now(UTC)
    expired = now - timedelta(days=1)
    active = now + timedelta(days=1)
    rows = [
        (0, None, 1),  # nunca tentado, pronto para o worker
        (4, expired, 1),  # ainda pode tentar depois da lease expirada
        (5, active, 1),  # a quinta tentativa está em curso, ainda não se esgotou
        (5, None, 1),  # falhou na quinta tentativa
        (6, expired, 1),  # esgotado, sem execução em curso
        (5, active, 3),  # publicado: lease residual não o torna pendente
        (5, None, 3),  # publicado: tentativas residuais não são uma falha pendente
    ]
    with conn.cursor() as cursor:
        cursor.executemany(
            """INSERT INTO control.exposure_job (tenant_id, earthquake_uid, stream_scope,
                   mode, desired_calculation_id, desired_generation, published_generation,
                   lease_until, attempts, last_error, marked_at)
               VALUES ('private-tenant', %s, 'LIVE', 'RADIUS_TRIAGE', %s, 3, %s, %s, %s,
                       'private-error-detail', %s)""",
            [
                (uuid4(), "a" * 64, published, lease, attempts, now)
                for attempts, lease, published in rows
            ],
        )
    expected = {"pending": 2, "leased": 1, "exhausted": 2}
    status = report(conn, now)
    assert status["exposure_jobs"] == expected
    assert "cálculos: 2 pendentes; 1 com lease ativa; 2 esgotados" in format_report(status)
    assert "private-tenant" not in json.dumps(status)
    assert "private-error-detail" not in json.dumps(status)
    with TestClient(create_app(lambda: database.connect("qe_api_read"), static_dir=None)) as api:
        response = api.get("/api/status")
        assert response.status_code == 200
        assert response.json()["exposure_jobs"] == expected
        assert response.json()["jobs_pending"] == 3
        assert response.json()["jobs_failed"] == 2
        assert response.json()["publications_outdated"] == 5
        assert "private-error-detail" not in response.text

    # Quando a lease da última tentativa termina, ela passa de leased para exhausted.
    assert report(conn, active + timedelta(seconds=1))["exposure_jobs"] == {
        "pending": 2,
        "leased": 0,
        "exhausted": 3,
    }
