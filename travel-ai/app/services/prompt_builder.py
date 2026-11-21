class PromptBuilder:
    @staticmethod
    def build_messages(user_message: str) -> list[dict]:
        system_prompt = "You are a helpful travel assistant."
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]