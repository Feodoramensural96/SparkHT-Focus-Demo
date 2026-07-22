from datetime import UTC, datetime, timedelta

import pytest

from focus.aggregator import FocusAggregator
from focus.models import FrameObservation


BASE = datetime(2026, 7, 22, 5, 0, tzinfo=UTC)


def observation(
    sequence: int,
    *,
    person: str = "present",
    phone: str = "not_visible",
    cup: str = "visible",
    cup_motion: str = "stable",
    confidence: float = 0.9,
) -> FrameObservation:
    return FrameObservation(
        frame_id=f"f-{sequence:03d}",
        captured_at=BASE + timedelta(seconds=sequence * 10),
        person=person,
        phone=phone,
        cup=cup,
        cup_motion=cup_motion,
        confidence=confidence,
        evidence="测试可见证据",
    )


def test_uncertain_and_low_confidence_are_excluded() -> None:
    aggregator = FocusAggregator()
    aggregator.add_many(
        [
            observation(1),
            observation(2, person="uncertain", phone="uncertain"),
            observation(3, person="absent", phone="visible", confidence=0.54),
        ]
    )

    stats = aggregator.snapshot()
    assert stats.presence_ratio == 1.0
    assert stats.phone_visible_ratio == 0.0
    assert stats.analyzed_frames == 3


def test_empty_denominators_return_none() -> None:
    aggregator = FocusAggregator()
    aggregator.add(observation(1, person="uncertain", phone="uncertain"))
    stats = aggregator.snapshot()
    assert stats.presence_ratio is None
    assert stats.phone_visible_ratio is None
    assert stats.focus_proxy_score is None


def test_phone_transition_does_not_cross_uncertain() -> None:
    aggregator = FocusAggregator()
    aggregator.add_many(
        [
            observation(1, phone="visible"),
            observation(2, phone="uncertain"),
            observation(3, phone="not_visible"),
            observation(4, phone="visible"),
        ]
    )
    assert aggregator.snapshot().phone_transition_count == 1


def test_suspected_drink_events_are_deduplicated_for_twenty_seconds() -> None:
    aggregator = FocusAggregator()
    aggregator.add_many(
        [
            observation(0, cup="visible"),
            observation(1, cup="not_visible"),
            observation(2, cup="visible"),
            observation(3, cup_motion="changed", confidence=0.8),
            observation(4, cup_motion="changed", confidence=0.8),
            observation(5, cup_motion="changed", confidence=0.8),
        ]
    )
    assert aggregator.snapshot().suspected_drink_events == 2


def test_focus_score_formula_and_bounds() -> None:
    aggregator = FocusAggregator()
    aggregator.add_many(
        [
            observation(1, person="present", phone="visible"),
            observation(2, person="absent", phone="not_visible"),
        ]
    )
    assert aggregator.snapshot().focus_proxy_score == pytest.approx(50.0)
