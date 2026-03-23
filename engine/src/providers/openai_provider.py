from typing import Dict, List, Optional
from openai import OpenAI
from .base import LLMProvider


class OpenAIProvider(LLMProvider):
    """OpenAI chat completion provider (GPT-4o family)."""

    _fast_model = "gpt-4o-mini"
    _reasoning_model = "gpt-4o"

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key)

    @property
    def fast_model(self) -> str:
        return self._fast_model

    @property
    def reasoning_model(self) -> str:
        return self._reasoning_model

    def complete(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: Optional[float] = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        kwargs: dict = {"model": model, "messages": messages}
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        response = self.client.chat.completions.create(**kwargs)
        return response.choices[0].message.content.strip()
