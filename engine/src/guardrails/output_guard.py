"""
Guardrails AI — Output Guard (guardrails-ai 0.9.x)
====================================================
Two validation paths:

  validate_answer(text)  — runs SecretsValidator → PIIValidator → MaliciousCodeValidator
                           sequentially so each fix feeds the next validator.
                           Uses validators directly (Guard chaining breaks fix propagation in 0.9.x).

  validate_action(dict)  — runs ActionSchemaValidator via a single Guard.

Both functions degrade gracefully if guardrails-ai is not installed.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("output_guard")

try:
    from guardrails import Guard, OnFailAction
    from guardrails.validator_base import FailResult

    from src.guardrails.validators import (  # noqa: F401 — side-effect: registers validators
        ActionSchemaValidator,
        MaliciousCodeValidator,
        PIIValidator,
        SecretsValidator,
    )

    # Instantiate once — validators are stateless
    _secrets_v = SecretsValidator(on_fail=OnFailAction.FIX)
    _pii_v = PIIValidator(on_fail=OnFailAction.FIX)
    _malicious_v = MaliciousCodeValidator(on_fail=OnFailAction.FIX)

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False
    logger.warning(
        "guardrails-ai is not installed — LLM output validation disabled. "
        "Run: pip install guardrails-ai"
    )

# Lazy singleton for action guard (single validator — Guard works fine)
_action_guard: Any = None


def _get_action_guard():
    global _action_guard
    if _action_guard is None:
        _action_guard = Guard().use(ActionSchemaValidator(on_fail=OnFailAction.FIX))
    return _action_guard


# ── Public API ────────────────────────────────────────────────────────────────

def validate_answer(answer: str) -> Tuple[str, List[str]]:
    """
    Validate and sanitise a final answer string.

    Applies validators sequentially so each fix feeds the next:
      1. SecretsValidator  — redact credentials
      2. PIIValidator      — redact PII
      3. MaliciousCodeValidator — append safety notice

    Returns
    -------
    (sanitised_answer, list_of_warning_messages)
    """
    if not _AVAILABLE or not answer:
        return answer, []

    current = answer
    warnings: List[str] = []

    for validator in (_secrets_v, _pii_v, _malicious_v):
        try:
            result = validator.validate(current)
            if isinstance(result, FailResult):
                warnings.append(result.error_message)
                logger.warning("[AnswerGuard] %s", result.error_message)
                if result.fix_value is not None:
                    current = result.fix_value
        except Exception as e:
            logger.error("[AnswerGuard] %s.validate error: %s", type(validator).__name__, e)

    return current, warnings


def validate_action(action_dict: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validate the JSON dict returned by decide_action().

    Serialises to a JSON string for the Guard, then deserialises the
    (possibly fixed) output back to a dict.

    Returns
    -------
    (validated_dict, list_of_warning_messages)
    """
    if not _AVAILABLE or not isinstance(action_dict, dict):
        return action_dict, []

    warnings: List[str] = []
    try:
        raw = json.dumps(action_dict)
        outcome = _get_action_guard().validate(raw)
        validated_raw = outcome.validated_output if outcome.validated_output is not None else raw

        for summary in outcome.validation_summaries or []:
            msg = getattr(summary, "failure_reason", None) or str(summary)
            if msg:
                warnings.append(msg)
                logger.warning("[ActionGuard] %s", msg)

        if isinstance(validated_raw, str):
            validated_raw = json.loads(validated_raw)

        return validated_raw, warnings
    except Exception as e:
        logger.error("[ActionGuard] validate_action error: %s", e)
        return action_dict, []
