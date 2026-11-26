import pytest

from app.api import limits
from app.api.limits import DAY, TurnLimiter
from tests.v1.test_api import new_trip, ready_trip, say


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _spend(limiter, user="u"):
    reason = limiter.refusal(user)
    if reason is None:
        limiter.record(user)
    return reason


def test_burst_limit_then_recovers_after_a_minute():
    clock = Clock()
    limiter = TurnLimiter(per_minute=2, per_day=100, clock=clock)
    assert _spend(limiter) is None and _spend(limiter) is None
    assert "minute" in _spend(limiter)
    clock.now += 61
    assert _spend(limiter) is None


def test_daily_quota_then_recovers_after_a_day():
    clock = Clock()
    limiter = TurnLimiter(per_minute=100, per_day=3, clock=clock)
    for _ in range(3):
        assert _spend(limiter) is None
        clock.now += 120
    assert "today's limit of 3" in _spend(limiter)
    clock.now += DAY - 300  # the first turn has aged out, the other two haven't
    assert _spend(limiter) is None
    assert "today's limit" in _spend(limiter)


def test_refused_requests_dont_count_and_travelers_are_separate():
    clock = Clock()
    limiter = TurnLimiter(per_minute=1, per_day=100, clock=clock)
    assert _spend(limiter, "a") is None
    for _ in range(5):
        assert _spend(limiter, "a")
    assert _spend(limiter, "b") is None
    clock.now += 61
    assert _spend(limiter, "a") is None


@pytest.fixture
def tight(monkeypatch):
    def install(per_minute=100, per_day=100):
        limiter = TurnLimiter(per_minute=per_minute, per_day=per_day, clock=Clock())
        monkeypatch.setattr(limits, "limiter", limiter)
        return limiter
    return install


def test_chat_turns_past_the_limit_get_a_429_with_a_message(client, auth, tight):
    tight(per_day=2)
    trip = new_trip(client, auth)
    say(client, auth, trip["id"], "Lisbon")
    say(client, auth, trip["id"], "food")

    refused = client.post(f"/trips/{trip['id']}/messages", json={"message": "a week"}, headers=auth)
    assert refused.status_code == 429 and "today's limit of 2" in refused.json()["detail"]
    with client.stream("POST", f"/trips/{trip['id']}/messages/stream", json={"message": "a week"}, headers=auth) as r:
        assert r.status_code == 429


def test_only_actions_that_used_the_model_are_charged(client, auth, fake_llm, tight):
    trip = ready_trip(client, auth, fake_llm)
    draft = say(client, auth, trip["id"], "not sure")["drafts"][0]
    client.post(f"/trips/{trip['id']}/drafts/{draft['id']}/choose", headers=auth)
    limiter = tight(per_day=1)
    base = f"/trips/{trip['id']}/itinerary/days/1/activities"

    assert client.post(f"{base}/0", json={"action": "swap"}, headers=auth).json()["options"]  # used the model
    assert client.post(f"{base}/0", json={"action": "swap"}, headers=auth).status_code == 429
    assert client.post(f"{base}/1", json={"action": "remove"}, headers=auth).status_code == 200  # no model: free
    assert len(limiter._hits) == 1 and len(next(iter(limiter._hits.values()))) == 1
