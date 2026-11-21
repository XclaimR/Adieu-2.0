from app.schemas.planner_state import PlannerState


class ConversationManager:

    def __init__(self):
        self._conversations: dict[str, PlannerState] = {}

    def get(self, conversation_id: str) -> PlannerState | None:
        return self._conversations.get(conversation_id)

    def create(self, conversation_id: str) -> PlannerState:
        state = PlannerState()
        self._conversations[conversation_id] = state
        return state

    def save(
        self,
        conversation_id: str,
        state: PlannerState
    ):
        self._conversations[conversation_id] = state


conversation_manager = ConversationManager()