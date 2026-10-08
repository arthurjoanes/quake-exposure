"""Identidade de evento (`earthquake_uid`) a partir do campo `ids` das revisões.

O mapeamento segue o `ids` da revisão mais recente de cada uid. Uma revisão cujo `ids` contém
o id preferido de outro uid funde os dois: fica o uid que já tinha o id preferido da revisão, o
outro vira `merged_into`. Vão para quarentena: a revisão que divide com outro uid um id que não é
o preferido dele (o id estaria em dois conjuntos atuais) e a fusão com informação mais velha que
a do uid absorvido. Nunca junta por distância ou tempo.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from uuid import UUID

from quakeexposure.domain.events import EventHistory, EventRevision, Outcome


@dataclass(frozen=True)
class AliasEntry:
    preferred_id: str
    ids: frozenset[str]


class AliasKind(StrEnum):
    MERGE = "MERGE"
    SPLIT = "SPLIT"


@dataclass(frozen=True)
class AliasChange:
    """Uma linha de `event_alias_revision`."""

    kind: AliasKind
    uid: UUID
    ids: frozenset[str]  # MERGE: ids que o uid tinha; SPLIT: ids que saíram do uid
    merged_into: UUID | None = None


@dataclass(frozen=True)
class Catalog:
    """Estado de identidade e revisões dos eventos tocados por uma revisão."""

    aliases: Mapping[UUID, AliasEntry] = MappingProxyType({})  # só uids ativos
    histories: Mapping[UUID, EventHistory] = MappingProxyType({})
    merged_into: Mapping[UUID, UUID] = MappingProxyType({})

    def uid_of(self, provider_id: str) -> UUID | None:
        return next((uid for uid, e in self.aliases.items() if provider_id in e.ids), None)


@dataclass(frozen=True)
class Ingested:
    uid: UUID | None  # None em ALIAS_CONFLICT
    outcome: Outcome
    changes: tuple[AliasChange, ...] = ()
    conflict_ids: frozenset[str] = frozenset()


def ingest(catalog: Catalog, rev: EventRevision, new_uid: UUID) -> tuple[Catalog, Ingested]:
    """Aplica uma revisão. `new_uid` só é usado se a revisão não casar com nenhum uid."""
    owners = {uid for uid, e in catalog.aliases.items() if e.ids & rev.ids}
    absorbed = {uid for uid in owners if catalog.aliases[uid].preferred_id in rev.ids}
    if owners - absorbed:
        return catalog, _conflict(catalog, rev, owners - absorbed)

    if not owners:
        target = new_uid
    else:
        holding = [u for u in absorbed if rev.provider_event_id in catalog.aliases[u].ids]
        # sem dono do id preferido (id novo juntando dois eventos): desempate estável
        target = holding[0] if holding else min(absorbed, key=str)
    losers = sorted(absorbed - {target}, key=str)

    history, outcome = catalog.histories.get(target, EventHistory()).apply(rev)
    histories = {**catalog.histories, target: history}
    if outcome is not Outcome.CURRENT:
        # revisão antiga ou repetida não mexe no mapeamento
        return Catalog(catalog.aliases, histories, catalog.merged_into), Ingested(target, outcome)
    # o uid absorvido tem revisão própria mais nova que esta: a fusão é informação velha
    stale = [u for u in losers if _updated_at(catalog, u) >= rev.source_updated_at]
    if stale:
        return catalog, _conflict(catalog, rev, set(stale))

    aliases = dict(catalog.aliases)
    merged_into = dict(catalog.merged_into)
    changes: list[AliasChange] = []
    for uid in losers:
        loser = aliases.pop(uid)
        merged_into[uid] = target
        changes.append(AliasChange(AliasKind.MERGE, uid, loser.ids, merged_into=target))
        if loser.ids - rev.ids:
            changes.append(AliasChange(AliasKind.SPLIT, uid, loser.ids - rev.ids))
    previous = aliases.get(target)
    if previous is not None and previous.ids - rev.ids:
        changes.append(AliasChange(AliasKind.SPLIT, target, previous.ids - rev.ids))
    aliases[target] = AliasEntry(rev.provider_event_id, rev.ids)
    return Catalog(aliases, histories, merged_into), Ingested(target, outcome, tuple(changes))


def _conflict(catalog: Catalog, rev: EventRevision, uids: set[UUID]) -> Ingested:
    shared = frozenset(i for u in uids for i in catalog.aliases[u].ids & rev.ids)
    return Ingested(None, Outcome.ALIAS_CONFLICT, conflict_ids=shared)


def _updated_at(catalog: Catalog, uid: UUID) -> datetime:
    current = catalog.histories[uid].current if uid in catalog.histories else None
    return current.source_updated_at if current else datetime.min.replace(tzinfo=UTC)
