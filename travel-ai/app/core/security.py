from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

_ALGORITHM = "HS256"


def create_session_token(user_id: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(days=settings.SESSION_TTL_DAYS)
    return jwt.encode({"sub": user_id, "exp": expires}, settings.SESSION_SECRET, algorithm=_ALGORITHM)


def read_session_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, settings.SESSION_SECRET, algorithms=[_ALGORITHM])
    except jwt.PyJWTError:
        return None
    return payload.get("sub")
