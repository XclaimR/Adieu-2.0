import httpx
import pytest
from openai import RateLimitError

from app.services import llm_client


def _rate_limit(delay: str | None):
    details = [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": delay}] if delay else []
    response = httpx.Response(429, request=httpx.Request("POST", "http://llm"))
    # Gemini's OpenAI-compatible endpoint wraps the error in a list.
    return RateLimitError("rate limited", response=response, body=[{"error": {"code": 429, "details": details}}])


class FakeCompletions:
    def __init__(self, errors):
        self.errors, self.calls = list(errors), 0

    def create(self, **kwargs):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return "ok"


@pytest.fixture
def fake(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm_client.time, "sleep", sleeps.append)

    def install(*errors):
        completions = FakeCompletions(errors)
        monkeypatch.setattr(llm_client, "_client", type("C", (), {"chat": type("Ch", (), {"completions": completions})})())
        return completions, sleeps
    return install


def test_waits_the_given_delay_then_retries_once(fake):
    completions, sleeps = fake(_rate_limit("2s"))
    assert llm_client._create(model="m") == "ok"
    assert completions.calls == 2 and sleeps == [2.5]


def test_gives_up_after_one_wait(fake):
    completions, _ = fake(_rate_limit("2s"), _rate_limit("2s"))
    with pytest.raises(RateLimitError):
        llm_client._create(model="m")
    assert completions.calls == 2


@pytest.mark.parametrize("delay", [None, "90s"])
def test_no_wait_without_a_short_delay(fake, delay):
    completions, sleeps = fake(_rate_limit(delay))
    with pytest.raises(RateLimitError):
        llm_client._create(model="m")
    assert completions.calls == 1 and sleeps == []
