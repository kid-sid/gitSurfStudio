from abc import ABC, abstractmethod
from typing import Dict, List, Optional


class LLMProvider(ABC):
    """
    Abstract base for LLM providers.
    Implement this to add a new provider (Anthropic, Gemini, Ollama, etc.).
    """

    @property
    @abstractmethod
    def fast_model(self) -> str:
        """Model name for cheap/fast calls (query refinement, file targeting)."""
        ...

    @property
    @abstractmethod
    def reasoning_model(self) -> str:
        """Model name for reasoning-heavy calls (action loop, search queries)."""
        ...

    @abstractmethod
    def complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: Optional[float] = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        """
        Send a chat completion request and return the response text.
        Raises RuntimeError on failure.
        """
        ...

    def stream_complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: Optional[float] = 0.1,
    ):
        """
        Stream text tokens. Default fallback: yield the full complete() result at once.
        Override in subclasses for real token-by-token streaming.
        """
        yield self.complete(messages, model, temperature=temperature)
