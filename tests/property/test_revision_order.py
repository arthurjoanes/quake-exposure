from datetime import timedelta

from builders import revision
from hypothesis import given
from hypothesis import strategies as st

from quakeexposure.domain.canonical import EPOCH
from quakeexposure.domain.events import EventHistory, EventRevision, Outcome


@st.composite
def shuffled_revisions(draw: st.DrawFn) -> list[EventRevision]:
    times = draw(st.lists(st.integers(0, 10**12), min_size=1, max_size=8, unique=True))
    mags = draw(
        st.lists(
            st.none() | st.integers(-10, 100).map(lambda m: m / 10),
            min_size=len(times),
            max_size=len(times),
        )
    )
    revs = [revision("us1", updated=t, mag=m) for t, m in zip(times, mags, strict=True)]
    return draw(st.permutations(revs))


def by_time(rev: EventRevision) -> int:
    return (rev.source_updated_at - EPOCH) // timedelta(milliseconds=1)


@given(shuffled_revisions())
def test_f02_any_order_ends_in_the_newest_with_full_history(order: list[EventRevision]) -> None:
    history = EventHistory()
    newest = -1
    for rev in order:
        history, outcome = history.apply(rev)
        # só a revisão mais nova que todas as anteriores vira atual (e pede cálculo)
        assert outcome is (Outcome.CURRENT if by_time(rev) > newest else Outcome.HISTORY)
        newest = max(newest, by_time(rev))
    assert history.revisions == tuple(sorted(order, key=by_time))
    assert history.current == max(order, key=by_time)


@given(shuffled_revisions(), st.data())
def test_f01_f06_repeated_or_conflicting_revision_changes_nothing(
    order: list[EventRevision], data: st.DataObject
) -> None:
    history = EventHistory()
    for rev in order:
        history, _ = history.apply(rev)
    again = data.draw(st.sampled_from(order))
    assert history.apply(again) == (history, Outcome.DUPLICATE)

    conflicting = revision("us1", updated=by_time(again), mag=(again.mag or 0) + 1)
    assert history.apply(conflicting) == (history, Outcome.CONFLICT)
