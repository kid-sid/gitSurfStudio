"""
Security Event Logger
=====================
Writes prompt-injection attempts to the `security_events` Supabase table
using the REST API with the service-role key (bypasses RLS).

Falls back to a local JSONL file if Supabase is not configured so that
no events are ever silently discarded.
"""

import json
import os
import threading
from datetime import datetime, timezone
from typing import Optional

import requests

from src.logger import get_logger
from src.security.prompt_guard import GuardResult

logger = get_logger("security_logger")

_SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
_LOCAL_FALLBACK = os.path.join(os.path.dirname(__file__), "..", "..", "security_events.jsonl")
_lock = threading.Lock()


def log_security_event(
    *,
    query: str,
    result: GuardResult,
    user_id: Optional[str] = None,
    ip_address: Optional[str] = None,
    blocked: bool,
) -> None:
    """
    Fire-and-forget: logs the event in a background thread so it never
    slows down the request path.
    """
    event = {
        "user_id": user_id,
        "query": query[:2000],          # cap stored query length
        "patterns_detected": [
            {"category": d.category, "severity": d.severity, "matched_text": d.matched_text}
            for d in result.detections
        ],
        "severity": result.severity,
        "ip_address": ip_address,
        "blocked": blocked,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    threading.Thread(target=_write_event, args=(event,), daemon=True).start()


def _write_event(event: dict) -> None:
    if _SUPABASE_URL and _SERVICE_KEY:
        _write_to_supabase(event)
    else:
        _write_to_local_fallback(event)


def _write_to_supabase(event: dict) -> None:
    url = f"{_SUPABASE_URL}/rest/v1/security_events"
    headers = {
        "apikey": _SERVICE_KEY,
        "Authorization": f"Bearer {_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }
    try:
        resp = requests.post(url, json=event, headers=headers, timeout=5)
        if not resp.ok:
            logger.warning("Supabase security log failed (%s): %s", resp.status_code, resp.text[:200])
            _write_to_local_fallback(event)   # failover
    except Exception as e:
        logger.warning("Supabase security log error: %s", e)
        _write_to_local_fallback(event)


def _write_to_local_fallback(event: dict) -> None:
    try:
        with _lock:
            with open(_LOCAL_FALLBACK, "a", encoding="utf-8") as f:
                f.write(json.dumps(event) + "\n")
    except Exception as e:
        logger.error("Security event local fallback write failed: %s", e)
