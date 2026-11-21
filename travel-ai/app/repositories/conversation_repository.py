from app.database.models.conversation import ConversationModel
from app.schemas.conversation import Conversation


class ConversationRepository:
    def __init__(self, db):
        self.db = db

    def create(self, title: str) -> Conversation:
        new_conversation = ConversationModel(title=title)
        self.db.add(new_conversation)
        self.db.commit()
        self.db.refresh(new_conversation)
        return Conversation.model_validate(new_conversation)

    def get_by_id(self, conversation_id: str) -> Conversation | None:
        conversation = self.db.get(ConversationModel, conversation_id)
        if conversation:
            return Conversation.model_validate(conversation)
        return None

    # def get_all_conversations(self):
    #     return self.db.query(ConversationModel).all()

    # def update_conversation(self, conversation_id: str, title: str):
    #     conversation = self.get_conversation(conversation_id)
    #     if conversation:
    #         conversation.title = title
    #         self.db.commit()
    #     return conversation

    # def delete_conversation(self, conversation_id: str):
    #     conversation = self.get_conversation(conversation_id)
    #     if conversation:
    #         self.db.delete(conversation)
    #         self.db.commit()
    #     return conversation