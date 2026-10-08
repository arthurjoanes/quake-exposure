"""Identidade de evento (`earthquake_uid`) a partir do campo `ids` das revisões.

A identidade sai só dos conjuntos atuais (o `ids` da revisão mais recente de cada uid ativo),
nunca do histórico, da distância ou do tempo. Para uma revisão nova:

- mesma `revision_id` em qualquer histórico: duplicata;
- mesmo id e mesmo `source_updated_at` com outro conteúdo: conflito (quarentena);
- não toca nenhum conjunto atual: uid novo;
- não é mais nova que nenhum dos uids que toca: só histórico, o mapeamento não muda;
- mais nova que todos: vira a atual; se o `ids` contém o id preferido de outro uid, os dois se
  fundem (fica o que já tinha o id preferido da revisão; o outro vira `merged_into`); se divide
  com outro uid um id que não é o preferido dele, quarentena;
- mais nova que uns e mais velha que outros: quarentena.

Quarentena é seguida de reconsulta por `eventid`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
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
    """Estado de identidade e revisões dos eventos que uma revisão pode tocar.

    Para `ingest` dar o mesmo resultado que daria com o catálogo inteiro, ele precisa trazer:
    os uids ativos cujos conjuntos tocam `rev.ids`, com os históricos deles; os históricos que
    têm revisão com a mesma `revision_id` ou com o mesmo id e o mesmo `source_updated_at`; e a
    cadeia `merged_into` desses uids.
    """

    aliases: Mapping[UUID, AliasEntry] = MappingProxyType({})  # só uids ativos
    histories: Mapping[UUID, EventHistory] = MappingProxyType({})
    merged_into: Mapping[UUID, UUID] = MappingProxyType({})

    def uid_of(self, provider_id: str) -> UUID | None:
        return next((uid for uid, e in self.aliases.items() if provider_id in e.ids), None)

    def active(self, uid: UUID) -> UUID:
        while uid in self.merged_into:
            uid = self.merged_into[uid]
        return uid


@dataclass(frozen=True)
class Ingested:
    uid: UUID | None  # None em ALIAS_CONFLICT
    outcome: Outcome
    changes: tuple[AliasChange, ...] = ()
    conflict_ids: frozenset[str] = frozenset()


def ingest(catalog: Catalog, rev: EventRevision, new_uid: UUID) -> tuple[Catalog, Ingested]:
    """Aplica uma revisão. `new_uid` só é usado se a revisão não tocar nenhum uid ativo."""
    for uid in sorted(catalog.histories, key=str):
        for r in catalog.histories[uid].revisions:
            if r.revision_id == rev.revision_id:
                return catalog, Ingested(catalog.active(uid), Outcome.DUPLICATE)
            if (r.provider_event_id, r.source_updated_at) == (
                rev.provider_event_id,
                rev.source_updated_at,
            ):
                return catalog, Ingested(catalog.active(uid), Outcome.CONFLICT)

    owners = sorted((u for u, e in catalog.aliases.items() if e.ids & rev.ids), key=str)
    if not owners:
        return _store(catalog, new_uid, rev, merge=[])

    # KeyError aqui é catálogo incompleto: uid ativo sem histórico carregado
    newer = [u for u in owners if _current(catalog, u).source_updated_at < rev.source_updated_at]
    if not newer:
        # informação velha para todos os uids que ela toca: só histórico
        holding = [u for u in owners if rev.provider_event_id in catalog.aliases[u].ids]
        home = holding[0] if holding else owners[0]
        history, outcome = catalog.histories[home].apply(rev)
        histories = {**catalog.histories, home: history}
        return Catalog(catalog.aliases, histories, catalog.merged_into), Ingested(home, outcome)
    if len(newer) < len(owners):
        return catalog, _conflict(catalog, rev, set(owners) - set(newer))

    disputed = {u for u in owners if catalog.aliases[u].preferred_id not in rev.ids}
    if disputed:
        return catalog, _conflict(catalog, rev, disputed)
    holding = [u for u in owners if rev.provider_event_id in catalog.aliases[u].ids]
    # sem dono do id preferido (id novo juntando dois eventos): desempate estável
    target = holding[0] if holding else owners[0]
    return _store(catalog, target, rev, merge=[u for u in owners if u != target])


def _store(
    catalog: Catalog, target: UUID, rev: EventRevision, merge: list[UUID]
) -> tuple[Catalog, Ingested]:
    history, outcome = catalog.histories.get(target, EventHistory()).apply(rev)
    assert outcome is Outcome.CURRENT  # mais nova que a atual de todos os uids que toca
    aliases = dict(catalog.aliases)
    merged_into = dict(catalog.merged_into)
    changes: list[AliasChange] = []
    for uid in merge:
        loser = aliases.pop(uid)
        merged_into[uid] = target
        changes.append(AliasChange(AliasKind.MERGE, uid, loser.ids, merged_into=target))
        if loser.ids - rev.ids:
            changes.append(AliasChange(AliasKind.SPLIT, uid, loser.ids - rev.ids))
    previous = aliases.get(target)
    if previous is not None and previous.ids - rev.ids:
        changes.append(AliasChange(AliasKind.SPLIT, target, previous.ids - rev.ids))
    aliases[target] = AliasEntry(rev.provider_event_id, rev.ids)
    histories = {**catalog.histories, target: history}
    return Catalog(aliases, histories, merged_into), Ingested(target, outcome, tuple(changes))


def _current(catalog: Catalog, uid: UUID) -> EventRevision:
    current = catalog.histories[uid].current
    assert current is not None
    return current


def _conflict(catalog: Catalog, rev: EventRevision, uids: set[UUID]) -> Ingested:
    shared = frozenset(i for u in uids for i in catalog.aliases[u].ids & rev.ids)
    return Ingested(None, Outcome.ALIAS_CONFLICT, conflict_ids=shared)
