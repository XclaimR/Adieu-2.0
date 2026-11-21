import uuid

from app.conversation.conversation_manager import conversation_manager

from app.schemas.chat_request import ChatRequest
from app.schemas.chat_response import ChatResponse

from app.schemas.planner_state import PlannerState

from app.workflows.travel_planner_workflow import (
    TravelPlannerWorkflow
)


class ChatService:

    def chat(
        self,
        request: ChatRequest
    ) -> ChatResponse:
        conversation_id = request.conversation_id
        if conversation_id is None:
            conversation_id = str(uuid.uuid4())
            state = conversation_manager.create(
                conversation_id
            )
        else:
            state = conversation_manager.get(
                conversation_id
            )
            if state is None:
                state = conversation_manager.create(
                    conversation_id
                )
        state = TravelPlannerWorkflow.process_message(
            state,
            request.message
        )
        conversation_manager.save(
            conversation_id,
            state
        )
        print(ChatResponse(
            conversation_id=conversation_id,
            assistant_message=self._build_response(state),
            profile=state.profile,
            itinerary=state.itinerary
        ))
        return ChatResponse(
            conversation_id=conversation_id,
            assistant_message=self._build_response(state),
            profile=state.profile,
            itinerary=state.itinerary
        )
    @staticmethod
    def _build_response(
        state: PlannerState
    ) -> str:
        if state.itinerary:
            return "I've created your itinerary."
        if state.next_question:
            return state.next_question
        return "How else can I help?"