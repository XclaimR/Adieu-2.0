from google import genai
from openai import OpenAI
from dotenv import load_dotenv
import os
from app.core.config import settings

load_dotenv()

client = OpenAI(api_key=settings.OPENAI_API_KEY)
# client = genai.Client(api_key=settings.GOOGLE_API_KEY)

class OpenAIService:
    @staticmethod
    def chat(messages):
        response = client.responses.create(
            model="gpt-3.5-turbo",
            input=messages
        )
        return response.output_text