import threading
import time
from collections import defaultdict, deque
from itertools import takewhile

from fastapi import HTTPException, status

from app.core.config import settings

DAY = 24 * 60 * 60


class TurnLimiter:
    """Counts each traveler's model-using requests (chat turns, which include drafting, and pick/activity swaps):
    a short burst limit and a rolling 24-hour quota, so one person can't spend the shared free LLM quota.
    shortcut: in-process, so counts reset when the API restarts and assume one uvicorn worker; move to Postgres
    if we ever run more than one."""

    def __init__(self, per_minute: int, per_day: int, clock=time.monotonic):
        self.per_minute, self.per_day, self.clock = per_minute, per_day, clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def refusal(self, user_id: str) -> str | None:
        """Why this traveler can't use the model right now, or None."""
        now = self.clock()
        with self._lock:
            hits = self._hits[user_id]
            while hits and hits[0] <= now - DAY:
                hits.popleft()
            if len(hits) >= self.per_day:
                return (f"You've reached today's limit of {self.per_day} requests. Adieu shares a small AI budget "
                        "with everyone, so please come back tomorrow — your trips are saved.")
            if sum(1 for _ in takewhile(lambda t: t > now - 60, reversed(hits))) >= self.per_minute:
                return "That's a lot at once — give me a minute, then try again."
            return None

    def record(self, user_id: str) -> None:
        with self._lock:
            self._hits[user_id].append(self.clock())


limiter = TurnLimiter(settings.TURNS_PER_MINUTE, settings.TURNS_PER_DAY)


def check_turn(user_id: str) -> None:
    """Raises 429 with a message for the traveler when they're past their limits."""
    if reason := limiter.refusal(user_id):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, reason)


def record_turn(user_id: str) -> None:
    limiter.record(user_id)


def spend_turn(user_id: str) -> None:
    """For work that always calls the model (a chat turn)."""
    check_turn(user_id)
    record_turn(user_id)
