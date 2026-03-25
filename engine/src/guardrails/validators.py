"""
Custom Guardrails AI Validators (compatible with guardrails-ai 0.9.x)
======================================================================
All validators are self-contained — no hub downloads or external API calls.

Validators
----------
SecretsValidator       — redacts hardcoded credentials / tokens in output
PIIValidator           — redacts emails, phone numbers, credit cards, SSNs
MaliciousCodeValidator — flags dangerous shell/SQL commands (appends notice)
ActionSchemaValidator  — validates decide_action() JSON has required keys
"""

import json
import re
from typing import Any, Dict, List, Optional, Union

from guardrails import register_validator, OnFailAction
from guardrails.validator_base import (
    FailResult,
    PassResult,
    ValidationResult,
    Validator,
)

_REDACT = "[REDACTED]"


# ── Secrets ───────────────────────────────────────────────────────────────────

_SECRET_PATTERNS: List[tuple] = [
    (r"(?i)(api[_\-]?key|apikey|access[_\-]?token|secret[_\-]?key|auth[_\-]?token)"
     r"\s*[:=]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?", "secret"),
    (r"AKIA[0-9A-Z]{16}", "aws_access_key"),
    (r"(?i)aws[_\-]?secret[_\-]?access[_\-]?key\s*[:=]\s*['\"]?([A-Za-z0-9/+]{40})['\"]?", "aws_secret"),
    (r"ghp_[A-Za-z0-9]{36}", "github_token"),
    (r"glpat-[A-Za-z0-9\-]{20}", "gitlab_token"),
    (r"sk-[A-Za-z0-9]{48}", "openai_key"),
    (r"(?i)bearer\s+[A-Za-z0-9\-._~+/]{20,}", "bearer_token"),
    (r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----", "private_key"),
    (r"(?i)(password|passwd|pwd)\s*[:=]\s*['\"]([^'\"]{6,})['\"]", "password"),
    (r"(?i)(mongodb|postgres|mysql|redis|amqp)(\+srv)?://[^@\s]+:[^@\s]+@", "connection_string"),
]

_COMPILED_SECRETS = [(re.compile(p), label) for p, label in _SECRET_PATTERNS]


@register_validator(name="gitsurf/secrets-detector", data_type="string")
class SecretsValidator(Validator):
    """Detects and redacts hardcoded credentials / tokens in LLM output."""

    def validate(self, value: Any, metadata: Optional[Dict] = None) -> ValidationResult:
        text: str = str(value)
        redacted = text
        found: List[str] = []

        for pattern, label in _COMPILED_SECRETS:
            if pattern.search(redacted):
                found.append(label)
                redacted = pattern.sub(_REDACT, redacted)

        if found:
            return FailResult(
                error_message=f"Output contained potential secrets: {', '.join(found)}. They have been redacted.",
                fix_value=redacted,
            )
        return PassResult()


# ── PII ───────────────────────────────────────────────────────────────────────

_PII_PATTERNS: List[tuple] = [
    (r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "email"),
    (r"\b(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}\b", "phone_number"),
    (r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b", "credit_card"),
    (r"\b\d{3}-\d{2}-\d{4}\b", "ssn"),
    (r"\b(192\.168\.|10\.\d+\.|172\.(1[6-9]|2\d|3[0-1])\.)\d+\.\d+\b", "private_ip"),
]

_COMPILED_PII = [(re.compile(p), label) for p, label in _PII_PATTERNS]


@register_validator(name="gitsurf/pii-detector", data_type="string")
class PIIValidator(Validator):
    """Detects and redacts PII (emails, phones, credit cards, SSNs) from LLM output."""

    def validate(self, value: Any, metadata: Optional[Dict] = None) -> ValidationResult:
        text: str = str(value)
        redacted = text
        found: List[str] = []

        for pattern, label in _COMPILED_PII:
            if pattern.search(redacted):
                found.append(label)
                redacted = pattern.sub(_REDACT, redacted)

        if found:
            return FailResult(
                error_message=f"Output contained PII: {', '.join(found)}. They have been redacted.",
                fix_value=redacted,
            )
        return PassResult()


# ── Malicious Code ────────────────────────────────────────────────────────────

_MALICIOUS_PATTERNS: List[tuple] = [
    (r"\brm\s+-rf?\s+/", "destructive_rm"),
    (r"\bmkfs\b", "destructive_mkfs"),
    (r"\bdd\s+if=/dev/zero\b", "destructive_dd"),
    (r":\(\)\s*\{.*\};\s*:", "fork_bomb"),
    (r"(?i)\bDROP\s+(TABLE|DATABASE|SCHEMA)\b", "sql_destructive"),
    (r"(?i)\bTRUNCATE\s+TABLE\b", "sql_truncate"),
    (r"(?i)\b(eval|exec)\s*\(\s*__import__", "python_exec"),
    (r"(?i)subprocess\.(call|run|Popen)\s*\(\s*['\"]?(rm|curl|wget|nc|bash|sh)", "subprocess_shell"),
    (r"(?i)(bash\s+-i\s+>&|/dev/tcp/|nc\s+-[el])", "reverse_shell"),
]

_COMPILED_MALICIOUS = [(re.compile(p, re.DOTALL), label) for p, label in _MALICIOUS_PATTERNS]

_SAFETY_NOTICE = (
    "\n\n> ⚠️ **Safety Notice**: This response contains patterns flagged as potentially "
    "dangerous (`{labels}`). Review carefully before executing any commands."
)


@register_validator(name="gitsurf/malicious-code-detector", data_type="string")
class MaliciousCodeValidator(Validator):
    """
    Flags dangerous shell/SQL patterns. Uses FIX to append a safety notice
    rather than blocking — avoids false positives in a code analysis context.
    """

    def validate(self, value: Any, metadata: Optional[Dict] = None) -> ValidationResult:
        text: str = str(value)
        found: List[str] = []

        for pattern, label in _COMPILED_MALICIOUS:
            if pattern.search(text):
                found.append(label)

        if found:
            notice = _SAFETY_NOTICE.format(labels=", ".join(found))
            return FailResult(
                error_message=f"Potentially dangerous patterns detected: {', '.join(found)}",
                fix_value=text + notice,
            )
        return PassResult()


# ── Action JSON Schema ────────────────────────────────────────────────────────

_VALID_ACTIONS = {"tool_call", "final_answer"}
_TOOL_CALL_REQUIRED = {"tool", "method"}

_SAFE_FALLBACK = {"action": "final_answer", "content": "Agent produced a malformed response."}


@register_validator(name="gitsurf/action-schema", data_type="string")
class ActionSchemaValidator(Validator):
    """
    Validates that decide_action() returns well-formed JSON.
    Accepts serialised JSON string; fixes by returning a safe fallback.
    """

    def validate(self, value: Any, metadata: Optional[Dict] = None) -> ValidationResult:
        # Deserialise if needed
        if isinstance(value, str):
            try:
                obj: Dict = json.loads(value)
            except json.JSONDecodeError:
                return FailResult(
                    error_message="Action response is not valid JSON.",
                    fix_value=json.dumps(_SAFE_FALLBACK),
                )
        else:
            obj = value  # already a dict

        action = obj.get("action")
        if action not in _VALID_ACTIONS:
            return FailResult(
                error_message=f"Invalid action '{action}'. Must be one of: {_VALID_ACTIONS}.",
                fix_value=json.dumps({"action": "final_answer", "content": obj.get("content", str(obj))}),
            )

        if action == "tool_call":
            missing = _TOOL_CALL_REQUIRED - set(obj.keys())
            if missing:
                return FailResult(
                    error_message=f"tool_call missing required keys: {missing}.",
                    fix_value=json.dumps(_SAFE_FALLBACK),
                )

        if action == "final_answer" and "content" not in obj:
            return FailResult(
                error_message="final_answer is missing 'content' key.",
                fix_value=json.dumps({"action": "final_answer", "content": ""}),
            )

        return PassResult()
