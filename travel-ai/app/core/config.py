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
    LLM_API_KEY = os.getenv("LLM_API_KEY", "ollama")
    # A 7-day draft takes ~140s on qwen3:8b locally; nginx allows 600s, enough for one retry.
    LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "240"))
    # "none" turns off slow hidden reasoning on models like qwen3 (27s -> 2s per call locally).
    # Set to empty to omit the parameter for providers that don't accept it.
    LLM_REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "none")

    GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
    GOOGLE_OAUTH_CLIENT_ID = os.getenv("GOOGLE_OAUTH_CLIENT_ID")

    SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-change-me")
    SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))

    # Lets you sign in without Google while developing. Never enable in production.
    ALLOW_DEV_LOGIN = _bool("ALLOW_DEV_LOGIN")

    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]

    PLACES_CACHE_TTL_HOURS = int(os.getenv("PLACES_CACHE_TTL_HOURS", "168"))


settings = Settings()
