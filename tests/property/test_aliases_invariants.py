from uuid import UUID

from builders import revision
from hypothesis import given
from hypothesis import strategies as st

from quakeexposure.domain.aliases import Catalog, ingest
from quakeexposure.domain.events import EventRevision, Outcome

IDS = ["ak1", "ci1", "us1", "us2", "nc1"]


@st.composite
def revisions(draw: st.DrawFn) -> list[EventRevision]:
    out = []
    for _ in range(draw(st.integers(1, 15))):
        ids = draw(st.sets(st.sampled_from(IDS), min_size=1, max_size=3))
        preferred = draw(st.sampled_from(sorted(ids)))
        out.append(revision(preferred, updated=draw(st.integers(0, 40)), ids=ids))
    return out


@given(revisions())
def test_no_id_in_two_active_events(seq: list[EventRevision]) -> None:
    catalog = Catalog()
    for n, rev in enumerate(seq, start=1):
        # a ordem das linhas que o banco devolver não pode mudar o resultado
        shuffled = Catalog(
            dict(reversed(list(catalog.aliases.items()))),
            dict(reversed(list(catalog.histories.items()))),
            dict(reversed(list(catalog.merged_into.items()))),
        )
        catalog, result = ingest(catalog, rev, UUID(int=n))
        assert ingest(shuffled, rev, UUID(int=n)) == (catalog, result)

        seen: set[str] = set()
        for uid, entry in catalog.aliases.items():
            assert not entry.ids & seen  # um id, um evento ativo: o valor não duplica
            seen |= entry.ids
            current = catalog.histories[uid].current
            assert current is not None
            assert (current.provider_event_id, current.ids) == (entry.preferred_id, entry.ids)
        kept = [r for h in catalog.histories.values() for r in h.revisions]
        assert len({r.revision_id for r in kept}) == len(kept)
        assert len({(r.provider_event_id, r.source_updated_at) for r in kept}) == len(kept)
        for uid in catalog.merged_into:
            assert uid not in catalog.aliases
            end = uid
            while end in catalog.merged_into:
                end = catalog.merged_into[end]
            assert end in catalog.aliases

        if result.outcome is Outcome.ALIAS_CONFLICT:
            assert result.uid is None
            continue
        assert result.uid is not None
        assert result.uid in catalog.aliases  # o resultado aponta sempre para um uid ativo
        stored = {r.revision_id for h in catalog.histories.values() for r in h.revisions}
        assert rev.revision_id in stored or result.outcome is Outcome.CONFLICT
        if result.outcome is Outcome.CURRENT:
            assert catalog.aliases[result.uid].ids == rev.ids

    # reprojetar o que já foi guardado não muda nada
    stored = {r.revision_id for h in catalog.histories.values() for r in h.revisions}
    for rev in seq:
        if rev.revision_id in stored:
            again, result = ingest(catalog, rev, UUID(int=999))
            assert result.outcome is Outcome.DUPLICATE and again is catalog


@st.composite
def one_event(draw: st.DrawFn) -> list[EventRevision]:
    """Um evento só: o id preferido passa de ak1 para us1 em algum ponto da linha do tempo."""
    times = sorted(draw(st.lists(st.integers(0, 10**6), min_size=1, max_size=7, unique=True)))
    switch = draw(st.integers(0, len(times)))
    revs = []
    for n, t in enumerate(times):
        if n < switch:
            linked = draw(st.booleans())
            revs.append(revision("ak1", updated=t, ids=["us1"] if linked else []))
        else:
            revs.append(revision("us1", updated=t, ids=["ak1"]))
    return draw(st.permutations(revs))


@given(one_event())
def test_f02_one_event_in_any_order(order: list[EventRevision]) -> None:
    catalog = Catalog()
    for n, rev in enumerate(order, start=1):
        catalog, result = ingest(catalog, rev, UUID(int=n))
        assert result.outcome in (Outcome.CURRENT, Outcome.HISTORY)
    assert len(catalog.aliases) == 1
    (uid,) = catalog.aliases
    newest = max(order, key=lambda r: r.source_updated_at)
    assert catalog.histories[uid].current == newest
    assert set(catalog.histories[uid].revisions) == set(order)
