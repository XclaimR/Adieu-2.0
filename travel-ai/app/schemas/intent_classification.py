from pydantic import BaseModel

from app.schemas.user_intent import UserIntent


class IntentClassification(BaseModel):
    intent: UserIntent
    