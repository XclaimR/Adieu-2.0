import os

from sqlalchemy import create_engine
from dotenv import load_dotenv
from app.core.config import settings

load_dotenv()

engine = create_engine(
    settings.DATABASE_URL
)

with engine.connect() as conn:
    print("Connected!")