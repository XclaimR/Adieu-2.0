from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.models.user import UserModel
from app.schemas.api import UserOut
from app.schemas.taste import Taste


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id: str) -> UserModel | None:
        return self.db.get(UserModel, user_id)

    def upsert(self, google_sub: str, email: str, name: str | None, picture: str | None) -> UserModel:
        user = self.db.scalar(select(UserModel).where(UserModel.google_sub == google_sub))
        if user is None:
            user = UserModel(google_sub=google_sub, email=email, name=name, picture=picture, taste={})
            self.db.add(user)
        else:
            user.email, user.name, user.picture = email, name, picture
        self.db.commit()
        self.db.refresh(user)
        return user

    @staticmethod
    def taste(user: UserModel) -> Taste:
        return Taste.model_validate(user.taste or {})

    def set_taste(self, user: UserModel, taste: Taste) -> None:
        user.taste = taste.model_dump()
        self.db.commit()

    @staticmethod
    def to_out(user: UserModel) -> UserOut:
        taste = UserRepository.taste(user)
        return UserOut(
            id=user.id,
            email=user.email,
            name=user.name,
            picture=user.picture,
            taste=taste,
            has_taste=bool(taste.vibes or taste.likes),
        )
