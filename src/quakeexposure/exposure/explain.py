"""`qe explain`: o vetor de entrada da publicação atual, o que mudou desde a anterior e por quê.

A causa vem dos campos do vetor que mudaram: revisão do evento, mapa, epoch da carteira, regra
ou exclusão de titular. Os ativos que entraram e saíram vêm da diferença entre os fatos.
"""

from typing import Any
from uuid import UUID

from quakeexposure.ingestion.store import Conn

CAUSES = {
    "event_semantic_sha256": "revisão do evento",
    "product_revision_id": "mapa",
    "portfolio_epoch": "epoch da carteira",
    "portfolio_source_epoch": "origem da carteira",
    "privacy_epoch": "exclusão de titular",
    "rule_version": "regra",
}


def calculation(conn: Conn, calculation_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """SELECT calculation_id, stream_scope, earthquake_uid::text, event_semantic_sha256,
                  event_revision_id, coalesce(product_revision_id, 'NONE'), tenant_id,
                  portfolio_epoch, portfolio_source_epoch, mode, privacy_epoch, rule_version,
                  status, eligible_count, qualifying_count, limitations, calculated_at
           FROM exposure.calculation WHERE calculation_id = %s""",
        (calculation_id,),
    ).fetchone()
    if row is None:
        return None
    (cid, scope, uid, semantic, revision, product, tenant, epoch, source_epoch, mode, privacy,
     rule, status, eligible, qualifying, limitations, calculated_at) = row  # fmt: skip
    totals = conn.execute(
        """SELECT currency, qualifying_value_minor::text FROM exposure.calculation_total
           WHERE calculation_id = %s ORDER BY currency""",
        (cid,),
    ).fetchall()
    return {
        "calculation_id": cid,
        "input_vector": {
            "stream_scope": scope,
            "earthquake_uid": uid,
            "event_semantic_sha256": semantic,
            "product_revision_id": product,
            "tenant_id": tenant,
            "portfolio_epoch": epoch,
            "portfolio_source_epoch": source_epoch,
            "mode": mode,
            "privacy_epoch": privacy,
            "rule_version": rule,
        },
        "event_revision_id": revision,
        "status": status,
        "eligible_count": eligible,
        "qualifying_count": qualifying,
        "qualifying_value_minor": dict(totals) if status == "COMPLETE" else None,
        "calculated_at": calculated_at,
        "limitations": list(limitations),
    }


def publications(
    conn: Conn, tenant_id: str, uid: UUID, scope: str, mode: str
) -> tuple[str | None, str | None]:
    """(calculation_id atual, anterior). Atual None: sem publicação ou retirada."""
    row = conn.execute(
        """SELECT calculation_id FROM serving.publication_log
           WHERE tenant_id = %s AND earthquake_uid = %s AND stream_scope = %s AND mode = %s
           ORDER BY publication_seq DESC LIMIT 1""",
        (tenant_id, uid, scope, mode),
    ).fetchone()
    current = row[0] if row else None
    return current, previous(conn, current) if current else None


def previous(conn: Conn, calculation_id: str) -> str | None:
    """A última publicação da mesma chave antes desta, com outro cálculo. Retiradas no meio
    (fusão, tipo fora da regra) não contam: o antes é o último cálculo publicado."""
    row = conn.execute(
        """SELECT l.calculation_id FROM serving.publication_log l
           JOIN exposure.calculation c ON c.calculation_id = %(id)s
           WHERE (l.tenant_id, l.earthquake_uid, l.stream_scope, l.mode)
                 = (c.tenant_id, c.earthquake_uid, c.stream_scope, c.mode)
             AND l.calculation_id IS NOT NULL AND l.calculation_id <> %(id)s
             AND l.publication_seq < (SELECT max(publication_seq) FROM serving.publication_log
                                      WHERE calculation_id = %(id)s)
           ORDER BY l.publication_seq DESC LIMIT 1""",
        {"id": calculation_id},
    ).fetchone()
    return row[0] if row else None


def diff(conn: Conn, before: str | None, after: str) -> dict[str, Any]:
    """Causas (campos do vetor que mudaram) e ativos que entraram ou saíram da classificação."""
    new = calculation(conn, after)
    assert new is not None
    old = calculation(conn, before) if before else None
    causes = (
        [label for field, label in CAUSES.items()
         if old["input_vector"][field] != new["input_vector"][field]]
        if old else ["primeira publicação"]
    )  # fmt: skip
    rows = conn.execute(
        """SELECT coalesce(a.asset_id, b.asset_id), b.qualifying, a.qualifying, b.mmi, a.mmi,
                  coalesce(a.currency, b.currency), coalesce(a.insured_value_minor,
                  b.insured_value_minor)::text
           FROM (SELECT * FROM exposure.fact WHERE calculation_id = %s) a
           FULL JOIN (SELECT * FROM exposure.fact WHERE calculation_id = %s) b
             USING (asset_id)
           WHERE coalesce(a.qualifying, false) <> coalesce(b.qualifying, false)
           ORDER BY 1""",
        (after, before),
    ).fetchall()
    entered: list[dict[str, Any]] = []
    left: list[dict[str, Any]] = []
    for asset_id, was, now, mmi_before, mmi_after, currency, value in rows:
        item = {
            "asset_id": asset_id,
            "currency": currency,
            "insured_value_minor": value,
            "mmi_before": mmi_before,
            "mmi_after": mmi_after,
            "in_before": was is not None,
            "in_after": now is not None,
        }
        (entered if now else left).append(item)
    return {"before": old, "after": new, "causes": causes, "entered": entered, "left": left}


def format_diff(d: dict[str, Any]) -> str:
    after = d["after"]
    v = after["input_vector"]
    lines = [
        f"publicação {after['calculation_id'][:16]} ({after['status']})",
        f"  evento {v['earthquake_uid']} revisão {after['event_revision_id'][:16]}",
        f"  mapa {v['product_revision_id']}",
        f"  tenant {v['tenant_id']} epoch {v['portfolio_epoch']} (origem"
        f" {v['portfolio_source_epoch']}), privacidade {v['privacy_epoch']}",
        f"  modo {v['mode']}, regra {v['rule_version'][:16]}",
        f"  elegíveis {after['eligible_count']}, classificados {after['qualifying_count']}",
    ]
    for currency, value in (after["qualifying_value_minor"] or {}).items():
        lines.append(f"  {currency} {value} (unidade menor)")
    lines.append("mudou por: " + ", ".join(d["causes"] or ["nada no vetor"]))
    for title, items in (("entraram", d["entered"]), ("saíram", d["left"])):
        lines.append(f"{title}: {len(items)}")
        for item in items[:20]:
            mmi = (
                f" MMI {item['mmi_before']} -> {item['mmi_after']}"
                if item["mmi_after"] or item["mmi_before"]
                else ""
            )
            lines.append(
                f"  {item['asset_id']} {item['currency']} {item['insured_value_minor']}{mmi}"
            )
        if len(items) > 20:
            lines.append(f"  ... e mais {len(items) - 20}")
    return "\n".join(lines)
