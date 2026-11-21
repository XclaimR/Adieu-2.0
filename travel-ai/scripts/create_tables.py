from app.database.base import Base
from app.database.session import engine
from dotenv import load_dotenv

load_dotenv()

from app.database.models.conversation import ConversationModel

Base.metadata.create_all(engine)

print("Tables created.")