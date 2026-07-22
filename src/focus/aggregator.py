from __future__ import annotations

from datetime import datetime, timedelta

from .models import FocusStats, FrameObservation


class FocusAggregator:
    """Accumulates only explicit, sufficiently confident visual observations."""

    def __init__(self, *, confidence_threshold: float = 0.55) -> None:
        self.confidence_threshold = confidence_threshold
        self._analyzed_frames = 0
        self._valid_person = 0
        self._present = 0
        self._valid_phone = 0
        self._phone_visible = 0
        self._phone_transitions = 0
        self._last_phone: str | None = None
        self._last_cup: str | None = None
        self._cup_went_missing = False
        self._last_motion_changed: FrameObservation | None = None
        self._last_drink_event_at: datetime | None = None
        self._drink_events = 0

    def add_many(self, observations: list[FrameObservation]) -> None:
        for observation in observations:
            self.add(observation)

    def add(self, observation: FrameObservation) -> None:
        self._analyzed_frames += 1
        confident = observation.confidence >= self.confidence_threshold

        if confident and observation.person != "uncertain":
            self._valid_person += 1
            self._present += observation.person == "present"

        if confident and observation.phone != "uncertain":
            self._valid_phone += 1
            self._phone_visible += observation.phone == "visible"
            if self._last_phone is not None and self._last_phone != observation.phone:
                self._phone_transitions += 1
            self._last_phone = observation.phone
        else:
            self._last_phone = None

        if confident and observation.cup != "uncertain":
            if self._last_cup == "visible" and observation.cup == "not_visible":
                self._cup_went_missing = True
            elif self._cup_went_missing and observation.cup == "visible":
                self._record_drink_candidate(observation.captured_at)
                self._cup_went_missing = False
            self._last_cup = observation.cup
        else:
            self._last_cup = None
            self._cup_went_missing = False

        if observation.cup_motion == "changed" and observation.confidence >= 0.65:
            previous = self._last_motion_changed
            if previous is not None:
                self._record_drink_candidate(observation.captured_at)
            self._last_motion_changed = observation
        else:
            self._last_motion_changed = None

    def _record_drink_candidate(self, occurred_at: datetime) -> None:
        if (
            self._last_drink_event_at is not None
            and occurred_at - self._last_drink_event_at <= timedelta(seconds=20)
        ):
            return
        self._drink_events += 1
        self._last_drink_event_at = occurred_at

    def snapshot(self) -> FocusStats:
        presence = self._present / self._valid_person if self._valid_person else None
        phone = self._phone_visible / self._valid_phone if self._valid_phone else None
        score = None
        if presence is not None and phone is not None:
            score = 100 * (0.7 * presence + 0.3 * (1 - phone))
        return FocusStats(
            analyzed_frames=self._analyzed_frames,
            valid_person_frames=self._valid_person,
            valid_phone_frames=self._valid_phone,
            presence_ratio=presence,
            phone_visible_ratio=phone,
            phone_transition_count=self._phone_transitions,
            suspected_drink_events=self._drink_events,
            focus_proxy_score=score,
        )
