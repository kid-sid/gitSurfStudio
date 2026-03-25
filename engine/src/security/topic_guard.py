"""
Topic / Content Policy Guard
=============================
Ensures the assistant only answers software-engineering and code-related
questions, gracefully refusing everything else.

Three-tier classification
--------------------------
1. Fast-allow  — regex signals that are unambiguously code-related.
                 Returns ALLOWED immediately without an LLM call.
2. Fast-reject  — regex signals that are unambiguously off-topic.
                 Returns REJECTED immediately without an LLM call.
3. LLM classify — ambiguous queries are sent to the fast LLM for a
                  binary yes/no judgment. Falls back to ALLOWED on
                  any API failure (prefer false-negatives over blocking
                  legitimate questions).
"""

import re
from dataclasses import dataclass
from typing import Optional

# ── Fast-allow patterns ───────────────────────────────────────────────────────
# Any query matching one of these is immediately treated as code-related.
_ALLOW_PATTERNS = [
    # Core programming concepts
    r"\b(function|method|class|object|module|package|library|framework|api|sdk)\b",
    r"\b(variable|constant|parameter|argument|return|type|interface|struct|enum)\b",
    r"\b(bug|error|exception|stacktrace|traceback|crash|fix|debug|breakpoint)\b",
    r"\b(refactor|optimize|performance|memory|leak|async|await|promise|thread)\b",
    r"\b(import|export|require|dependency|install|build|compile|transpile|bundle)\b",
    r"\b(test|unit\s*test|integration\s*test|mock|stub|fixture|coverage|assertion)\b",
    r"\b(deploy|docker|container|kubernetes|ci/?cd|pipeline|devops|nginx|server)\b",
    r"\b(database|sql|query|schema|migration|index|orm|crud|endpoint|rest|graphql)\b",
    r"\b(git|commit|branch|merge|pull\s*request|pr|fork|clone|rebase|diff|stash)\b",
    r"\b(file|directory|path|read|write|parse|serialize|json|yaml|toml|config)\b",
    r"\b(algorithm|data\s*structure|complexity|recursion|iteration|loop|sorting)\b",
    r"\b(authentication|authorization|oauth|jwt|token|session|cookie|cors|csrf)\b",
    r"\b(frontend|backend|fullstack|component|render|dom|css|html|svelte|react|vue)\b",
    r"\b(llm|embeddings|vector|model|inference|fine.?tun|prompt|rag|langchain)\b",
    # Code-like syntax
    r"[`'\"][\w\./]+\.(py|js|ts|svelte|json|yaml|toml|rs|go|java|cpp|c|sh|sql)[`'\"]",
    r"\b\w+\.\w+\s*\(",   # method call pattern: foo.bar(
    r"def |class |import |from .+ import|const |let |var |function |=>|->|::",
    r"\b(how\s+does|what\s+is|explain|where\s+is|show\s+me).{0,40}"
    r"(implement|work|function|defined|used|called|return)",
    r"\b(what|how|why|where).{0,30}(code|codebase|project|repo|file|module)\b",
]

# ── Fast-reject patterns ──────────────────────────────────────────────────────
# Unambiguously off-topic — reject without an LLM call.
_REJECT_PATTERNS = [
    # Factual / trivia
    r"\b(capital\s+of|population\s+of|who\s+invented|when\s+was\s+.+born)\b",
    r"\b(weather|forecast|temperature)\s+(in|at|for)\b",
    r"\b(recipe|ingredient|cook|bake|calories|nutrition)\b",
    r"\b(movie|film|actor|actress|director|oscar|grammy|emmy)\b",
    r"\b(sports?|football|basketball|soccer|nfl|nba|fifa|world\s+cup|score)\b",
    r"\b(stock\s+price|crypto\s+price|bitcoin|exchange\s+rate)\b",
    r"\b(lottery|gambling|casino|poker|blackjack)\b",
    # Personal / social
    r"\b(relationship\s+advice|dating|breakup|divorce|marriage\s+advice)\b",
    r"\b(my\s+horoscope|zodiac|astrology|tarot)\b",
    r"\b(diet\s+plan|weight\s+loss\s+tips|workout\s+routine)\b",
    # Harmful content (in non-code context)
    r"\b(how\s+to\s+make\s+a\s+bomb|synthesize\s+drugs?|buy\s+illegal)\b",
    # Political / controversial
    r"\b(vote\s+for|political\s+party|election\s+results|which\s+president)\b",
    r"\b(best\s+religion|should\s+i\s+believe|is\s+god\s+real)\b",
]

_COMPILED_ALLOW = [re.compile(p, re.IGNORECASE) for p in _ALLOW_PATTERNS]
_COMPILED_REJECT = [re.compile(p, re.IGNORECASE) for p in _REJECT_PATTERNS]

# ── LLM classifier prompt ─────────────────────────────────────────────────────
_SYSTEM_PROMPT = (
    "You are a topic classifier for a software-engineering AI assistant. "
    "Determine whether the user's query is related to software engineering, "
    "programming, codebases, developer tools, or technical computing.\n\n"
    "Reply with ONLY a JSON object: {\"code_related\": true} or {\"code_related\": false}.\n"
    "When in doubt, answer true."
)

# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class TopicResult:
    allowed: bool
    reason: str          # "fast_allow" | "fast_reject" | "llm_allow" | "llm_reject" | "fallback_allow"
    tier: str            # "fast" | "llm" | "fallback"

    @property
    def refusal_message(self) -> str:
        return (
            "I'm GitSurf AI — a code assistant. I can only help with software engineering, "
            "programming, debugging, codebases, and developer tools.\n\n"
            "Feel free to ask about your code!"
        )


class TopicGuard:
    """
    Classifies queries as code-related or off-topic.

    Usage
    -----
        guard = TopicGuard(llm_client)   # llm_client may be None
        result = guard.classify(query)
        if not result.allowed:
            return result.refusal_message
    """

    def __init__(self, llm=None):
        self._llm = llm   # LLMClient instance — optional, used for tier-3

    def classify(self, query: str) -> TopicResult:
        stripped = query.strip()

        # ── Tier 1: fast allow ────────────────────────────────────────────
        for pattern in _COMPILED_ALLOW:
            if pattern.search(stripped):
                return TopicResult(allowed=True, reason="fast_allow", tier="fast")

        # ── Tier 2: fast reject ───────────────────────────────────────────
        for pattern in _COMPILED_REJECT:
            if pattern.search(stripped):
                return TopicResult(allowed=False, reason="fast_reject", tier="fast")

        # ── Tier 3: LLM classifier ────────────────────────────────────────
        if self._llm is not None:
            try:
                return self._llm_classify(stripped)
            except Exception:
                pass  # fall through to fallback

        # ── Fallback: allow (prefer false-negatives over blocking) ────────
        return TopicResult(allowed=True, reason="fallback_allow", tier="fallback")

    def _llm_classify(self, query: str) -> TopicResult:
        import json as _json

        content = self._llm._call(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query[:500]},  # cap to save tokens
            ],
            model=self._llm.fast_model,
            temperature=0.0,
            max_tokens=20,
        )

        # Parse {"code_related": true/false}
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1:
            try:
                data = _json.loads(content[start:end + 1])
                is_related = bool(data.get("code_related", True))
                reason = "llm_allow" if is_related else "llm_reject"
                return TopicResult(allowed=is_related, reason=reason, tier="llm")
            except _json.JSONDecodeError:
                pass

        # Could not parse — allow
        return TopicResult(allowed=True, reason="fallback_allow", tier="fallback")
