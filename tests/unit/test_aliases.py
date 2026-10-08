from uuid import UUID

from builders import revision

from quakeexposure.domain.aliases import AliasChange, AliasEntry, AliasKind, Catalog, ingest
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
