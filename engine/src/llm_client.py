import re
import os
import json
from typing import List, Dict, Any, Optional

from src.providers.base import LLMProvider
from src.providers.openai_provider import OpenAIProvider
from src.prompts import (
    refine_query_prompt,
    identify_relevant_files_prompt,
    generate_search_queries_prompt,
    analyze_project_context_prompt,
    generate_questions_prompt,
    github_search_query_prompt,
    decide_action_prompt,
)


def _build_provider(provider_name: str) -> Optional[LLMProvider]:
    """Factory: instantiate the correct provider from an env-configured name."""
    if provider_name == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if api_key:
            return OpenAIProvider(api_key=api_key)
        print("Warning: OPENAI_API_KEY not set. LLM calls will fail.")
        return None
    # Add more providers here:
    # elif provider_name == "anthropic":
    #     return AnthropicProvider(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return None


def _extract_json_object(text: str) -> Optional[Dict]:
    """Extract the first {...} JSON object from a string."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _extract_json_array(text: str) -> Optional[List]:
    """Extract the first [...] JSON array from a string."""
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return None
    try:
        result = json.loads(match.group(0))
        return result if isinstance(result, list) else None
    except json.JSONDecodeError:
        return None


class LLMClient:
    """
    High-level LLM client. Handles prompt construction, JSON parsing,
    and graceful fallbacks. Provider-specific details live in src/providers/.

    Provider is selected by passing provider= or via the LLM_PROVIDER env var.
    Supported: "openai" (default), "mock" (tests/offline).
    """

    def __init__(self, provider: str = ""):
        self.provider = provider or os.getenv("LLM_PROVIDER", "openai")
        self._provider: Optional[LLMProvider] = (
            None if self.provider == "mock" else _build_provider(self.provider)
        )

    # ── Backwards-compatibility properties ───────────────────────────

    @property
    def client(self):
        """Direct access to the underlying SDK client (used by autocomplete)."""
        if isinstance(self._provider, OpenAIProvider):
            return self._provider.client
        return None

    @property
    def fast_model(self) -> str:
        return self._provider.fast_model if self._provider else "gpt-4o-mini"

    @property
    def reasoning_model(self) -> str:
        return self._provider.reasoning_model if self._provider else "gpt-4o"

    # ── Internal call helper ─────────────────────────────────────────

    def _call(
        self,
        messages: List[Dict[str, str]],
        model: str,
        temperature: Optional[float] = 0.1,
        max_tokens: Optional[int] = None,
    ) -> str:
        if self._provider is None:
            raise RuntimeError(f"LLM provider '{self.provider}' not configured.")
        return self._provider.complete(messages, model, temperature=temperature, max_tokens=max_tokens)

    # ── Public methods ───────────────────────────────────────────────

    def refine_user_query(
        self,
        user_question: str,
        history: Optional[List[Dict[str, Any]]] = None,
        project_context: str = "",
        file_structure: str = "",
    ) -> Dict[str, Any]:
        """Refines a vague query into a structured intent dict."""
        if self.provider == "mock":
            return {
                "intent": "Search for code related to the question.",
                "refined_question": user_question,
                "keywords": user_question.split(),
                "is_action_request": False,
            }

        history_str = ""
        if history:
            history_str = "Recent Conversation History:\n" + "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in history[-5:]
            ) + "\n"

        prompt = refine_query_prompt(user_question, history_str, project_context, file_structure)
        try:
            content = self._call(
                [
                    {"role": "system", "content": "You are a helpful assistant. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                self.fast_model,
            )
            data = _extract_json_object(content)
            if data:
                data.setdefault("is_action_request", False)
                data.setdefault("is_overview_question", False)
                data.setdefault("target_files", [])
                data.setdefault("action_type", None)
                data.setdefault("direct_tool_call", None)
                return data
        except Exception as e:
            print(f"[Query Expansion] Warning: Failed to refine query: {e}")

        return {
            "intent": "General code search",
            "refined_question": user_question,
            "keywords": [],
            "is_action_request": False,
            "is_overview_question": False,
        }

    def identify_relevant_files(
        self,
        user_question: str,
        file_structure: str,
        symbol_minimap: Optional[Dict] = None,
    ) -> List[str]:
        """Returns files most likely to contain the answer."""
        if self.provider == "mock":
            return []

        minimap_hint = ""
        if symbol_minimap:
            parts = []
            for path, entry in list(symbol_minimap.items())[:50]:
                symbols = entry if isinstance(entry, list) else entry.get("symbols", [])
                file_keywords = [] if isinstance(entry, list) else entry.get("keywords", [])
                sym_strs = []
                for s in symbols[:10]:
                    sig = s.get("signature", "")
                    doc = f" - {s['doc']}" if s.get("doc") else ""
                    sym_strs.append(f"{s['name']}{sig}{doc}")
                kw_str = f"  Keywords: {', '.join(file_keywords)}" if file_keywords else ""
                parts.append(
                    f"### {path}\n" + "\n".join(f"  * {ss}" for ss in sym_strs)
                    + (f"\n{kw_str}" if kw_str else "")
                )
            minimap_hint = "\nSymbol MiniMap (Classes, Functions, Signatures, Keywords):\n" + "\n".join(parts) + "\n"

        prompt = identify_relevant_files_prompt(user_question, file_structure, minimap_hint)
        try:
            content = self._call(
                [
                    {"role": "system", "content": "You are a helpful assistant. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                self.fast_model,
            )
            files = _extract_json_array(content)
            if files is not None:
                print(f"[Skeleton] Identified {len(files)} relevant files: {files}")
                return files[:8]
            print(f"[Skeleton] Warning: No JSON list found in response: {content[:100]}...")
        except Exception as e:
            print(f"[Skeleton] Warning: Could not parse file list: {e}")

        return []

    def generate_search_queries(
        self,
        user_question: str,
        tool: str = "ripgrep",
        history: Optional[List[Dict]] = None,
        project_context: str = "",
        file_structure: str = "",
        repo_name: str = "",
        language_hint: str = "",
    ) -> List[str]:
        if self.provider == "mock":
            return [w for w in user_question.split() if len(w) > 3]

        history_str = ""
        if history:
            history_str = "Conversation History:\n" + "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in history
            ) + "\n"

        structure_hint = ""
        if file_structure:
            structure_hint = (
                f"\nProject File Structure:\n```\n{file_structure[:2000]}\n```\n"
                "Use this structure to generate targeted queries.\n"
            )

        prompt = (
            github_search_query_prompt(user_question, repo_name=repo_name, language_hint=language_hint)
            if tool == "github"
            else generate_search_queries_prompt(user_question, project_context, structure_hint, history_str)
        )

        try:
            content = self._call(
                [
                    {
                        "role": "system",
                        "content": "You are a code search specialist. Return only search queries, one per line.",
                    },
                    {"role": "user", "content": prompt},
                ],
                self.reasoning_model,
                temperature=None,  # use API default
            )
            content = content.replace("```", "").strip()
            queries = [line.strip() for line in content.splitlines() if line.strip()]
            if not queries:
                print("[SearchQueries] Warning: LLM returned empty query list.")
                return self._fallback_queries(user_question)
            print(f"[SearchQueries] Generated {len(queries)} queries.")
            return queries
        except Exception as e:
            print(f"[SearchQueries] Warning: API call failed: {e}")
            return self._fallback_queries(user_question)

    def _fallback_queries(self, user_question: str) -> List[str]:
        stop_words = {
            "how", "do", "i", "the", "a", "an", "is", "in", "to",
            "what", "does", "can", "for", "of", "and", "or", "my",
            "this", "that", "it", "with", "on", "at", "from", "be",
        }
        return [w for w in user_question.lower().split() if len(w) > 3 and w not in stop_words][:5]

    def decide_action(
        self,
        user_question: str,
        context: str,
        project_structure: str = "",
        history: Optional[List[Dict]] = None,
        available_tools: str = "",
        current_iteration: int = 1,
        max_iterations: int = 5,
    ) -> Dict:
        if self.provider == "mock":
            return {"action": "final_answer", "content": "[Mock Final Answer]"}

        history_str = ""
        if history:
            recent = history[-10:]
            history_str = "".join(
                f"{m['role'].upper()}: {m['content']}\n" for m in recent
            )

        prompt = decide_action_prompt(
            user_question,
            context,
            history_str,
            project_structure,
            available_tools,
            current_iteration=current_iteration,
            max_iterations=max_iterations,
        )

        try:
            content = self._call(
                [
                    {
                        "role": "system",
                        "content": "You are an autonomous software agent. "
                                   "Return ONLY a valid JSON object — no markdown, no explanation.",
                    },
                    {"role": "user", "content": prompt},
                ],
                self.reasoning_model,
                temperature=None,
            )
            # Strip markdown fences if model ignores instructions
            if content.startswith("```"):
                lines = content.splitlines()
                content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

            data = _extract_json_object(content)
            if data:
                return data
            # Model returned plain text — treat as final answer
            return {"action": "final_answer", "content": content}

        except Exception as e:
            print(f"[Action Loop] JSON parse error or API failure: {e}")
            return {
                "action": "final_answer",
                "content": f"Agent encountered an error: {e}",
                "thought": "Exception raised during API call.",
            }

    def stream_final_answer(
        self,
        question: str,
        context: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """
        Generates the final answer using streaming.
        Tokens are printed to stdout as [ANSWER_TOKEN]<json-encoded-token>
        so the server can forward them to the frontend in real time.
        Returns the complete answer string.
        """
        if self.provider == "mock":
            answer = "[Mock Final Answer]"
            print(f"[ANSWER_TOKEN]{json.dumps(answer)}")
            return answer

        history_str = ""
        if history:
            history_str = "".join(
                f"{m['role'].upper()}: {m['content']}\n" for m in history[-5:]
            )

        prompt = (
            f"<conversation_history>\n{history_str}\n</conversation_history>\n\n"
            f"<context>\n{context[:18000]}\n</context>\n\n"
            f"Answer the following question thoroughly using the context above.\n"
            f"Use Markdown: headings, bullet points, **bold**, and fenced code blocks where appropriate.\n\n"
            f"Question: {question}"
        )

        messages = [
            {"role": "system", "content": "You are an expert AI coding assistant. Answer precisely and helpfully."},
            {"role": "user", "content": prompt},
        ]

        full_answer = ""
        try:
            for token in self._provider.stream_complete(messages, self.reasoning_model, temperature=0.1):
                full_answer += token
                # JSON-encode token so embedded newlines don't break the line protocol
                print(f"[ANSWER_TOKEN]{json.dumps(token)}")
        except Exception as e:
            fallback = f"Error generating answer: {e}"
            full_answer = fallback
            print(f"[ANSWER_TOKEN]{json.dumps(fallback)}")

        return full_answer

    def analyze_project_context(self, readme_content: str) -> str:
        if not readme_content or not readme_content.strip():
            return ""
        if self.provider == "mock":
            return "Mock Project Context"
        try:
            return self._call(
                [
                    {"role": "system", "content": "You are a technical documentation analyst. Be concise and precise."},
                    {"role": "user", "content": analyze_project_context_prompt(readme_content)},
                ],
                self.fast_model,
            )
        except Exception as e:
            print(f"[ProjectContext] Warning: {e}")
            return ""

    def generate_questions(self, context: str, num: int = 5) -> str:
        if self.provider == "mock":
            return "1. **Question**: What is this?\n   - **Answer**: A mock project."
        try:
            return self._call(
                [
                    {"role": "system", "content": "You are a helpful assistant."},
                    {"role": "user", "content": generate_questions_prompt(context[:50000], num)},
                ],
                self.reasoning_model,
                temperature=None,
            )
        except Exception as e:
            return f"Error generating questions: {e}"
