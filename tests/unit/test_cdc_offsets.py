"""Offsets fora da retenção param o consumidor antes de atribuir partições."""

import pytest
from confluent_kafka import OFFSET_INVALID, TopicPartition

from quakeexposure.cdc.consumer import assign_checked
from quakeexposure.cdc.store import TOPICS, CdcError


class Offsets:
    def __init__(self, offsets: list[int], watermarks: list[tuple[int, int]]) -> None:
        self.offsets = offsets
        self.watermarks = dict(zip(TOPICS, watermarks, strict=True))
        self.assigned: list[TopicPartition] | None = None

    def committed(
        self, partitions: list[TopicPartition], *, timeout: float
    ) -> list[TopicPartition]:
        return [
            TopicPartition(p.topic, p.partition, offset)
            for p, offset in zip(partitions, self.offsets, strict=True)
        ]

    def get_watermark_offsets(
        self, partition: TopicPartition, *, timeout: float
    ) -> tuple[int, int]:
        return self.watermarks[partition.topic]

    def assign(self, partitions: list[TopicPartition]) -> None:
        self.assigned = partitions


def test_new_group_only_starts_at_zero_with_all_history_available() -> None:
    consumer = Offsets([OFFSET_INVALID] * 3, [(0, 0), (0, 30), (0, 10)])
    assign_checked(consumer, [TopicPartition(topic, 0) for topic in TOPICS])
    assert consumer.assigned is not None
    assert [p.offset for p in consumer.assigned] == [0, 0, 0]


def test_committed_positions_resume_without_seeking_to_earliest() -> None:
    consumer = Offsets([5, 20, 10], [(5, 10), (15, 30), (0, 10)])
    assign_checked(consumer, [TopicPartition(topic, 0) for topic in TOPICS])
    assert consumer.assigned is not None
    assert [p.offset for p in consumer.assigned] == [5, 20, 10]


@pytest.mark.parametrize("offset", [OFFSET_INVALID, 4, 31, -1])
def test_expired_missing_or_truncated_offset_requires_reset_before_any_assignment(
    offset: int,
) -> None:
    consumer = Offsets([5, offset, 10], [(0, 10), (5, 30), (0, 10)])
    with pytest.raises(CdcError, match="reset manual"):
        assign_checked(consumer, [TopicPartition(topic, 0) for topic in TOPICS])
    assert consumer.assigned is None
