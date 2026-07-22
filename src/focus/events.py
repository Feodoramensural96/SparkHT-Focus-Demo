from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator

from .models import FocusEvent


class EventHub:
    def __init__(self, *, max_events: int = 200) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: deque[FocusEvent] = deque(maxlen=max_events)
        self._subscribers: set[asyncio.Queue[FocusEvent]] = set()

    def publish(self, event: FocusEvent) -> None:
        self._events.append(event)
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                queue.put_nowait(event)

    def replay(
        self, session_id: str, last_event_id: str | None = None
    ) -> list[FocusEvent]:
        matching = [event for event in self._events if event.session_id == session_id]
        if last_event_id is None:
            return matching
        for index, event in enumerate(matching):
            if event.event_id == last_event_id:
                return matching[index + 1 :]
        return matching

    async def subscribe(
        self, session_id: str, last_event_id: str | None = None
    ) -> AsyncIterator[FocusEvent | None]:
        queue: asyncio.Queue[FocusEvent] = asyncio.Queue(maxsize=200)
        self._subscribers.add(queue)
        try:
            for event in self.replay(session_id, last_event_id):
                yield event
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except TimeoutError:
                    yield None
                    continue
                if event.session_id == session_id:
                    yield event
        finally:
            self._subscribers.discard(queue)
