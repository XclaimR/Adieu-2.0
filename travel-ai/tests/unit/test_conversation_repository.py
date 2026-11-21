from app.database.session import SessionLocal

from app.repositories.conversation_repository import (
    ConversationRepository
)


def test_create_conversation():

    db = SessionLocal()

    repo = ConversationRepository(db)

    conversation = repo.create(
        "Japan Vacation"
    )

    assert conversation.title == "Japan Vacation"

    loaded = repo.get_by_id(
        conversation.id
    )

    assert loaded is not None

    assert loaded.id == conversation.id

    db.close()