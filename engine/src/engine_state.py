"""
Global engine state singleton for GitSurf Studio.
"""

import os
import threading
from typing import Optional, Dict, Any

from fastapi import HTTPException

from src.llm_client import LLMClient
from src.history_manager import HistoryManager
from src.memory.supabase_memory import SupabaseMemory
from src.memory.chat_memory import ChatMemory
from src.tools.repo_manager import RepoManager
from src.tools.git_tool import GitTool
from src.tools.symbol_extractor import SymbolExtractor
from src.tools.symbol_peeker import SymbolPeeker
from src.tools.terminal_tool import TerminalTool
from src.security import PromptGuard, TopicGuard
from src.mcp.client_manager import MCPClientManager

_ENGINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class EngineState:
    def __init__(self):
        self._lock = threading.Lock()  # guards _ensure_initialized
        self.llm: Optional[LLMClient] = None
        self.history: Optional[HistoryManager] = None
        self.pipeline_ctx = None  # PipelineContext (set during init to avoid circular import)
        self.agent_tools: Dict = {}
        self.workspace_path: Optional[str] = None
        self.project_context: str = ""
        self.git_tool: Optional[GitTool] = None
        self.github_token: Optional[str] = None
        self.repo_manager = RepoManager(
            cache_dir=os.path.join(_ENGINE_DIR, ".cache")
        )
        self.symbol_extractor = SymbolExtractor(
            cache_dir=os.path.join(_ENGINE_DIR, ".cache", "symbols")
        )
        self._symbols_cache: Dict[str, tuple] = {}  # path -> (symbols_dict, mtime)
        self.prompt_guard = PromptGuard()
        self.topic_guard: Optional[TopicGuard] = None  # wired after LLM init
        self.supabase_memory = SupabaseMemory()
        self._pending_memory_save: Optional[Dict] = None  # set by /init when reindex needed
        self.chat_memory: Optional[ChatMemory] = None     # wired after LLM init
        self.mcp_manager: Optional[MCPClientManager] = None
        self.mcp_ready: bool = False
        self.available_tools: str = ""  # populated after AVAILABLE_TOOLS is defined
        self.terminal_tool: Optional[TerminalTool] = None
        self.active_changesets: Dict[str, Any] = {}  # changeset_id -> changeset dict
        self.active_executor = None  # current AgentExecutor (for cancel/respond)

    def get_active_token(self) -> Optional[str]:
        return self.github_token or os.getenv("GITHUB_TOKEN")


state = EngineState()


def _safe_path(workspace: str, user_path: str) -> str:
    """
    Resolve user_path and assert it lives inside workspace.
    Uses realpath() to follow symlinks before comparing, preventing
    symlink-based escapes and classic ../../../ traversal attacks.
    Raises HTTP 403 if the resolved path is outside the workspace.
    """
    workspace_real = os.path.realpath(workspace) + os.sep
    # If user_path is relative, resolve it against the workspace (not CWD)
    if not os.path.isabs(user_path):
        user_path = os.path.join(workspace, user_path)
    resolved = os.path.realpath(user_path)
    # Append sep before comparing so the workspace root itself passes the check
    if not (resolved + os.sep).startswith(workspace_real):
        raise HTTPException(status_code=403, detail="Path outside of workspace")
    return resolved


class SymbolPeekerTool:
    """
    Lazy wrapper around SymbolPeeker.
    Builds the symbol index on first call (uses cache when available).
    """

    def __init__(self, pipeline_ctx, workspace_path: str):
        self._ctx = pipeline_ctx
        self._workspace = workspace_path
        self._peeker: Optional[SymbolPeeker] = None

    def _ensure_peeker(self):
        if self._peeker is not None:
            return
        index = self._ctx.sym_extractor.extract_from_directory(self._workspace)
        self._peeker = SymbolPeeker(index, self._workspace)

    def peek_symbol(self, symbol_name: str) -> list:
        """
        Returns all definitions of symbol_name found in the workspace.
        Each entry: {file, type, name, start_line, end_line, content}
        """
        self._ensure_peeker()
        return self._peeker.peek_symbol(symbol_name)
