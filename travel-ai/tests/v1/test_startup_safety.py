from types import SimpleNamespace

import pytest

from app.core.config import production_problems, refuse_unsafe_production

STRONG = "x" * 40
SAFE_DB = "postgresql+psycopg2://postgres:s3cret-long@postgres:5432/travel_ai"


def _settings(**overrides):
    values = dict(APP_ENV="production", SESSION_SECRET=STRONG, ALLOW_DEV_LOGIN=False, DATABASE_URL=SAFE_DB)
    return SimpleNamespace(**(values | overrides))


def test_safe_production_settings_start():
    refuse_unsafe_production(_settings())


@pytest.mark.parametrize("secret", [None, "", "dev-only-change-me", "change-me-to-a-long-random-string", "short"])
def test_refuses_missing_default_or_short_secret(secret):
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        refuse_unsafe_production(_settings(SESSION_SECRET=secret))


def test_refuses_dev_login_in_production():
    with pytest.raises(RuntimeError, match="ALLOW_DEV_LOGIN"):
        refuse_unsafe_production(_settings(ALLOW_DEV_LOGIN=True))


def test_refuses_default_database_password():
    url = "postgresql+psycopg2://postgres:postgres@postgres:5432/travel_ai"
    with pytest.raises(RuntimeError, match="database password"):
        refuse_unsafe_production(_settings(DATABASE_URL=url))


def test_development_allows_dev_settings():
    dev = _settings(APP_ENV="development", SESSION_SECRET="dev-only-change-me", ALLOW_DEV_LOGIN=True,
                    DATABASE_URL="postgresql+psycopg2://postgres:postgres@postgres:5432/travel_ai")
    assert production_problems(dev) == []


def test_lists_every_problem_at_once():
    assert len(production_problems(_settings(SESSION_SECRET="", ALLOW_DEV_LOGIN=True))) == 2
