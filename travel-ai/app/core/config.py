import os

from dotenv import load_dotenv
load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes")


class Settings:
    # "production" unless set otherwise, so a forgotten setting fails closed. Local dev sets APP_ENV=development.
    APP_ENV = os.getenv("APP_ENV", "production").strip().lower()

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

    SESSION_SECRET = os.getenv("SESSION_SECRET") or "dev-only-change-me"  # empty counts as unset
    SESSION_TTL_DAYS = int(os.getenv("SESSION_TTL_DAYS", "30"))

    # Lets you sign in without Google while developing. Never enable in production.
    ALLOW_DEV_LOGIN = _bool("ALLOW_DEV_LOGIN")

    CORS_ORIGINS = [
        o.strip()
        for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if o.strip()
    ]


_DEFAULT_SECRETS = {"dev-only-change-me", "change-me-to-a-long-random-string"}
MIN_SECRET_LENGTH = 32


def production_problems(s) -> list[str]:
    """What makes these settings unsafe to serve the public. Empty outside production."""
    if s.APP_ENV != "production":
        return []
    problems = []
    secret = s.SESSION_SECRET or ""
    if secret in _DEFAULT_SECRETS or len(secret) < MIN_SECRET_LENGTH:
        problems.append(f"SESSION_SECRET must be set to a random string of at least {MIN_SECRET_LENGTH} characters")
    if s.ALLOW_DEV_LOGIN:
        problems.append("ALLOW_DEV_LOGIN must be false (it lets anyone sign in as any email)")
    if ":postgres@" in (s.DATABASE_URL or ""):
        problems.append("the database password must not be the default 'postgres' (set POSTGRES_PASSWORD)")
    return problems


def refuse_unsafe_production(s) -> None:
    if problems := production_problems(s):
        raise RuntimeError(
            "Refusing to start with APP_ENV=production: " + "; ".join(problems)
            + ". For local development set APP_ENV=development."
        )


settings = Settings()
