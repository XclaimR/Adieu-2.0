import json

from google import genai
from dotenv import load_dotenv
import os

from pydantic import BaseModel
from app.core.config import settings

load_dotenv()

client = genai.Client(
    api_key=settings.GOOGLE_API_KEY
)

class GeminiService:

    @staticmethod
    def generate(prompt: str) -> str:
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
        )

        return response.text

    @staticmethod
    def generate_structured(prompt: str, schema: type[BaseModel]):
        response = client.models.generate_content(
            model="gemini-3.1-flash-lite",
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_schema": schema.model_json_schema(),
            },
        )

        data = json.loads(response.text)

        return schema.model_validate(data)