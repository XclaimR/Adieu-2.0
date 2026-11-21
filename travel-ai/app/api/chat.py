from fastapi import APIRouter
from app.schemas.chat_request import ChatRequest
from app.schemas.chat_response import ChatResponse
# from app.services.openai_service import OpenAIService
# from app.services.prompt_builder import PromptBuilder
from app.services.chat_service import ChatService

router = APIRouter()

chat_service = ChatService()

@router.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # messages = PromptBuilder.build_messages(request.message)
    # answer = OpenAIService.chat(messages)
    print(request)
    response = chat_service.chat(request)
    print(response)
    return response