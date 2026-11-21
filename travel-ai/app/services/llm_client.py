import logging
import re
import time
from typing import TypeVar

from openai import OpenAI, RateLimitError
from pydantic import BaseModel, ValidationError

from app.core.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client = OpenAI(
    base_url=settings.LLM_BASE_URL,
    api_key=settings.LLM_API_KEY,
    timeout=settings.LLM_TIMEOUT_SECONDS,
    # One retry (with backoff) on timeouts, connection and 5xx errors. The SDK default of 2 turned one
    # stuck call into three; this caps a failing call at two attempts.
    max_retries=1,
)

_MAX_RATE_LIMIT_WAIT = 30.0


def _retry_delay(error: RateLimitError) -> float | None:
    """Seconds the provider asks us to wait (Gemini puts it in the body as RetryInfo.retryDelay, e.g. "2s")."""
    body = error.body[0] if isinstance(error.body, list) and error.body else error.body
    if not isinstance(body, dict):
        return None
    error_obj = body.get("error")
    # Some providers send "error" as plain text, which has no details.
    details = (error_obj if isinstance(error_obj, dict) else body).get("details") or []
    for d in details:
        if isinstance(d, dict) and str(d.get("@type", "")).endswith("RetryInfo"):
            match = re.fullmatch(r"([\d.]+)s", str(d.get("retryDelay", "")))
            if match:
                return float(match.group(1))
    return None


def _create(**kwargs):
    """One chat call. On a rate limit with a provider-given wait (free tiers allow ~15 calls a minute),
    wait that long (capped) and try once more; otherwise let the error through.
    Each create() already includes the SDK's own single retry, so this is at most 4 attempts."""
    try:
        return _client.chat.completions.create(**kwargs)
    except RateLimitError as e:
        delay = _retry_delay(e)
        if delay is None or delay > _MAX_RATE_LIMIT_WAIT:
            raise
        logger.warning("LLM rate limited; waiting %.1fs before one more try", delay)
        time.sleep(delay + 0.5)
        return _client.chat.completions.create(**kwargs)


_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMOutputError(RuntimeError):
    pass


def _clean(content: str) -> str:
    content = _THINK_BLOCK.sub("", content or "").strip()
    # Some models wrap JSON in markdown fences.
    if content.startswith("```"):
        content = content.strip("`")
        content = content.removeprefix("json").strip()
    return content


def _require_all(node):
    """Mark every property required (nullable ones can still be null).

    Small local models tend to omit optional fields entirely, e.g. skipping the profile updates.
    Requiring them makes the model fill each one in explicitly.
    """
    if isinstance(node, dict):
        # Models can opt out (e.g. ProfileUpdates), where forcing every field makes the model invent values.
        if node.get("type") == "object" and "properties" in node and not node.get("x-optional-fields"):
            node["required"] = list(node["properties"])
        for value in node.values():
            _require_all(value)
    elif isinstance(node, list):
        for value in node:
            _require_all(value)
    return node


class LLMClient:

    @staticmethod
    def generate_structured(
        prompt: str,
        schema: type[T],
        history: list[dict] | None = None,
        system: str | None = None,
    ) -> T:
        """Ask the model for JSON matching `schema`; retry once with the error if it doesn't."""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.extend(history or [])
        messages.append({"role": "user", "content": prompt})

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema.__name__,
                "schema": _require_all(schema.model_json_schema()),
            },
        }

        last_error: Exception | None = None
        for _ in range(2):
            response = _create(
                model=settings.LLM_MODEL,
                messages=messages,
                response_format=response_format,
                **({"reasoning_effort": settings.LLM_REASONING_EFFORT} if settings.LLM_REASONING_EFFORT else {}),
            )
            content = _clean(response.choices[0].message.content)
            try:
                return schema.model_validate_json(content)
            except ValidationError as e:
                last_error = e
                logger.warning("LLM output failed validation for %s: %s", schema.__name__, e)
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": f"That JSON was invalid: {e}. Reply again with only valid JSON matching the schema.",
                })

        raise LLMOutputError(f"Model returned invalid {schema.__name__}") from last_error
