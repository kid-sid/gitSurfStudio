from typing import List, Optional

class NotifyUserTool:
    """
    Tool to pause agent execution and request human feedback or approval.
    """

    def __init__(self, ask_callback):
        """
        Args:
            ask_callback: A function `ask(question, options)` that blocks and returns the user's string answer.
                          In AgentExecutor, this corresponds to `self._ask_user`.
        """
        self.ask_callback = ask_callback

    def notify_user(
        self,
        message: str,
        paths_to_review: Optional[List[str]] = None,
        blocked_on_user: bool = True
    ) -> str:
        """
        Notifies the user and optionally blocks until they respond.
        
        Args:
            message: The message to show the user.
            paths_to_review: Optional files for the user to look at (e.g., ['implementation_plan.md']).
            blocked_on_user: If true, pauses execution until the user responds.
        """
        prompt = message
        if paths_to_review:
            prompt += f"\n\nPlease review these files:\n" + "\n".join(f"- {p}" for p in paths_to_review)
            
        if not blocked_on_user:
            # In a real async/UI environment, maybe we just emit a message.
            # But the primary use case is blocking for approval.
            try:
                # Fast unblocked emit if supported, but here we'll just block with an "Ok" default
                return self.ask_callback(prompt, ["Ok"])
            except Exception as e:
                return f"[Error] Could not notify user: {e}"

        try:
            response = self.ask_callback(prompt)
            return f"[User Response] {response}"
        except Exception as e:
            return f"[Error] Failed to wait for user approval: {e}"
