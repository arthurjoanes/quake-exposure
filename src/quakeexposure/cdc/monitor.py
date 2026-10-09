"""Estado resumido do Connect e do slot, sem dados da carteira nem credenciais."""

import os
from datetime import UTC, datetime

import httpx

from quakeexposure.ingestion.store import Conn


def refresh(conn: Conn) -> None:
    from quakeexposure.portfolio.source import connect_source

    connector_state, task_state = "UNAVAILABLE", "UNKNOWN"
    slot_active: bool | None = None
    retained: int | None = None
    failures: list[str] = []
    endpoint = os.environ.get("QE_CONNECT_URL", "http://connect:8083").rstrip("/")
    try:
        with httpx.Client(timeout=3, follow_redirects=False) as client:
            response = client.get(f"{endpoint}/connectors/portfolio/status")
            response.raise_for_status()
            state = response.json()
        connector_state = str(state["connector"]["state"])
        states = {str(t["state"]) for t in state["tasks"]}
        task_state = ",".join(sorted(states)) or "NO_TASKS"
    except httpx.HTTPError, ValueError, KeyError, TypeError:
        failures.append("Connect indisponível")
    try:
        with connect_source() as source:
            row = source.execute("SELECT * FROM ops.cdc_slot_status()").fetchone()
            if row is not None:
                slot_active, retained = row
            else:
                failures.append("slot qe_portfolio ausente")
    except Exception:
        failures.append("estado do slot indisponível")
    conn.execute(
        """INSERT INTO control.cdc_health
               (singleton, checked_at, connector_state, task_state, slot_active,
                retained_wal_bytes, last_error)
           VALUES (true, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (singleton) DO UPDATE SET checked_at = EXCLUDED.checked_at,
               connector_state = EXCLUDED.connector_state, task_state = EXCLUDED.task_state,
               slot_active = EXCLUDED.slot_active, retained_wal_bytes = EXCLUDED.retained_wal_bytes,
               last_error = EXCLUDED.last_error""",
        (
            datetime.now(UTC),
            connector_state,
            task_state,
            slot_active,
            retained,
            "; ".join(failures) or None,
        ),
    )
