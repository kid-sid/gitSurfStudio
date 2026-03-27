"""
RedisSessionMemory: In-session agent execution memory with human feedback support.

Stores agent execution state, step results, human feedback, and changeset
in Redis with a 2-hour TTL. On server restart, users can resume from where
they left off within the same session.

Falls back to in-memory dict if Redis is not configured (for local dev).
"""

import os
import json
import time
from typing import Optional, Dict, Any, List

from src.logger import get_logger

logger = get_logger("redis_session_memory")

_REDIS_URL = os.getenv("REDIS_URL", "").strip()

# Try to import redis if available
try:
    import redis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

# TTL for session data (2 hours)
SESSION_TTL = 7200


class RedisSessionMemory:
    """
    Manages in-session agent execution memory.

    Stores:
    - Execution plan and step status
    - Step results and observations
    - Human feedback (questions + responses)
    - Changeset (file changes)
    - Execution status and timestamps
    """

    def __init__(self):
        self._use_redis = _REDIS_AVAILABLE and bool(_REDIS_URL)
        self._redis_client: Optional[redis.Redis] = None
        self._memory: Dict[str, Dict] = {}  # Fallback in-memory storage

        if self._use_redis:
            try:
                self._redis_client = redis.from_url(_REDIS_URL, decode_responses=True)
                self._redis_client.ping()
                logger.info("RedisSessionMemory: Connected to Redis")
            except Exception as e:
                logger.warning("RedisSessionMemory: Failed to connect to Redis (%s), falling back to in-memory", e)
                self._use_redis = False
        else:
            logger.debug("RedisSessionMemory: REDIS_URL not configured, using in-memory storage")

    def _get_key(self, session_id: str, task_id: str) -> str:
        """Generate Redis key for agent task execution."""
        return f"agent_execution:{session_id}:{task_id}"

    # ── Task Lifecycle ─────────────────────────────────────────────────────

    def start_task(
        self,
        session_id: str,
        task_id: str,
        plan_dict: Dict,
        user_query: str,
    ) -> None:
        """Initialize execution tracking for a new agent task."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        execution_state = {
            "task_id": task_id,
            "session_id": session_id,
            "plan": plan_dict,
            "user_query": user_query,
            "status": "running",
            "execution_log": [],
            "human_feedback": [],
            "changeset": None,
            "final_answer": None,
            "error_context": None,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
        self._set(key, execution_state)

    def log_step_complete(
        self,
        session_id: str,
        task_id: str,
        step_id: int,
        status: str,  # done | failed | skipped
        observation: str = "",
        error: str = "",
    ) -> None:
        """Log completion of a step."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        state = self._get(key)
        if not state:
            return

        # Add or update step in execution log
        step_log = {
            "step_id": step_id,
            "status": status,
            "observation": observation,
            "error": error,
            "timestamp": time.time(),
        }

        # Replace if step already exists, otherwise append
        existing = [s for s in state.get("execution_log", []) if s["step_id"] != step_id]
        state["execution_log"] = existing + [step_log]
        state["updated_at"] = time.time()

        self._set(key, state)

    def add_human_feedback(
        self,
        session_id: str,
        task_id: str,
        step_id: int,
        question: str,
        response: str,
    ) -> None:
        """Store human feedback (question + response) for a step."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        state = self._get(key)
        if not state:
            return

        feedback = {
            "step_id": step_id,
            "question": question,
            "response": response,
            "timestamp": time.time(),
        }

        state["human_feedback"] = state.get("human_feedback", []) + [feedback]
        state["updated_at"] = time.time()

        self._set(key, state)

    def update_changeset(
        self,
        session_id: str,
        task_id: str,
        changeset_dict: Dict,
    ) -> None:
        """Update the changeset (file changes)."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        state = self._get(key)
        if not state:
            return

        state["changeset"] = changeset_dict
        state["updated_at"] = time.time()

        self._set(key, state)

    def update_plan(
        self,
        session_id: str,
        task_id: str,
        plan_dict: Dict,
    ) -> None:
        """Update the agent plan (e.g. after re-planning)."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        state = self._get(key)
        if not state:
            return

        state["plan"] = plan_dict
        state["updated_at"] = time.time()

        self._set(key, state)

    def finalize_execution(
        self,
        session_id: str,
        task_id: str,
        final_answer: str,
        status: str,  # completed | failed | cancelled | partial
        error_context: Optional[str] = None,
    ) -> None:
        """Mark execution as complete."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        state = self._get(key)
        if not state:
            return

        state["status"] = status
        state["final_answer"] = final_answer
        state["error_context"] = error_context
        state["updated_at"] = time.time()

        self._set(key, state)

    # ── Recovery ──────────────────────────────────────────────────────────

    def get_execution_state(
        self, session_id: str, task_id: str
    ) -> Optional[Dict]:
        """Retrieve execution state for resume or display."""
        if not session_id or not task_id:
            return None

        key = self._get_key(session_id, task_id)
        return self._get(key)

    def get_incomplete_task(self, session_id: str) -> Optional[Dict]:
        """
        Check if there's an incomplete ('running') task for this session.
        Returns the first incomplete task found.
        """
        if not session_id:
            return None

        if self._use_redis:
            # Search for keys matching agent_execution:{session_id}:*
            pattern = f"agent_execution:{session_id}:*"
            try:
                keys = self._redis_client.keys(pattern)
                for key in keys:
                    state = self._get(key)
                    if state and state.get("status") == "running":
                        return state
            except Exception as e:
                logger.warning("Error searching for incomplete tasks: %s", e)
        else:
            # Search in-memory
            for key, state in self._memory.items():
                if (
                    key.startswith(f"agent_execution:{session_id}:")
                    and state.get("status") == "running"
                ):
                    return state

        return None

    def clear_task(self, session_id: str, task_id: str) -> None:
        """Delete execution state (after accept/rollback)."""
        if not session_id or not task_id:
            return

        key = self._get_key(session_id, task_id)
        self._delete(key)

    # ── Low-level Storage ──────────────────────────────────────────────────

    def _set(self, key: str, value: Dict) -> None:
        """Store value in Redis or in-memory."""
        try:
            if self._use_redis:
                self._redis_client.setex(
                    key,
                    SESSION_TTL,
                    json.dumps(value),
                )
            else:
                self._memory[key] = value
        except Exception as e:
            logger.warning("RedisSessionMemory._set error: %s", e)

    def _get(self, key: str) -> Optional[Dict]:
        """Retrieve value from Redis or in-memory."""
        try:
            if self._use_redis:
                data = self._redis_client.get(key)
                return json.loads(data) if data else None
            else:
                return self._memory.get(key)
        except Exception as e:
            logger.warning("RedisSessionMemory._get error: %s", e)
            return None

    def _delete(self, key: str) -> None:
        """Delete value from Redis or in-memory."""
        try:
            if self._use_redis:
                self._redis_client.delete(key)
            else:
                self._memory.pop(key, None)
        except Exception as e:
            logger.warning("RedisSessionMemory._delete error: %s", e)
