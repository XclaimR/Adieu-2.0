from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.security import read_session_token
from app.database.models.user import UserModel
from app.database.session import get_db
from app.repositories.user_repository import UserRepository


def current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> UserModel:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")

    user_id = read_session_token(authorization.split(" ", 1)[1])
    user = UserRepository(db).get(user_id) if user_id else None
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired")
    return user
