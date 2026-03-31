"""
Prompt Injection Guard
======================
Detects prompt injection / jailbreak attempts before they reach the LLM.

Severity levels
---------------
  high   → request is BLOCKED and the attempt is logged to Supabase
  medium → request is ALLOWED but logged (possible false positive in code context)
  low    → request is ALLOWED and logged silently

Each pattern is a tuple of (compiled_regex, category, severity).
"""

import re
from dataclasses import dataclass, field
from typing import List, Tuple


# ── Pattern registry ──────────────────────────────────────────────────────────
# (regex pattern, category, severity)
_RAW_PATTERNS: List[Tuple[str, str, str]] = [
    # Instruction override
    (r"ignore\s+(all\s+)?(previous|prior|above|your|the)\s+(instructions?|rules?|guidelines?|directives?|prompts?|constraints?|system)", "instruction_override", "high"),
    (r"disregard\s+(all\s+)?(previous|prior|above|your|the)\s+(instructions?|rules?|guidelines?|constraints?)", "instruction_override", "high"),
    (r"forget\s+(everything|all|your\s+instructions?|what\s+(you\s+were|you'?ve\s+been)\s+told)", "instruction_override", "high"),
    (r"override\s+(your\s+)?(instructions?|programming|directives?|rules?)", "instruction_override", "high"),
    (r"(new|updated?)\s+(instructions?|prompt|rules?|directives?)\s*:", "instruction_override", "medium"),

    # Role / identity hijacking
    (r"\byou\s+are\s+now\b.{0,60}(ai|bot|assistant|model|gpt|llm)", "role_hijack", "high"),
    (r"\bact\s+as\b.{0,40}\b(without|no|ignoring)\b.{0,30}\b(restriction|limit|filter|rule|guideline|safety)", "role_hijack", "high"),
    (r"\bpretend\s+(you\s+are|to\s+be|you'?re)\b.{0,40}\b(without|no|unrestricted|unlimited)", "role_hijack", "high"),
    (r"\broleplay\s+as\b.{0,40}\b(without|no|unrestricted)", "role_hijack", "medium"),

    # Known jailbreak names / techniques
    (r"\b(dan|dude|stan|jailbreak|dev\s*mode|developer\s*mode|god\s*mode)\b.{0,30}\b(mode|prompt|enabled?|activated?|unlock)", "jailbreak", "high"),
    (r"\bunrestricted\s+(ai|mode|version|access)\b", "jailbreak", "high"),
    (r"\b(do\s+anything\s+now|no\s+restrictions?|no\s+limits?|no\s+rules?)\b", "jailbreak", "high"),

    # System prompt / instruction extraction
    (r"(reveal|show|print|display|output|repeat|tell\s+me|what\s+(is|are))\s+(your|the)\s+(system\s+)?(prompt|instructions?|guidelines?|rules?|context)", "prompt_extraction", "high"),
    (r"(what\s+were\s+you\s+told|your\s+(original|initial|actual)\s+(instructions?|prompt|context))", "prompt_extraction", "high"),
    (r"print\s+(everything|all)\s+(above|before|prior)", "prompt_extraction", "medium"),

    # Privilege / capability escalation
    (r"\b(admin|root|sudo|superuser|developer)\s+(mode|access|override|privileges?|panel)\b", "privilege_escalation", "high"),
    (r"\bbypass\s+(all\s+)?(filter|restriction|limit|rule|safeguard|guardrail|safety|moderation)\b", "bypass_attempt", "high"),
    (r"\bdisable\s+(all\s+)?(filter|safety|restriction|guardrail|moderation)\b", "bypass_attempt", "high"),

    # Token / delimiter injection (LLM prompt boundary attacks)
    (r"<\|?(system|im_start|im_end)\|?>", "token_injection", "high"),
    (r"\[INST\]|\[/INST\]|<<SYS>>|<</SYS>>|\[SYSTEM\]", "token_injection", "high"),
    (r"#{3,}\s*(system|instruction|prompt|context)", "token_injection", "medium"),

    # Encoded / obfuscated payloads
    (r"(?:[A-Za-z0-9+/]{40,}={0,2})", "encoded_payload", "medium"),  # long base64 blobs

    # Social engineering
    (r"(hypothetically|theoretically|in\s+fiction|in\s+a\s+story|for\s+educational\s+purposes)\s*.{0,60}(how\s+to|steps?\s+to|instructions?\s+(for|to))\s*.{0,40}(hack|exploit|attack|bypass|break)", "social_engineering", "medium"),
    (r"my\s+(grandmother|grandma|teacher|professor).{0,60}(used\s+to\s+)?(tell|explain|show)\s+me\s+how\s+to", "social_engineering", "low"),
]

# Compile all patterns once at import time
PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(pat, re.IGNORECASE | re.DOTALL), cat, sev)
    for pat, cat, sev in _RAW_PATTERNS
]

_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}


@dataclass
class Detection:
    category: str
    severity: str
    matched_text: str


@dataclass
class GuardResult:
    is_safe: bool                       # False → block the request
    severity: str                       # highest severity found ("none" if clean)
    detections: List[Detection] = field(default_factory=list)

    @property
    def should_log(self) -> bool:
        return len(self.detections) > 0

    def to_dict(self) -> dict:
        return {
            "is_safe": self.is_safe,
            "severity": self.severity,
            "detections": [
                {"category": d.category, "severity": d.severity, "matched_text": d.matched_text}
                for d in self.detections
            ],
        }


class PromptGuard:
    """
    Scans a query for prompt injection indicators.

    Usage
    -----
        guard = PromptGuard()
        result = guard.scan(user_query)
        if not result.is_safe:
            raise HTTPException(400, "Query blocked by security policy")
    """

    # Queries longer than this are treated as an automatic medium-severity flag
    MAX_SAFE_LENGTH = 8_000

    def scan(self, query: str) -> GuardResult:
        detections: List[Detection] = []
        highest = 0

        for pattern, category, severity in PATTERNS:
            match = pattern.search(query)
            if match:
                matched_text = match.group(0)[:120]  # truncate for storage
                detections.append(Detection(category=category, severity=severity, matched_text=matched_text))
                highest = max(highest, _SEVERITY_RANK[severity])

        # Extra heuristic: abnormally long query with no code context
        if len(query) > self.MAX_SAFE_LENGTH and not self._looks_like_code(query):
            detections.append(Detection(
                category="abnormal_length",
                severity="medium",
                matched_text=f"Query length: {len(query)} chars",
            ))
            highest = max(highest, _SEVERITY_RANK["medium"])

        severity_label = {0: "none", 1: "low", 2: "medium", 3: "high"}[highest]

        # Block on high severity only; log medium/low but allow through
        is_safe = highest < _SEVERITY_RANK["high"]

        return GuardResult(is_safe=is_safe, severity=severity_label, detections=detections)

    @staticmethod
    def _looks_like_code(text: str) -> bool:
        """Rough check — does the text contain code-like tokens?"""
        code_signals = ["def ", "class ", "import ", "function ", "const ", "var ", "=>", "->", "{}"]
        return any(sig in text for sig in code_signals)
