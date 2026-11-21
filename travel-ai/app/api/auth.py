from fastapi import APIRouter, Depends, HTTPException, status
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import create_session_token
from app.database.session import get_db
from app.repositories.user_repository import UserRepository
from app.schemas.api import DevLoginRequest, GoogleLoginRequest, LoginResponse

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/config")
def auth_config():
    """Lets the frontend know how sign-in is configured."""
    return {
        "google_client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "dev_login": settings.ALLOW_DEV_LOGIN,
    }


@router.post("/google", response_model=LoginResponse)
def google_login(request: GoogleLoginRequest, db: Session = Depends(get_db)):
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Google sign-in is not configured")
    try:
        claims = id_token.verify_oauth2_token(
            request.id_token, google_requests.Request(), settings.GOOGLE_OAUTH_CLIENT_ID
        )
    except ValueError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid Google token")

    repo = UserRepository(db)
    user = repo.upsert(
        google_sub=claims["sub"],
        email=claims.get("email", ""),
        name=claims.get("name"),
        picture=claims.get("picture"),
    )
    return LoginResponse(token=create_session_token(user.id), user=repo.to_out(user))


@router.post("/dev", response_model=LoginResponse)
def dev_login(request: DevLoginRequest, db: Session = Depends(get_db)):
    if not settings.ALLOW_DEV_LOGIN:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    repo = UserRepository(db)
    user = repo.upsert(google_sub=f"dev:{request.email}", email=request.email, name=request.name, picture=None)
    return LoginResponse(token=create_session_token(user.id), user=repo.to_out(user))
