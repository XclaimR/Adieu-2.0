import os

from dotenv import load_dotenv
load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


class Settings:
    DATABASE_URL = os.getenv("DATABASE_URL")

    # Any OpenAI-compatible endpoint: Ollama (default), Gemini, OpenAI, ...
    # Switching providers is a config change only.
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "http://localhost:11434/v1")
    LLM_MODEL = os.getenv("LLM_MODEL", "qwen3:8b")
    # Optional stronger model for drafts and edits. Empty means LLM_MODEL does everything.
    LLM_MODEL_PLAN = os.getenv("LLM_MODEL_PLAN") or LLM_MODEL
    LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
    # A 7-day draft takes ~140s on qwen3:8b locally; nginx allows 600s, enough for one retry.
    LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "240"))
    # "none" turns off slow hidden reasoning on models like qwen3 (27s -> 2s per call locally).
    # Set to empty to omit the parameter for providers that don't accept it.
    LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")

    GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")

    SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
    SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))

    # Lets you sign in without Google while developing. Never enable in production.
    ALLOW_DEV_LOGIN = _bool("ALLOW_DEV_LOGIN")

    # Per traveler: model-using requests (chat turns, pick and activity swaps) per minute and per rolling day.
    # The shared Gemini free tier allows ~15 calls a minute and 500 a day per model; a turn with drafts uses ~4-5.
    TURNS_PER_MINUTE = int(os.getenv("TURNS_PER_MINUTE", "5"))
    TURNS_PER_DAY = int(os.getenv("TURNS_PER_DAY", "40"))

    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]


settings = Settings()
