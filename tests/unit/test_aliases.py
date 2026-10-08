from itertools import permutations
from uuid import UUID

from builders import revision

from quakeexposure.domain.aliases import (
    AliasChange,
    AliasEntry,
    AliasKind,
    Catalog,
    Ingested,
    ingest,
)
from quakeexposure.domain.events import Outcome

U1, U2, U3, U4 = (UUID(int=i) for i in range(1, 5))


def test_new_event_gets_new_uid() -> None:
    catalog, result = ingest(Catalog(), revision("ak1", updated=1), U1)
    assert (result.uid, result.outcome) == (U1, Outcome.CURRENT)
    assert catalog.aliases == {U1: AliasEntry("ak1", frozenset({"ak1"}))}
    assert catalog.uid_of("ak1") == U1


def test_f04_two_events_start_sharing_ids() -> None:
    catalog, _ = ingest(Catalog(), revision("ak1", updated=1), U1)
    catalog, _ = ingest(catalog, revision("us1", updated=2), U2)
    catalog, result = ingest(catalog, revision("us1", updated=3, ids=["ak1"]), U3)

    # fica o uid que já tinha o id preferido da revisão (us1)
    assert (result.uid, result.outcome) == (U2, Outcome.CURRENT)
    assert result.changes == (AliasChange(AliasKind.MERGE, U1, frozenset({"ak1"}), merged_into=U2),)
    assert list(catalog.aliases) == [U2]  # uma publicação ativa só
    assert catalog.merged_into == {U1: U2}
    assert catalog.uid_of("ak1") == catalog.uid_of("us1") == U2

    # revisão nova pelo id antigo cai no uid que ficou
    catalog, result = ingest(catalog, revision("us1", updated=4, ids=["ak1"]), U4)
    assert result.uid == U2 and U4 not in catalog.histories


def test_preferred_id_change_keeps_uid() -> None:
    catalog, _ = ingest(Catalog(), revision("ak1", updated=1), U1)
    catalog, result = ingest(catalog, revision("us1", updated=2, ids=["ak1"]), U2)
    assert result.uid == U1 and result.changes == ()
    assert catalog.aliases[U1] == AliasEntry("us1", frozenset({"ak1", "us1"}))


def test_split_detaches_ids_and_they_can_become_another_event() -> None:
    catalog, _ = ingest(Catalog(), revision("us1", updated=1, ids=["ak1"]), U1)
    catalog, result = ingest(catalog, revision("us1", updated=2), U2)
    assert result.changes == (AliasChange(AliasKind.SPLIT, U1, frozenset({"ak1"})),)
    catalog, result = ingest(catalog, revision("ak1", updated=3), U3)
    assert result.uid == U3 and set(catalog.aliases) == {U1, U3}


def test_older_revision_does_not_change_mapping() -> None:
    catalog, _ = ingest(Catalog(), revision("us1", updated=5, ids=["ak1"]), U1)
    after, result = ingest(catalog, revision("us1", updated=3), U2)
    assert result.outcome is Outcome.HISTORY
    assert after.aliases == catalog.aliases
    assert len(after.histories[U1].revisions) == 2


def test_id_claimed_by_two_current_sets_goes_to_quarantine() -> None:
    catalog, _ = ingest(Catalog(), revision("us1", updated=1, ids=["x1"]), U1)
    after, result = ingest(catalog, revision("us2", updated=2, ids=["x1"]), U2)
    assert result.outcome is Outcome.ALIAS_CONFLICT
    assert result.uid is None and result.conflict_ids == {"x1"}
    assert after is catalog


def test_stale_merge_goes_to_quarantine() -> None:
    catalog, _ = ingest(Catalog(), revision("us1", updated=5), U1)
    catalog, _ = ingest(catalog, revision("ak1", updated=9), U2)
    # mais nova que a atual de us1, mas mais velha que a atual de ak1
    after, result = ingest(catalog, revision("us1", updated=6, ids=["ak1"]), U3)
    assert result.outcome is Outcome.ALIAS_CONFLICT and after is catalog


def test_new_preferred_id_joining_two_events_is_deterministic() -> None:
    catalog, _ = ingest(Catalog(), revision("ak1", updated=1), U2)
    catalog, _ = ingest(catalog, revision("ci1", updated=2), U1)
    catalog, result = ingest(catalog, revision("us1", updated=3, ids=["ak1", "ci1"]), U3)
    assert result.uid == U1 and catalog.merged_into == {U2: U1}


def test_f02_through_ingest_with_preferred_id_change() -> None:
    r1 = revision("ak1", updated=1)
    r2 = revision("ak1", updated=2, ids=["us1"])
    r3 = revision("us1", updated=3, ids=["ak1"])
    for order in permutations([r1, r2, r3]):
        catalog = Catalog()
        newest = 0.0
        for n, rev in enumerate(order, start=1):
            catalog, result = ingest(catalog, rev, UUID(int=n))
            t = rev.source_updated_at.timestamp()
            assert result.outcome is (Outcome.CURRENT if t > newest else Outcome.HISTORY), order
            newest = max(newest, t)
        assert len(catalog.aliases) == 1
        (uid,) = catalog.aliases
        assert catalog.histories[uid].revisions == (r1, r2, r3)
        assert catalog.aliases[uid] == AliasEntry("us1", frozenset({"ak1", "us1"}))


def test_replayed_revision_is_a_duplicate_after_merge_or_split() -> None:
    old = revision("ak1", updated=1)
    merged, _ = ingest(Catalog(), old, U1)
    merged, _ = ingest(merged, revision("us1", updated=2), U2)
    merged, _ = ingest(merged, revision("us1", updated=3, ids=["ak1"]), U3)
    assert ingest(merged, old, U4) == (merged, Ingested(U2, Outcome.DUPLICATE))

    split, _ = ingest(Catalog(), old, U1)
    split, _ = ingest(split, revision("us1", updated=2, ids=["ak1"]), U2)
    split, _ = ingest(split, revision("us1", updated=3), U3)  # ak1 sai do conjunto
    assert ingest(split, old, U4) == (split, Ingested(U1, Outcome.DUPLICATE))

    # revisão inédita de um id que saiu de todos os conjuntos: a identidade vem só dos
    # conjuntos atuais, então é outro evento (como seria se ela tivesse chegado na ordem)
    after, result = ingest(split, revision("ak1", updated=0), U4)
    assert result == Ingested(U4, Outcome.CURRENT)
    assert after.aliases[U4] == AliasEntry("ak1", frozenset({"ak1"}))


def test_same_version_with_other_content_in_an_absorbed_history_is_a_conflict() -> None:
    catalog, _ = ingest(Catalog(), revision("ak1", updated=10), U1)
    catalog, _ = ingest(catalog, revision("us1", updated=20), U2)
    catalog, _ = ingest(catalog, revision("us1", updated=30, ids=["ak1"]), U3)  # U1 -> U2
    after, result = ingest(catalog, revision("ak1", updated=10, mag=6.4), U4)
    assert result == Ingested(U2, Outcome.CONFLICT) and after is catalog


def test_id_that_left_a_set_does_not_depend_on_arrival_order() -> None:
    revs = {
        "ak1@10": revision("ak1", updated=10),
        "us1@20": revision("us1", updated=20, ids=["ak1"]),
        "us1@30": revision("us1", updated=30),  # ak1 sai
        "ak1@25": revision("ak1", updated=25),
    }
    finals = set()
    for order in permutations(revs.values()):
        catalog = Catalog()
        quarantined = []
        for n, rev in enumerate(order, start=1):
            catalog, result = ingest(catalog, rev, UUID(int=n))
            if result.outcome is Outcome.ALIAS_CONFLICT:
                quarantined.append(rev)
        # a reconsulta devolve a mesma revisão, agora depois das outras
        for n, rev in enumerate(quarantined, start=10):
            catalog, result = ingest(catalog, rev, UUID(int=n))
            assert result.outcome is not Outcome.ALIAS_CONFLICT
        finals.add(frozenset(catalog.aliases.values()))
    assert finals == {
        frozenset({AliasEntry("us1", frozenset({"us1"})), AliasEntry("ak1", frozenset({"ak1"}))})
    }
