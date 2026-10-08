"""Carteira na plataforma: carga direta (M3) e leitura das versões para o as-of."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from quakeexposure.domain.money import Currency
from quakeexposure.domain.portfolio import AssetVersion
from quakeexposure.ingestion.store import Conn
from quakeexposure.portfolio.synthetic import TENANTS, SeedRow


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    portfolio_epoch: int
    source_epoch: int
    privacy_epoch: int


def tenants(conn: Conn) -> list[Tenant]:
    rows = conn.execute(
        """SELECT tenant_id, portfolio_epoch, source_epoch, privacy_epoch
           FROM portfolio.tenant ORDER BY tenant_id"""
    ).fetchall()
    return [Tenant(*r) for r in rows]


def load(conn: Conn, rows: Iterable[SeedRow], now: datetime | None = None) -> int:
    """Carga direta, num commit. Cada epoch da carga vira um comando: a epoch do tenant fica
    igual à maior `known_epoch` das linhas. Recusa tenant que já tem carteira."""
    rows = list(rows)
    now = now or datetime.now(UTC)
    names = {t: name for t, name, _, _ in TENANTS}
    with conn.transaction():
        for tenant_id in sorted({r.tenant_id for r in rows}):
            epoch = max(r.known_epoch for r in rows if r.tenant_id == tenant_id)
            done = conn.execute(
                """INSERT INTO portfolio.tenant (tenant_id, name, portfolio_epoch)
                   VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING 1""",
                (tenant_id, names.get(tenant_id, tenant_id), epoch),
            ).fetchone()
            if done is None:
                raise ValueError(f"o tenant {tenant_id} já tem carteira")
        with conn.cursor().copy(
            """COPY portfolio.asset_revision (tenant_id, asset_interval_id, row_version, asset_id,
                   subject_token, known_epoch, source_epoch, valid_from, valid_to, lon, lat,
                   insured_value_minor, currency, is_deleted, updated_at) FROM STDIN"""
        ) as copy:
            for r in rows:
                copy.write_row((
                    r.tenant_id, r.asset_interval_id, r.row_version, r.asset_id, r.subject_token,
                    r.known_epoch, 1, r.valid_from, r.valid_to, r.lon, r.lat,
                    r.insured_value_minor, r.currency, r.is_deleted, now,
                ))  # fmt: skip
        latest: dict[tuple[str, str], SeedRow] = {}
        for r in rows:
            key = (r.tenant_id, r.asset_interval_id)
            if key not in latest or r.row_version > latest[key].row_version:
                latest[key] = r
        with conn.cursor().copy(
            """COPY portfolio.asset_interval (tenant_id, asset_interval_id, asset_id, row_version,
                   valid_from, valid_to, is_deleted) FROM STDIN"""
        ) as copy:
            for r in latest.values():
                copy.write_row((
                    r.tenant_id, r.asset_interval_id, r.asset_id, r.row_version, r.valid_from,
                    r.valid_to, r.is_deleted,
                ))  # fmt: skip
    return len(rows)


def versions_known(conn: Conn, tenant_id: str, epoch: int) -> list[AssetVersion]:
    """Todas as versões que a epoch K já conhecia; o domínio escolhe as vigentes em T."""
    rows = conn.execute(
        """SELECT tenant_id, asset_interval_id, asset_id, row_version, known_epoch, valid_from,
                  valid_to, lon, lat, insured_value_minor, currency, is_deleted
           FROM portfolio.asset_revision WHERE tenant_id = %s AND known_epoch <= %s""",
        (tenant_id, epoch),
    ).fetchall()
    return [
        AssetVersion(t, i, a, rv, ke, vf, vt, lon, lat, value, Currency(cur), deleted)
        for t, i, a, rv, ke, vf, vt, lon, lat, value, cur, deleted in rows
    ]
