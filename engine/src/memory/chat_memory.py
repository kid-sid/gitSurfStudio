"""
ChatMemory — Persists and summarizes chat history in Supabase.

Design:
  - Each user×repo combination has one or more "sessions" (conversation threads).
  - Messages are stored sequentially (idx 0, 1, 2, …).
  - When unsummarized messages exceed SUMMARIZE_THRESHOLD, the older ones are
    rolled into a LLM-generated summary. Recent messages stay verbatim.
  - The LLM receives [system(summary)] + [recent N messages] instead of the
    full raw history, keeping context bounded while preserving actionable info.

All Supabase writes are fire-and-forget background threads.
Falls back gracefully (returns empty context) if Supabase is unavailable.
"""

import os
import json
import threading
from typing import Optional, List, Dict, Any

import requests

from src.logger import get_logger

logger = get_logger("chat_memory")

_SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")

# When total unsummarized messages exceed this, trigger background summarization
SUMMARIZE_THRESHOLD = 10
# How many recent messages to keep verbatim (not included in summary)
RECENT_KEEP = 6


def _headers() -> Dict[str, str]:
    return {
        "apikey": _SERVICE_KEY,
        "Authorization": f"Bearer {_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


class ChatMemory:
    """
    Manages persistent, summarized chat sessions per (user, repo).
    """

    def __init__(self, llm_client=None):
        """
        Args:
            llm_client: Optional LLMClient instance for summarization.
                        If None, summarization is skipped.
        """
        self._available = bool(_SUPABASE_URL and _SERVICE_KEY)
        self._llm = llm_client
        if not self._available:
            logger.debug("ChatMemory: Supabase not configured — operating in local-only mode")

    # ── Session management ─────────────────────────────────────────────────────

    def get_or_create_session(self, user_id: str, repo_identifier: str) -> Optional[str]:
        """
        Returns the most recent session ID for this user+repo, creating one if none exists.
        Returns None if Supabase is unavailable.
        """
        if not self._available:
            return None

        # Try to find the most recent session
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {
            "user_id": f"eq.{user_id}",
            "repo_identifier": f"eq.{repo_identifier}",
            "select": "id",
            "order": "updated_at.desc",
            "limit": "1",
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=5)
            if resp.ok:
                data = resp.json()
                if data:
                    return data[0]["id"]
        except Exception as e:
            logger.warning("ChatMemory.get_or_create_session (get) error: %s", e)
            return None

        # No session found — create one
        return self.create_session(user_id, repo_identifier)

    def create_session(self, user_id: str, repo_identifier: str, title: Optional[str] = None) -> Optional[str]:
        """Create a new chat session. Returns session_id or None."""
        if not self._available:
            return None
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        payload: Dict[str, Any] = {"user_id": user_id, "repo_identifier": repo_identifier}
        if title:
            payload["title"] = title
        try:
            resp = requests.post(url, headers=_headers(), json=payload, timeout=5)
            if resp.ok:
                data = resp.json()
                return data[0]["id"] if data else None
            logger.warning("ChatMemory.create_session failed (%s): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("ChatMemory.create_session error: %s", e)
        return None

    def list_sessions(self, user_id: str, repo_identifier: str, limit: int = 20) -> List[Dict]:
        """Returns recent sessions (newest first) for this user+repo."""
        if not self._available:
            return []
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {
            "user_id": f"eq.{user_id}",
            "repo_identifier": f"eq.{repo_identifier}",
            "select": "id,title,summary,created_at,updated_at",
            "order": "updated_at.desc",
            "limit": str(limit),
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=5)
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("ChatMemory.list_sessions error: %s", e)
        return []

    def delete_session(self, session_id: str) -> bool:
        """Delete a session and all its messages (cascade). Returns True on success."""
        if not self._available:
            return False
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {"id": f"eq.{session_id}"}
        try:
            headers = dict(_headers())
            headers["Prefer"] = "return=minimal"
            resp = requests.delete(url, headers=headers, params=params, timeout=5)
            return resp.ok
        except Exception as e:
            logger.warning("ChatMemory.delete_session error: %s", e)
        return False

    # ── Message management ─────────────────────────────────────────────────────

    def _get_session_info(self, session_id: str) -> Optional[Dict]:
        """Fetch session metadata including message count and summary."""
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {
            "id": f"eq.{session_id}",
            "select": "id,summary,summary_covers_up_to,title",
            "limit": "1",
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=5)
            if resp.ok:
                data = resp.json()
                return data[0] if data else None
        except Exception as e:
            logger.warning("ChatMemory._get_session_info error: %s", e)
        return None

    def _get_message_count(self, session_id: str) -> int:
        """Count messages in a session."""
        url = f"{_SUPABASE_URL}/rest/v1/chat_messages"
        params = {"session_id": f"eq.{session_id}", "select": "id"}
        headers = dict(_headers())
        headers["Prefer"] = "count=exact"
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=5)
            count_header = resp.headers.get("content-range", "")
            if "/" in count_header:
                return int(count_header.split("/")[1])
            return len(resp.json()) if resp.ok else 0
        except Exception:
            return 0

    def add_message(self, session_id: str, role: str, content: str) -> None:
        """Insert a message and trigger background summarization if threshold exceeded."""
        if not self._available or not session_id:
            return
        threading.Thread(target=self._add_message_bg, args=(session_id, role, content), daemon=True).start()

    def _add_message_bg(self, session_id: str, role: str, content: str) -> None:
        count = self._get_message_count(session_id)
        url = f"{_SUPABASE_URL}/rest/v1/chat_messages"
        headers = dict(_headers())
        headers["Prefer"] = "return=minimal"
        payload = {"session_id": session_id, "idx": count, "role": role, "content": content}
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=5)
            if not resp.ok:
                logger.warning("ChatMemory.add_message failed (%s): %s", resp.status_code, resp.text[:200])
                return
        except Exception as e:
            logger.warning("ChatMemory.add_message error: %s", e)
            return

        # Update session title from first user message
        if count == 0 and role == "user":
            self._set_session_title(session_id, content[:80])

        # Update session updated_at
        self._touch_session(session_id)

        # Trigger summarization if we have too many unsummarized messages
        session = self._get_session_info(session_id)
        if session:
            covers = session.get("summary_covers_up_to", 0)
            unsummarized = (count + 1) - covers
            if unsummarized > SUMMARIZE_THRESHOLD and self._llm:
                self._summarize(session_id, session)

    def _set_session_title(self, session_id: str, title: str) -> None:
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {"id": f"eq.{session_id}"}
        headers = dict(_headers())
        headers["Prefer"] = "return=minimal"
        try:
            requests.patch(url, headers=headers, params=params, json={"title": title}, timeout=5)
        except Exception:
            pass

    def _touch_session(self, session_id: str) -> None:
        url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {"id": f"eq.{session_id}"}
        headers = dict(_headers())
        headers["Prefer"] = "return=minimal"
        try:
            requests.patch(url, headers=headers, params=params, json={"updated_at": "now()"}, timeout=5)
        except Exception:
            pass

    # ── Context retrieval for LLM ──────────────────────────────────────────────

    def get_context_for_llm(self, session_id: str, recent_n: int = RECENT_KEEP) -> List[Dict]:
        """
        Build the history list to pass to the LLM:
          [{"role": "system", "content": "<rolling summary>"}]   (if summary exists)
          + last recent_n messages verbatim

        Returns empty list if session_id is None or Supabase unavailable.
        """
        if not self._available or not session_id:
            return []

        session = self._get_session_info(session_id)
        if not session:
            return []

        # Fetch recent messages
        url = f"{_SUPABASE_URL}/rest/v1/chat_messages"
        params = {
            "session_id": f"eq.{session_id}",
            "select": "role,content",
            "order": "idx.desc",
            "limit": str(recent_n),
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=5)
            if not resp.ok:
                return []
            recent = list(reversed(resp.json()))  # restore chronological order
        except Exception as e:
            logger.warning("ChatMemory.get_context_for_llm error: %s", e)
            return []

        result: List[Dict] = []
        summary = session.get("summary", "")
        if summary:
            result.append({
                "role": "system",
                "content": f"[Previous conversation summary]\n{summary}",
            })
        result.extend({"role": m["role"], "content": m["content"]} for m in recent)
        return result

    def load_messages_for_display(self, session_id: str, limit: int = 100) -> List[Dict]:
        """Load messages for frontend display (newest first, then reversed)."""
        if not self._available or not session_id:
            return []
        url = f"{_SUPABASE_URL}/rest/v1/chat_messages"
        params = {
            "session_id": f"eq.{session_id}",
            "select": "role,content,idx",
            "order": "idx.asc",
            "limit": str(limit),
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=5)
            if resp.ok:
                return resp.json()
        except Exception as e:
            logger.warning("ChatMemory.load_messages_for_display error: %s", e)
        return []

    # ── Summarization ──────────────────────────────────────────────────────────

    def _summarize(self, session_id: str, session: Dict) -> None:
        """
        Summarize older messages (all except the last RECENT_KEEP).
        Updates session.summary and session.summary_covers_up_to.
        Runs synchronously — should only be called from a background thread.
        """
        total = self._get_message_count(session_id)
        summarize_up_to = total - RECENT_KEEP  # keep last RECENT_KEEP verbatim
        if summarize_up_to <= 0:
            return

        already_covered = session.get("summary_covers_up_to", 0)
        if summarize_up_to <= already_covered:
            return  # nothing new to summarize

        # Fetch messages from already_covered to summarize_up_to (exclusive)
        url = f"{_SUPABASE_URL}/rest/v1/chat_messages"
        params = {
            "session_id": f"eq.{session_id}",
            "idx": f"gte.{already_covered}",
            "idx": f"lt.{summarize_up_to}",
            "select": "role,content",
            "order": "idx.asc",
        }
        # Note: can't use two eq. filters for same key in simple params dict — use range filter
        params = {
            "session_id": f"eq.{session_id}",
            "select": "role,content",
            "order": "idx.asc",
            "idx": f"gte.{already_covered}&idx=lt.{summarize_up_to}",
        }
        # Build proper filter URL
        filter_url = (
            f"{url}?session_id=eq.{session_id}"
            f"&idx=gte.{already_covered}&idx=lt.{summarize_up_to}"
            f"&select=role,content&order=idx.asc"
        )
        try:
            resp = requests.get(filter_url, headers=_headers(), timeout=10)
            if not resp.ok:
                return
            msgs_to_summarize = resp.json()
        except Exception as e:
            logger.warning("ChatMemory._summarize fetch error: %s", e)
            return

        if not msgs_to_summarize:
            return

        from src.prompts import summarize_chat_prompt
        existing_summary = session.get("summary", "")
        prompt = summarize_chat_prompt(existing_summary, msgs_to_summarize)

        try:
            new_summary = self._llm._call(
                messages=[
                    {"role": "system", "content": "You are a coding assistant conversation summarizer. Be concise."},
                    {"role": "user", "content": prompt},
                ],
                model=self._llm.fast_model,
                temperature=0.1,
                max_tokens=600,
            )
        except Exception as e:
            logger.warning("ChatMemory summarization LLM error: %s", e)
            return

        # Update session with new summary
        update_url = f"{_SUPABASE_URL}/rest/v1/chat_sessions"
        params = {"id": f"eq.{session_id}"}
        headers = dict(_headers())
        headers["Prefer"] = "return=minimal"
        try:
            requests.patch(
                update_url, headers=headers, params=params,
                json={"summary": new_summary, "summary_covers_up_to": summarize_up_to},
                timeout=5,
            )
            logger.info("ChatMemory: summarized messages 0–%d for session %s", summarize_up_to, session_id)
        except Exception as e:
            logger.warning("ChatMemory._summarize update error: %s", e)
