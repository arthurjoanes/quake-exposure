"""Estado da coleta para o `qe status` (e, depois, para o `/status` da API)."""

from datetime import datetime, timedelta
from typing import Any

from quakeexposure.ingestion.collector import MAX_REQUEST_ATTEMPTS
from quakeexposure.ingestion.store import Conn

# passou disso sem sucesso: a fonte aparece como desatualizada
STALE_AFTER = {"feed": timedelta(minutes=5), "incremental": timedelta(minutes=30)}


def report(conn: Conn, now: datetime) -> dict[str, Any]:
    rows = conn.execute(
        """SELECT source, last_attempt_at, last_ok_at, last_error, watermark, last_new_revisions
           FROM control.source_status"""
    ).fetchall()
    known = {r[0]: r for r in rows}
    sources: dict[str, Any] = {}
    for source, limit in STALE_AFTER.items():
        _, attempt, ok, error, watermark, new = known.get(
            source, (source, None, None, None, None, None)
        )
        sources[source] = {
            "last_attempt_age_s": _age(now, attempt),
            "last_ok_age_s": _age(now, ok),
            "last_error": error,
            "watermark": watermark.isoformat() if watermark else None,
            "last_new_revisions": new,
            "stale": ok is None or now - ok > limit,
        }

    def count(sql: str, *params: Any) -> int:
        row = conn.execute(sql, params).fetchone()
        return int(row[0]) if row else 0

    return {
        "sources": sources,
        "captures_pending": count(
            "SELECT count(*) FROM control.capture WHERE projected_at IS NULL"
        ),
        "quarantine_total": count("SELECT count(*) FROM control.quarantine"),
        "quarantine_24h": count(
            "SELECT count(*) FROM control.quarantine WHERE created_at > %s", now - timedelta(days=1)
        ),
        "detail_requests_pending": count(
            "SELECT count(*) FROM control.fetch_request WHERE attempts < %s", MAX_REQUEST_ATTEMPTS
        ),
        "detail_requests_failed": count(
            "SELECT count(*) FROM control.fetch_request WHERE attempts >= %s", MAX_REQUEST_ATTEMPTS
        ),
        "grids_rejected": count(
            "SELECT count(*) FROM quake.shakemap_grid WHERE status = 'REJECTED'"
        ),
        "grids_retrying": count(
            "SELECT count(*) FROM control.grid_attempt WHERE attempts < %s", MAX_REQUEST_ATTEMPTS
        ),
        "grids_failed": count(
            "SELECT count(*) FROM control.grid_attempt WHERE attempts >= %s", MAX_REQUEST_ATTEMPTS
        ),
        "events_active": {
            scope: n
            for scope, n in conn.execute(
                """SELECT stream_scope, count(*) FROM quake.earthquake
                   WHERE merged_into IS NULL GROUP BY stream_scope ORDER BY stream_scope"""
            )
        },
    }


def _age(now: datetime, then: datetime | None) -> int | None:
    return None if then is None else int((now - then).total_seconds())


def format_report(r: dict[str, Any]) -> str:
    lines = []
    for name, label in (("feed", "feed all_day"), ("incremental", "incremental FDSN")):
        s = r["sources"][name]
        ok = "nunca" if s["last_ok_age_s"] is None else f"há {_human(s['last_ok_age_s'])}"
        line = f"{label}: último ok {ok}"
        if s["stale"]:
            line += " (desatualizado)"
        if s["last_error"]:
            line += f"; último erro: {s['last_error']}"
        if name == "incremental" and s["watermark"]:
            line += f"; watermark {s['watermark']}; {s['last_new_revisions']} revisões novas"
        lines.append(line)
    lines += [
        f"capturas não projetadas: {r['captures_pending']}",
        f"quarentena: {r['quarantine_total']} ({r['quarantine_24h']} nas últimas 24 h)",
        f"details pendentes: {r['detail_requests_pending']}"
        f" (desistidos: {r['detail_requests_failed']})",
        f"grids recusados: {r['grids_rejected']}; com falha de rede: {r['grids_retrying']}"
        f" (desistidos: {r['grids_failed']})",
        "eventos ativos: "
        + (", ".join(f"{k} {v}" for k, v in r["events_active"].items()) or "nenhum"),
    ]
    return "\n".join(lines)


def _human(seconds: int) -> str:
    if seconds < 120:
        return f"{seconds} s"
    if seconds < 7200:
        return f"{seconds // 60} min"
    return f"{seconds // 3600} h"
