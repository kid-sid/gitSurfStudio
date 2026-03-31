"""
Route modules for the GitSurf Studio engine.

Shared dependencies used across route files are exported here.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

from src.engine_state import _safe_path as _safe_path  # noqa: F401
from src.engine_state import state
from src.history_manager import HistoryManager
from src.llm_client import LLMClient
from src.logger import get_logger
from src.memory.chat_memory import ChatMemory
from src.orchestrator import PipelineContext
from src.tool_registry import (
    AVAILABLE_TOOLS as AVAILABLE_TOOLS,  # noqa: F401
)
from src.tool_registry import (
    register_tools,
    start_mcp_background,
)

logger = get_logger("server")
limiter = Limiter(key_func=get_remote_address)


def _ensure_initialized(workspace_path: str):
    """Lazily initialize the engine for a given workspace (double-checked locking)."""
    # Fast path — already initialized for this workspace (no lock needed)
    if state.workspace_path == workspace_path and state.llm is not None:
        return

    # Slow path — acquire lock and re-check (double-checked locking pattern)
    with state._lock:
        if state.workspace_path == workspace_path and state.llm is not None:
            return

        state.workspace_path = workspace_path
        try:
            state.llm = LLMClient()
        except Exception as e:
            logger.error("Failed to initialize LLMClient: %s", e)
            state.llm = None
            return

        # Wire ChatMemory with the LLM for summarization
        if state.chat_memory is None:
            state.chat_memory = ChatMemory(llm_client=state.llm)

        state.history = HistoryManager(
            history_file=os.path.join(workspace_path, ".gitsurf_history.json")
        )

        # ── Wire project context ──────────────────────────────────────
        readme_candidates = ["README.md", "readme.md", "README.rst", "README.txt"]
        state.project_context = ""
        for candidate in readme_candidates:
            readme_path = os.path.join(workspace_path, candidate)
            if os.path.exists(readme_path):
                try:
                    with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                        readme_content = f.read()
                    state.project_context = state.llm.analyze_project_context(readme_content)
                    logger.info("Project context ready (%d chars)", len(state.project_context))
                except Exception as e:
                    logger.warning("Could not analyze README: %s", e)
                break
        # ─────────────────────────────────────────────────────────────

        state.pipeline_ctx = PipelineContext(
            search_path=workspace_path,
            rebuild_index=False,
        )

        # Register all agent tools
        register_tools(state, workspace_path, state.pipeline_ctx)

        # Start MCP tool discovery in background
        _engine_dir = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        _mcp_config = os.path.join(_engine_dir, "..", "mcp_config.json")
        start_mcp_background(state, _mcp_config)
