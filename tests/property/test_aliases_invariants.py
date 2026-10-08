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
        catalog, result = ingest(catalog, rev, UUID(int=n))

        seen: set[str] = set()
        for entry in catalog.aliases.values():
            assert entry.preferred_id in entry.ids
            assert not entry.ids & seen  # um id, um evento ativo: o valor não duplica
            seen |= entry.ids
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
        stored = {r.revision_id for r in catalog.histories[result.uid].revisions}
        assert rev.revision_id in stored or result.outcome is Outcome.CONFLICT
        if result.outcome is Outcome.CURRENT:
            assert catalog.aliases[result.uid].ids == rev.ids
