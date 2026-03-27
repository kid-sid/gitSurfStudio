"""
GitSurf Studio — AI Engine Server

A FastAPI server that wraps the GitSurf PRAR pipeline, providing:
  - POST /chat       — Streaming agent responses (SSE)
  - POST /index      — Trigger re-indexing of the workspace
  - POST /autocomplete — Fast inline code completions
  - GET  /health     — Health check
"""

import os
import re
import sys
import json
import asyncio
import threading
import requests
import subprocess
import platform

# ── Windows Subprocess Support ──────────────────────────────────────────
# On Windows, asyncio.create_subprocess_exec/shell requires the Proactor loop.
if platform.system() == "Windows":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass # Fallback for old/stripped environments
# ───────────────────────────────────────────────────────────────────────
from typing import Optional, List, Dict, Any, Callable, cast
from contextlib import redirect_stdout
import queue
from dotenv import load_dotenv

# Try to load .env from current directory, then from parent (project root)
if not load_dotenv():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from src.logger import get_logger

logger = get_logger("server")

# Ensure engine modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.llm_client import LLMClient
from src.history_manager import HistoryManager
from src.memory.supabase_memory import SupabaseMemory
from src.memory.chat_memory import ChatMemory
from src.memory.redis_session_memory import RedisSessionMemory
from src.tools.file_editor_tool import FileEditorTool
from src.tools.search_tool import SearchTool
from src.tools.web_tool import WebSearchTool
from src.tools.repo_manager import RepoManager
from src.tools.git_tool import GitTool
from src.tools.editor_ui_tool import EditorUITool
from src.tools.symbol_extractor import SymbolExtractor
from src.tools.symbol_peeker import SymbolPeeker
from src.orchestrator import run_code_aware_pipeline, run_local_pipeline, run_agent_pipeline, PipelineContext
from src.security import PromptGuard, TopicGuard
from src.security.supabase_logger import log_security_event
from src.mcp.client_manager import MCPClientManager
from src.mcp.tool_proxy import MCPToolProxy
from src.tools.browser_tool import BrowserTool
from src.tools.terminal_tool import TerminalTool

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="GitSurf Studio Engine",
    description="AI-powered codebase reasoning engine",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tauri app runs on localhost
    allow_methods=["*"],
    allow_headers=["*"],
)

class EngineState:
    def __init__(self):
        self._lock = threading.Lock()  # guards _ensure_initialized
        self.llm: Optional[LLMClient] = None
        self.history: Optional[HistoryManager] = None
        self.pipeline_ctx: Optional[PipelineContext] = None
        self.agent_tools: Dict = {}
        self.workspace_path: Optional[str] = None
        self.project_context: str = ""
        self.git_tool: Optional[GitTool] = None
        self.github_token: Optional[str] = None
        self.repo_manager = RepoManager(
            cache_dir=os.path.join(os.path.dirname(__file__), ".cache")
        )
        self.symbol_extractor = SymbolExtractor(
            cache_dir=os.path.join(os.path.dirname(__file__), ".cache", "symbols")
        )
        self._symbols_cache: Dict[str, tuple] = {}  # path -> (symbols_dict, mtime)
        self.prompt_guard = PromptGuard()
        self.topic_guard: Optional[TopicGuard] = None  # wired after LLM init
        self.supabase_memory = SupabaseMemory()
        self._pending_memory_save: Optional[Dict] = None  # set by /init when reindex needed
        self.chat_memory: Optional[ChatMemory] = None     # wired after LLM init
        self.session_memory: RedisSessionMemory = RedisSessionMemory()  # in-session agent recovery
        self.mcp_manager: Optional[MCPClientManager] = None
        self.mcp_ready: bool = False
        self.available_tools: str = ""  # populated after AVAILABLE_TOOLS is defined below
        self.terminal_tool: Optional[TerminalTool] = None
        self.active_changesets: Dict[str, Any] = {}  # changeset_id -> changeset dict
        self.active_executor = None  # current AgentExecutor (for cancel/respond)
        self.active_session_id: Optional[str] = None  # session_id for current agent task
        self.active_task_id: Optional[str] = None  # task_id for current agent task

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
    # (e.g. resolved == workspace without trailing sep would otherwise be rejected)
    if not (resolved + os.sep).startswith(workspace_real):
        raise HTTPException(status_code=403, detail="Path outside of workspace")
    return resolved


class ChatRequest(BaseModel):
    query: str
    path: str
    history: Optional[List[Dict[str, str]]] = []
    user_id: Optional[str] = None   # Supabase auth user ID, passed by frontend
    agent_mode: bool = False        # True = Plan→Execute→Verify pipeline

    @field_validator("query")
    @classmethod
    def query_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("query cannot be empty")
        if len(v) > 20_000:
            raise ValueError("query exceeds maximum length of 20,000 characters")
        return v

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class IndexRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class InitRequest(BaseModel):
    input: str
    user_id: Optional[str] = None   # Supabase auth user ID (for persistent memory)

    @field_validator("input")
    @classmethod
    def input_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("input cannot be empty")
        return v


class AutocompleteRequest(BaseModel):
    code_context: str
    file_path: str
    path: str

    @field_validator("code_context")
    @classmethod
    def context_max_length(cls, v: str) -> str:
        if len(v) > 100_000:
            raise ValueError("code_context exceeds maximum length of 100,000 characters")
        return v


class WriteRequest(BaseModel):
    path: str
    content: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v

    @field_validator("content")
    @classmethod
    def content_max_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 10 * 1024 * 1024:  # 10 MB
            raise ValueError("content exceeds maximum size of 10 MB")
        return v

class RestoreRequest(BaseModel):
    path: str

class CompleteRequest(BaseModel):
    path: str
    prefix: str
    suffix: str
    language: str = "plaintext"

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class GitStatusRequest(BaseModel):
    path: str

class GitStageRequest(BaseModel):
    path: str
    files: List[str]

    @field_validator("files")
    @classmethod
    def files_not_empty(cls, v: List[str]) -> List[str]:
        if not v:
            raise ValueError("files list cannot be empty")
        return v


class GitCommitRequest(BaseModel):
    path: str
    message: str

    @field_validator("message")
    @classmethod
    def message_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("commit message cannot be empty")
        return v


class GitForkRequest(BaseModel):
    path: str
    repo_name: str


# ── Response Models (for OpenAPI docs) ──────────────────────────────

class HealthResponse(BaseModel):
    status: str
    workspace: Optional[str]

class InitResponse(BaseModel):
    status: str
    workspace_path: str
    has_project_context: bool
    is_github: bool

class ReadResponse(BaseModel):
    content: str

class WriteResponse(BaseModel):
    status: str
    message: str

class GitStatusResponse(BaseModel):
    status: List[Dict[str, str]]

class GitMessageResponse(BaseModel):
    message: str

class BranchResponse(BaseModel):
    current: Optional[str]
    branches: List[str]

class SymbolResponse(BaseModel):
    path: str
    symbols: List[Dict]

class AutocompleteResponse(BaseModel):
    completion: str

# ────────────────────────────────────────────────────────────────────

class SymbolPeekerTool:
    """
    Lazy wrapper around SymbolPeeker.
    Builds the symbol index on first call (uses cache when available).
    """

    def __init__(self, pipeline_ctx: "PipelineContext", workspace_path: str):
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


AVAILABLE_TOOLS = """
Tool: FileEditorTool
Description: Read, write, modify, or delete files inside the project directory.
Methods:
  - read_file(path, start_line=None, end_line=None)
  - write_file(path, content): Creates a NEW file. Fails if the file exists. Use replace_in_file for existing files.
  - replace_in_file(path, target, replacement, allow_multiple=False)
    NOTE: target must match exactly once unless allow_multiple=True.
    On ambiguity, returns the line numbers of all matches to help refine the target.
  - delete_file(path)

Tool: EditorUITool
Description: Control the IDE user interface.
Methods:
  - open_file(path)
    Opens the specified file in an editor tab.
 
Tool: GitTool
Description: Handle local Git operations (status, stage, commit, diff).
Methods:
  - get_status()
  - stage_files(files)
  - commit(message)
  - get_diff(file_path=None)

Tool: SearchTool
Description: Search for text patterns in the codebase using ripgrep.
Methods:
  - search(query, search_path=".")
  - search_and_chunk(query, search_path=".", context_lines=10)

Tool: WebSearchTool
Description: Search the web or fetch URL content for documentation/errors.
Methods:
  - search(query, num_results=5)
  - fetch_url(url)

Tool: SymbolPeekerTool
Description: Peek the definition of any function or class by name (like F12 in VS Code). Returns the full source block, file path, and line range. Use this to inspect a symbol's current implementation before editing it — avoids reading entire files.
Methods:
  - peek_symbol(symbol_name): Returns [{file, type, name, start_line, end_line, content}]

Tool: BrowserTool
Description: High-level browser automation for verifying pages, testing interactions, and debugging client-side issues. Uses Playwright under the hood. Prefer this over raw mcp__playwright__* calls for multi-step browser workflows.
Methods:
  - verify_page(url, checks=None, wait_ms=2000)
    Navigate to URL, capture snapshot + screenshot, optionally check for expected text.
    checks: JSON array of strings, e.g. '["Submit button", "Welcome"]'
  - test_interaction(url, steps)
    Execute a sequence of browser actions and report pass/fail per step.
    steps: JSON array, e.g. '[{"action":"click","element":"Login"},{"action":"snapshot","expect":"Dashboard"}]'
  - debug_page(url)
    Capture snapshot, screenshot, and console messages for debugging.
  - scrape_rendered(url)
    Fetch content from a JavaScript-rendered page (use instead of WebSearchTool.fetch_url for SPAs).

Tool: TerminalTool
Description: Execute shell commands safely in the workspace (tests, linting, builds).
Methods:
  - run_command(command, timeout_sec=30, cwd=None)
    Run a shell command. Only safe commands are allowed (pytest, ruff, npm, node, etc.).
  - run_test(test_path=None)
    Run project tests (auto-detects pytest or npm test).
  - run_lint(file_path=None)
    Run linter (ruff for Python, eslint for JS/TS/Svelte).
"""

# Seed the default — _init_mcp_background will append MCP tools once servers are ready
state.available_tools = AVAILABLE_TOOLS


def _build_mcp_schema_hint(input_schema: dict) -> str:
    """
    Build a rich, human-readable parameter hint for an MCP tool.
    Marks required params with *, optional with ?, includes descriptions and enum values.
    Example: url*: string "The URL to navigate to", wait_until?: enum[load|domcontentloaded]
    """
    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    if not props:
        return ""
    parts = []
    for k, v in props.items():
        req_marker = "*" if k in required else "?"
        typ = v.get("type", "any")
        if "enum" in v:
            typ = "enum[" + "|".join(str(e) for e in v["enum"]) + "]"
        elif typ == "object" and "properties" in v:
            nested = ", ".join(
                f"{nk}: {nv.get('type', 'any')}" for nk, nv in v["properties"].items()
            )
            typ = f"object{{{nested}}}"
        hint = f"{k}{req_marker}: {typ}"
        desc = v.get("description", "")
        if desc:
            hint += f' "{desc[:60]}"'
        parts.append(hint)
    return ", ".join(parts)


def _init_mcp_background(config_path: str) -> None:
    """
    Runs in a daemon thread. Connects to all MCP servers in parallel, then
    registers proxies into state.agent_tools and updates state.available_tools.
    The /init endpoint returns immediately; this runs concurrently.
    """
    try:
        manager = MCPClientManager()
        manager.initialize(config_path)

        mcp_tools_desc = ""
        for tool_info in manager.list_all_tools():
            full_key = f"mcp__{tool_info['server']}__{tool_info['name']}"
            # Bug 1A: also register a shorthand alias (hyphens → underscores)
            shorthand = tool_info["name"].replace("-", "_")
            proxy = MCPToolProxy(
                manager=manager,
                server_name=tool_info["server"],
                tool_name=tool_info["name"],
                description=tool_info["description"],
                input_schema=tool_info.get("input_schema", {}),
            )
            # Bug 2: rich schema hint (* = required, ? = optional)
            schema_hint = _build_mcp_schema_hint(tool_info.get("input_schema", {}))
            with state._lock:
                state.agent_tools[full_key] = proxy
                state.agent_tools[shorthand] = proxy   # alias

            mcp_tools_desc += (
                f"\nTool: {full_key}  (alias: {shorthand})\n"
                f"Description: {tool_info['description']}\n"
                f"Methods:\n"
                f"  - execute({schema_hint})\n"
                f"  NOTE: method must always be \"execute\"\n"
            )

        with state._lock:
            state.mcp_manager = manager
            if mcp_tools_desc:
                state.available_tools = (
                    AVAILABLE_TOOLS
                    + "\n\n## MCP Tools (* = required, ? = optional)\n"
                    + mcp_tools_desc
                )
            state.mcp_ready = True

        tool_count = len(manager.list_all_tools())
        logger.info("[MCP] Ready — %d tool(s) registered.", tool_count)
    except Exception as exc:
        logger.error("[MCP] Background init failed: %s", exc)
        with state._lock:
            state.mcp_ready = True   # mark ready anyway so status endpoint doesn't hang


def _ensure_initialized(workspace_path: str):
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

        file_editor = FileEditorTool(root_path=workspace_path)
        git_tool = GitTool(root_path=workspace_path)
        editor_ui = EditorUITool(root_path=workspace_path)
        searcher = SearchTool()
        web_tool = WebSearchTool()

        state.pipeline_ctx = PipelineContext(
            search_path=workspace_path,
            rebuild_index=False,
        )

        terminal_tool = TerminalTool(workspace_path=workspace_path)
        state.terminal_tool = terminal_tool

        state.agent_tools = {
            "FileEditorTool": file_editor,
            "GitTool": git_tool,
            "EditorUITool": editor_ui,
            "SearchTool": searcher,
            "WebSearchTool": web_tool,
            "SymbolPeekerTool": SymbolPeekerTool(state.pipeline_ctx, workspace_path),
            "BrowserTool": BrowserTool(tools_getter=lambda: state.agent_tools),
            "TerminalTool": terminal_tool,
        }
        state.git_tool = git_tool

        # ── MCP tool integration (background — non-blocking) ─────────────────
        state.mcp_ready = False
        _mcp_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mcp_config.json")
        mcp_thread = threading.Thread(
            target=_init_mcp_background,
            args=(_mcp_config,),
            daemon=True,
            name="mcp-init",
        )
        mcp_thread.start()
        # ─────────────────────────────────────────────────────────────────────


@app.on_event("shutdown")
async def _shutdown_mcp():
    if state.mcp_manager:
        state.mcp_manager.shutdown()


@app.get("/mcp/status")
async def mcp_status():
    """Returns MCP readiness and list of available tool names. Poll after /init."""
    tools = []
    if state.mcp_manager:
        tools = [t["name"] for t in state.mcp_manager.list_all_tools()]
    return {"ready": state.mcp_ready, "tools": tools, "count": len(tools)}


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "workspace": state.workspace_path}


@app.post("/init", response_model=InitResponse)
@limiter.limit("10/minute")
async def init_workspace(request: Request, req: InitRequest):
    target_path = req.input.strip()
    is_github = "github.com" in target_path.lower()
    repo_name: Optional[str] = None

    if is_github:
        repo_name = target_path.split("github.com/")[-1].strip("/")
        if repo_name.endswith(".git"):
            repo_name = str(repo_name)[:-4]
        try:
            target_path = state.repo_manager.sync_repo(repo_name)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to clone: {e}")
    else:
        target_path = os.path.abspath(target_path)
        if not os.path.exists(target_path):
            raise HTTPException(status_code=404, detail="Local path does not exist")

    # ── Persistent memory: inject Supabase symbol cache if available ──────────
    user_id = req.user_id
    if user_id:
        repo_id = SupabaseMemory.make_repo_identifier(
            repo_name if (is_github and repo_name) else target_path, is_github=is_github
        )
        current_sha = SupabaseMemory.get_head_sha(target_path)

        if not state.supabase_memory.needs_reindex(user_id, repo_id, current_sha):
            # Fresh cache in Supabase — inject into local cache files
            _engine_dir = os.path.dirname(os.path.abspath(__file__))
            cache_base = os.path.join(_engine_dir, ".cache")
            injected = state.supabase_memory.load_and_inject_cache(
                user_id, repo_id,
                symbol_cache_dir=os.path.join(cache_base, "symbols"),
                call_graph_cache_dir=os.path.join(cache_base, "call_graph"),
            )
            if injected:
                logger.info("[SupabaseMemory] Loaded symbol cache from Supabase (SHA: %s)", current_sha)
        else:
            # Schedule save after pipeline builds the index (done lazily by PipelineContext)
            # We store (user_id, repo_id, sha) on state so the background save can fire later
            state._pending_memory_save = {
                "user_id": user_id,
                "repo_id": repo_id,
                "repo_display": repo_name if (is_github and repo_name) else os.path.basename(target_path),
                "commit_sha": current_sha or "unknown",
            }
    else:
        state._pending_memory_save = None
    # ─────────────────────────────────────────────────────────────────────────

    _ensure_initialized(target_path)

    return {
        "status": "success",
        "workspace_path": target_path,
        "has_project_context": bool(state.project_context),
        "is_github": is_github,
    }

@app.get("/symbols", response_model=SymbolResponse)
async def get_symbols(path: str, workspace: Optional[str] = None):
    """
    Extracts symbols (classes, functions) from a file or directory.
    If 'workspace' is provided, 'path' is treated as relative to it.
    """
    effective_workspace = workspace or state.workspace_path
    if effective_workspace:
        target_path = _safe_path(effective_workspace, os.path.join(effective_workspace, path) if workspace else path)
    else:
        target_path = os.path.realpath(path)

    if not os.path.exists(target_path):
        raise HTTPException(status_code=404, detail="Path not found")

    try:
        if os.path.isfile(target_path):
            # Extract from single file
            ext = os.path.splitext(target_path)[1].lower()
            if ext in state.symbol_extractor.PYTHON_EXTENSIONS:
                symbols = state.symbol_extractor._extract_python(target_path)
            elif ext in state.symbol_extractor.JS_EXTENSIONS:
                symbols = state.symbol_extractor._extract_js(target_path)
            elif ext in state.symbol_extractor.C_FAMILY_EXTENSIONS:
                symbols = state.symbol_extractor._extract_c_family(target_path)
            else:
                symbols = []
            return {"path": path, "symbols": symbols}
        else:
            # Extract from directory — use mtime-keyed server cache to avoid
            # re-running the extractor on every request when nothing has changed
            try:
                mtime = os.path.getmtime(target_path)
            except OSError:
                mtime = None

            cached = state._symbols_cache.get(target_path)
            if cached is not None and mtime is not None and cached[1] == mtime:
                raw_symbols = cached[0]
            else:
                raw_symbols = state.symbol_extractor.extract_from_directory(target_path)
                if mtime is not None:
                    state._symbols_cache[target_path] = (raw_symbols, mtime)
            # Flatten dict into a list
            symbols = []
            for file_path, file_symbols in raw_symbols.items():
                for sym in file_symbols:
                    sym_with_file = dict(sym)
                    sym_with_file["file"] = file_path # Keep as relative path from extraction root
                    symbols.append(sym_with_file)
            return {"path": path, "symbols": symbols}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/peek-symbol")
async def peek_symbol_endpoint(name: str):
    """
    Returns the source block(s) for a symbol name — the F12 / Peek Definition backend.
    Each result: {file, type, name, start_line, end_line, content}
    """
    if not state.workspace_path:
        raise HTTPException(status_code=400, detail="No workspace initialized")
    tool = state.agent_tools.get("SymbolPeekerTool")
    if not tool:
        raise HTTPException(status_code=503, detail="SymbolPeekerTool not available")
    try:
        results = tool.peek_symbol(name)
        return {"symbol": name, "results": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/git/status", response_model=GitStatusResponse)
async def git_status(path: str):
    git = state.git_tool
    if not git:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    status = git.get_status()
    return {"status": status}

@app.post("/git/stage", response_model=GitMessageResponse)
async def git_stage(req: GitStageRequest):
    git = state.git_tool
    if not git:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    result = git.stage_files(req.files)
    return {"message": result}

@app.post("/git/commit", response_model=GitMessageResponse)
async def git_commit(req: GitCommitRequest):
    git = state.git_tool
    if not git:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    result = git.commit(req.message)
    return {"message": result}

@app.get("/git/branch", response_model=BranchResponse)
async def git_branch(path: str):
    _ensure_initialized(path)
    git_tool = state.git_tool
    if not git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    return git_tool.get_branches()

class GitCheckoutRequest(BaseModel):
    path: str
    branch: str

@app.post("/git/checkout")
async def git_checkout(req: GitCheckoutRequest):
    _ensure_initialized(req.path)
    git_tool = state.git_tool
    if not git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    try:
        result = git_tool.checkout_branch(req.branch)
        return {"message": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class GitStashRequest(BaseModel):
    path: str

@app.post("/git/stash")
async def git_stash(req: GitStashRequest):
    _ensure_initialized(req.path)
    git_tool = state.git_tool
    if not git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    try:
        result = git_tool.stash_changes()
        return {"message": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

class GitDiscardRequest(BaseModel):
    path: str
    file: str

@app.post("/git/discard")
async def git_discard(req: GitDiscardRequest):
    _ensure_initialized(req.path)
    git_tool = state.git_tool
    if not git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    try:
        result = git_tool.discard_changes(req.file)
        return {"message": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/git/fork")
async def git_fork(req: GitForkRequest):
    _ensure_initialized(req.path)
    github_token = state.get_active_token()
    if not github_token:
        raise HTTPException(status_code=401, detail="Please login with GitHub first")
    
    try:
        fork_url = state.repo_manager.fork_repo(req.repo_name, github_token)
        return {"status": "success", "fork_url": fork_url}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ── Auth Endpoints ──────────────────────────────────────────────────

@app.get("/auth/status")
async def auth_status():
    return {"authenticated": state.github_token is not None}

@app.get("/auth/login")
async def auth_login():
    client_id = os.getenv("GITHUB_CLIENT_ID")
    if not client_id:
        raise HTTPException(status_code=500, detail="GITHUB_CLIENT_ID not set")
    
    scope = "repo,user"
    github_url = f"https://github.com/login/oauth/authorize?client_id={client_id}&scope={scope}"
    return RedirectResponse(github_url)

@app.get("/auth/callback")
async def auth_callback(code: str):
    client_id = os.getenv("GITHUB_CLIENT_ID")
    client_secret = os.getenv("GITHUB_CLIENT_SECRET")
    
    if not client_id or not client_secret:
        raise HTTPException(status_code=500, detail="OAuth credentials not set")

    # Exchange code for token
    token_url = "https://github.com/login/oauth/access_token"
    headers = {"Accept": "application/json"}
    params = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
    }
    
    response = requests.post(token_url, json=params, headers=headers)
    token_data = response.json()
    
    if "access_token" in token_data:
        state.github_token = token_data["access_token"]
        return HTMLResponse("""
            <html>
                <body style="font-family: sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; background: #0d1117; color: white;">
                    <div style="text-align: center;">
                        <h1>🌊 GitSurf Studio</h1>
                        <p style="color: #63ff63;">✓ Authenticated successfully!</p>
                        <p>You can close this tab and return to the IDE.</p>
                        <script>setTimeout(() => window.close(), 3000);</script>
                    </div>
                </body>
            </html>
        """)
    else:
        error_msg = token_data.get("error_description", "Failed to exchange token")
        raise HTTPException(status_code=400, detail=error_msg)


def _parse_diff_hunks(diff_str: str) -> dict:
    """Parse unified diff output into added/modified line numbers (1-indexed, new-file side)."""
    added: list[int] = []
    modified: list[int] = []
    new_line = 0
    pending_deletes = 0

    for line in diff_str.splitlines():
        if line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            if m:
                new_line = int(m.group(1)) - 1
            pending_deletes = 0
        elif line.startswith("---") or line.startswith("+++"):
            continue
        elif line.startswith("-"):
            pending_deletes += 1
        elif line.startswith("+"):
            new_line += 1
            if pending_deletes > 0:
                modified.append(new_line)
                pending_deletes -= 1
            else:
                added.append(new_line)
        else:  # context line
            new_line += 1
            pending_deletes = 0

    return {"added": added, "modified": modified}


@app.get("/git/diff-lines")
async def git_diff_lines(path: str):
    """Returns added/modified line numbers vs HEAD for a file, for gutter decorations."""
    if not state.workspace_path:
        return {"added": [], "modified": []}
    abs_path = _safe_path(state.workspace_path, path)
    rel_path = os.path.relpath(abs_path, state.workspace_path)

    try:
        result = subprocess.run(
            ["git", "diff", "HEAD", "--", rel_path],
            capture_output=True, text=True, cwd=state.workspace_path,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout:
            return {"added": [], "modified": []}
        return _parse_diff_hunks(result.stdout)
    except Exception:
        return {"added": [], "modified": []}


@app.get("/files")
async def get_files(path: str):
    """Returns a JSON tree of the workspace files."""
    # Validate the requested root is within the active workspace
    if state.workspace_path:
        path = _safe_path(state.workspace_path, path)
    else:
        path = os.path.realpath(path)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")

    def build_tree(current_path):
        current_path_str = str(current_path)
        name = os.path.basename(current_path_str)
        if not name and current_path_str: # Handle root path
             name = current_path_str
             
        if os.path.isfile(current_path_str):
            return {"name": name, "type": "file", "path": current_path_str}
        
        children = []
        try:
            for item in sorted(os.listdir(current_path_str)):
                if item in {".git", "__pycache__", "node_modules", ".cache", "venv", ".venv", ".claude"}:
                    continue
                children.append(build_tree(os.path.join(current_path_str, item)))
        except PermissionError:
            pass
            
        return {"name": name, "type": "dir", "path": current_path, "children": children}

    return build_tree(path)


@app.get("/read", response_model=ReadResponse)
async def read_file(path: str):
    """Reads a file's content from the local disk."""
    if state.workspace_path:
        path = _safe_path(state.workspace_path, path)
    else:
        path = os.path.realpath(path)

    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return {"content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/write", response_model=WriteResponse)
async def write_file(req: WriteRequest):
    """Writes content to a file on local disk."""
    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)
    
    try:
        # Ensure parent directories exist
        parent_dir = os.path.dirname(abs_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # Write directly — do NOT use FileEditorTool here (that creates .bak files
        # and emits AI UI commands, which are only appropriate for AI-driven edits)
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "success", "message": f"Wrote to {abs_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class MkdirRequest(BaseModel):
    path: str

    @field_validator("path")
    @classmethod
    def path_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("path cannot be empty")
        return v


class RenameRequest(BaseModel):
    old_path: str
    new_path: str


@app.post("/mkdir")
async def mkdir(req: MkdirRequest):
    """Creates a directory on local disk."""
    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)

    try:
        os.makedirs(abs_path, exist_ok=True)
        return {"status": "success", "message": f"Created directory {abs_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/restore")
async def restore_file(req: RestoreRequest):
    """Restores a file from its .bak backup created by FileEditorTool."""
    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)

    bak_path = abs_path + ".bak"
    if not os.path.exists(bak_path):
        raise HTTPException(status_code=404, detail="No backup found for this file")

    try:
        with open(bak_path, "r", encoding="utf-8") as f:
            original = f.read()
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(original)
        return {"status": "restored", "path": abs_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/cleanup-backup")
async def cleanup_backup(req: RestoreRequest):
    """Deletes the .bak backup file after the user accepts or rejects an AI diff."""
    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)

    bak_path = abs_path + ".bak"
    if os.path.exists(bak_path):
        try:
            os.remove(bak_path)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok"}


@app.post("/delete-file")
async def delete_file_endpoint(req: RestoreRequest):
    """Deletes a newly AI-created file when the user rejects it."""
    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)

    if not os.path.exists(abs_path):
        raise HTTPException(status_code=404, detail="File not found")
    try:
        os.remove(abs_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"status": "ok"}


@app.post("/rename")
async def rename_entry(req: RenameRequest):
    """Renames (moves) a file or directory."""
    if state.workspace_path:
        abs_old = _safe_path(state.workspace_path, req.old_path)
        abs_new = _safe_path(state.workspace_path, req.new_path)
    else:
        abs_old = os.path.realpath(req.old_path)
        abs_new = os.path.realpath(req.new_path)

    if not os.path.exists(abs_old):
        raise HTTPException(status_code=404, detail="Source path not found")
    if os.path.exists(abs_new):
        raise HTTPException(status_code=409, detail="Destination already exists")

    try:
        os.rename(abs_old, abs_new)
        return {"status": "ok", "old_path": abs_old, "new_path": abs_new}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/delete-dir")
async def delete_directory(req: RestoreRequest):
    """Recursively deletes a directory."""
    import shutil

    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)

    if not os.path.isdir(abs_path):
        raise HTTPException(status_code=404, detail="Directory not found")

    try:
        shutil.rmtree(abs_path)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/complete")
async def complete_code(req: CompleteRequest):
    """Fast inline code completion using the LLM fast model."""
    if not state.llm:
        raise HTTPException(status_code=400, detail="Engine not initialized — open a workspace first")

    filename = os.path.basename(req.path.replace("\\", "/").replace("file:///", ""))
    prompt = (
        f"You are a code completion engine for {req.language}. "
        f"File: {filename}\n\n"
        f"Code before cursor:\n{req.prefix[-900:]}\n"
        f"Code after cursor:\n{req.suffix[:200]}\n\n"
        "Complete the code at the cursor position. "
        "Return ONLY the inserted text (1–4 lines max). No explanation, no markdown fences."
    )

    try:
        completion = state.llm._call(
            messages=[{"role": "user", "content": prompt}],
            model=state.llm.fast_model,
            temperature=0.1,
            max_tokens=120,
        )
        return {"completion": completion.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LintRequest(BaseModel):
    file_path: str
    content: str
    workspace: str = ""

    @field_validator("content")
    @classmethod
    def content_max_size(cls, v: str) -> str:
        if len(v.encode("utf-8")) > 1 * 1024 * 1024:  # 1 MB cap for linting
            raise ValueError("content too large for linting")
        return v


@app.post("/lint")
@limiter.limit("120/minute")
async def lint_code(request: Request, req: LintRequest):
    """Real-time lint — pipes editor content to ruff (Python) or eslint (JS/TS)."""
    from src.tools.lint_tool import LintTool
    tool = LintTool()
    try:
        diagnostics = tool.lint_content(req.content, req.file_path, req.workspace)
        return {"diagnostics": diagnostics}
    except Exception as e:
        logger.warning("Lint endpoint error: %s", e)
        return {"diagnostics": []}


# ── Chat Session Endpoints ───────────────────────────────────────────────────

class SessionRequest(BaseModel):
    user_id: str
    repo_identifier: str

class NewSessionRequest(BaseModel):
    user_id: str
    repo_identifier: str
    title: Optional[str] = None

@app.post("/chat/sessions")
async def create_chat_session(req: NewSessionRequest):
    """Create a new chat session for this user+repo."""
    if not state.chat_memory:
        return {"session_id": None, "error": "ChatMemory not initialized"}
    session_id = state.chat_memory.create_session(req.user_id, req.repo_identifier, req.title)
    return {"session_id": session_id}

@app.get("/chat/sessions")
async def list_chat_sessions(user_id: str, repo_identifier: str):
    """List recent sessions for a user+repo (newest first)."""
    if not state.chat_memory:
        return {"sessions": []}
    sessions = state.chat_memory.list_sessions(user_id, repo_identifier)
    return {"sessions": sessions}

@app.get("/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Load messages for a session (for frontend display)."""
    if not state.chat_memory:
        return {"messages": []}
    messages = state.chat_memory.load_messages_for_display(session_id)
    return {"messages": messages}

@app.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    """Delete a chat session and all its messages."""
    if not state.chat_memory:
        return {"status": "ok"}
    ok = state.chat_memory.delete_session(session_id)
    return {"status": "ok" if ok else "error"}


class QueueWriter:
    """A file-like object that pushes each print() line into a thread-safe queue."""
    def __init__(self, log_queue: queue.Queue):
        self._queue = log_queue
        self._buffer = ""

    def write(self, text: str):
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._queue.put(line)

    def flush(self):
        if self._buffer.strip():
            self._queue.put(self._buffer.strip())
            self._buffer = ""


@app.post("/chat")
@limiter.limit("10/minute")
async def chat(request: Request, req: ChatRequest):
    # ── Prompt injection guard ────────────────────────────────
    guard_result = state.prompt_guard.scan(req.query)
    if guard_result.should_log:
        ip = request.client.host if request.client else None
        log_security_event(
            query=req.query,
            result=guard_result,
            user_id=req.user_id,
            ip_address=ip,
            blocked=not guard_result.is_safe,
        )
    if not guard_result.is_safe:
        raise HTTPException(
            status_code=400,
            detail="Query blocked by security policy. This attempt has been logged.",
        )
    # ─────────────────────────────────────────────────────────
    _ensure_initialized(req.path)

    # ── Topic / content policy ────────────────────────────────
    # Wire TopicGuard with the LLM client after first init
    if state.topic_guard is None:
        state.topic_guard = TopicGuard(llm=state.llm)

    topic_result = state.topic_guard.classify(req.query)
    if not topic_result.allowed:
        logger.info(
            "[TopicGuard] Rejected query (reason=%s tier=%s): %.80s",
            topic_result.reason, topic_result.tier, req.query,
        )
        raise HTTPException(status_code=400, detail=topic_result.refusal_message)
    # ─────────────────────────────────────────────────────────

    async def stream_response():
        log_queue = queue.Queue()
        result_holder = {}

        # Use local copies of state objects to satisfy type checkers
        llm = state.llm
        project_context = state.project_context
        agent_tools = state.agent_tools
        pipeline_ctx = state.pipeline_ctx
        history = state.history
        chat_memory = state.chat_memory

        # ── Persistent chat session ─────────────────────────────────────────
        session_id: Optional[str] = None
        effective_history = req.history   # default: use frontend-provided history

        if req.user_id and chat_memory:
            repo_id = SupabaseMemory.make_repo_identifier(req.path, is_github=False)
            session_id = chat_memory.get_or_create_session(req.user_id, repo_id)
            if session_id:
                # Replace raw frontend history with persisted summarized context
                persistent_ctx = chat_memory.get_context_for_llm(session_id)
                if persistent_ctx:
                    effective_history = persistent_ctx
        # ───────────────────────────────────────────────────────────────────

        pending_save = state._pending_memory_save
        supabase_mem = state.supabase_memory

        def run_pipeline():
            writer = QueueWriter(log_queue)
            try:
                with redirect_stdout(writer):
                    if req.agent_mode:
                        # Agent mode: Plan → Execute → Verify
                        answer, changeset_dict = run_agent_pipeline(
                            question=req.query,
                            search_path=req.path,
                            llm=llm,
                            project_context=project_context,
                            available_tools=state.available_tools or AVAILABLE_TOOLS,
                            tools=agent_tools,
                            history=effective_history,
                            ctx=pipeline_ctx or PipelineContext(req.path),
                            terminal_tool=state.terminal_tool,
                            state=state,
                            session_id=session_id,
                            session_memory=state.session_memory,
                        )
                        context = ""
                        # Store changeset for rollback
                        if changeset_dict and changeset_dict.get("id"):
                            state.active_changesets[changeset_dict["id"]] = changeset_dict
                    else:
                        answer, context = run_local_pipeline(
                            question=req.query,
                            search_path=req.path,
                            llm=llm,
                            project_context=project_context,
                            available_tools=state.available_tools or AVAILABLE_TOOLS,
                            tools=agent_tools,
                            history=effective_history,
                            ctx=pipeline_ctx or PipelineContext(req.path),
                        )
                writer.flush()
                result_holder["answer"] = answer
                result_holder["context"] = context

                # ── Save symbol graph to Supabase after first build ────────────
                if pending_save and pipeline_ctx and pipeline_ctx._sym_extractor:
                    symbols = pipeline_ctx._sym_extractor.symbols
                    call_graph_data = None
                    if pipeline_ctx._call_graph:
                        try:
                            cg = pipeline_ctx._call_graph
                            call_graph_data = {
                                "callees": {k: list(v) for k, v in cg.callees.items()},
                                "callers": {k: list(v) for k, v in cg.callers.items()},
                                "node_info": cg.node_info,
                            }
                        except Exception:
                            pass
                    if symbols:
                        supabase_mem.schedule_save(
                            user_id=pending_save["user_id"],
                            repo_identifier=pending_save["repo_id"],
                            repo_display=pending_save["repo_display"],
                            commit_sha=pending_save["commit_sha"],
                            symbols=symbols,
                            call_graph=call_graph_data,
                        )
                        state._pending_memory_save = None  # only save once
                # ──────────────────────────────────────────────────────────────

            except Exception as e:
                import traceback
                traceback.print_exc()
                result_holder["answer"] = f"Pipeline error: {e}"
                result_holder["context"] = ""
            finally:
                log_queue.put(None)  # Sentinel: ALWAYS signal completion

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, cast(Callable[..., Any], run_pipeline))

        # Poll the queue and yield each log line in real-time
        while True:
            await asyncio.sleep(0.02)  # 20ms polling for snappier streaming
            while not log_queue.empty():
                line = log_queue.get_nowait()
                if line is None:
                    # Pipeline finished — streaming answer was already sent as tokens
                    answer = result_holder.get("answer", "")
                    if not result_holder.get("answer_streamed"):
                        # Fallback: answer was not streamed (e.g. mock provider)
                        yield json.dumps({"type": "answer", "content": answer}) + "\n"
                    if history:
                        history.add_interaction(str(req.query), str(answer))
                    # Persist to Supabase chat session (fire-and-forget)
                    if session_id and chat_memory:
                        chat_memory.add_message(session_id, "user", str(req.query))
                        chat_memory.add_message(session_id, "assistant", str(answer))
                    return

                if line.startswith("[ANSWER_TOKEN]"):
                    try:
                        token = json.loads(line[len("[ANSWER_TOKEN]"):])
                    except Exception:
                        token = line[len("[ANSWER_TOKEN]"):]
                    result_holder["answer_streamed"] = True
                    yield json.dumps({"type": "answer_token", "content": token}) + "\n"
                elif line.startswith("[UI_COMMAND] "):
                    parts = line.replace("[UI_COMMAND] ", "").split(" ", 1)
                    cmd = parts[0]
                    args = parts[1] if len(parts) > 1 else ""
                    yield json.dumps({"type": "ui_command", "command": cmd, "args": args}) + "\n"
                else:
                    yield json.dumps({"type": "log", "content": line}) + "\n"

    return StreamingResponse(stream_response(), media_type="application/x-ndjson")


@app.post("/index")
@limiter.limit("5/minute")
async def reindex(request: Request, req: IndexRequest):
    """Trigger a full re-index of the workspace."""
    _ensure_initialized(req.path)

    # Force rebuild
    state.pipeline_ctx = PipelineContext(
        search_path=req.path,
        rebuild_index=True,
    )

    return {"status": "reindex_triggered", "path": req.path}


@app.post("/autocomplete", response_model=AutocompleteResponse)
@limiter.limit("30/minute")
async def autocomplete(request: Request, req: AutocompleteRequest):
    llm = state.llm
    if not llm or not llm.client:
        raise HTTPException(status_code=503, detail="No LLM client configured")

    code_ctx = str(req.code_context)
    prompt = f"""You are an expert code completion engine.
Given the code context below, predict the next 3-5 lines of code.
Return ONLY the code, no explanations.

<file_path>{req.file_path}</file_path>
<code_context>
{code_ctx[-3000:]}
</code_context>

Complete the code:"""

    try:
        response = llm.client.chat.completions.create(
            model=llm.fast_model,   # was hardcoded "gpt-4o-mini"
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
        )
        completion = response.choices[0].message.content.strip()
        if completion.startswith("```"):
            lines = completion.split("\n")
            completion = "\n".join(
                lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            )
        return {"completion": completion}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Autocomplete failed: {e}")

# ── Embedded Terminal ────────────────────────────────────────────────────────

@app.websocket("/terminal")
async def terminal_ws(websocket: WebSocket, cwd: Optional[str] = None):
    """
    WebSocket terminal using ConPTY (pywinpty) on Windows, pty module on Linux/Mac.
    Full PTY support: backspace, arrow keys, tab completion, colours all work.
    Text messages: raw terminal input bytes (str).
    JSON messages: {"type":"resize","cols":N,"rows":N} to resize the PTY.
    """
    await websocket.accept()

    # Use explicitly passed cwd, fall back to workspace, then home dir
    if cwd and os.path.isdir(cwd):
        cwd = os.path.realpath(cwd)
    else:
        cwd = state.workspace_path or os.path.expanduser("~")
    loop = asyncio.get_event_loop()

    if platform.system() == "Windows":
        # ── Windows: use pywinpty ConPTY ──────────────────────────────────────
        try:
            from winpty import PtyProcess
        except ImportError:
            await websocket.send_text(
                "\r\n\x1b[31m[pywinpty not installed — run: pip install pywinpty]\x1b[0m\r\n"
            )
            await websocket.close()
            return

        try:
            pty_proc = PtyProcess.spawn("cmd.exe", dimensions=(24, 220), cwd=cwd)
        except Exception as e:
            await websocket.send_text(f"\r\n\x1b[31m[PTY failed to start: {e}]\x1b[0m\r\n")
            await websocket.close()
            return

        stop_event = asyncio.Event()

        def _read_pty():
            """Blocking PTY read — runs in a thread."""
            while not stop_event.is_set():
                try:
                    data = pty_proc.read(4096)
                    if data:
                        asyncio.run_coroutine_threadsafe(
                            websocket.send_text(data), loop
                        )
                except EOFError:
                    break
                except Exception:
                    break

        read_thread = threading.Thread(target=_read_pty, daemon=True)
        read_thread.start()

        try:
            while True:
                msg = await websocket.receive_text()
                # JSON → resize control message
                try:
                    ctrl = json.loads(msg)
                    if ctrl.get("type") == "resize":
                        pty_proc.setwinsize(
                            int(ctrl.get("rows", 24)),
                            int(ctrl.get("cols", 220)),
                        )
                    continue
                except (json.JSONDecodeError, TypeError, ValueError):
                    pass
                # Regular keystrokes → PTY stdin
                pty_proc.write(msg)
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            stop_event.set()
            try:
                pty_proc.terminate(force=True)
            except Exception:
                pass

    else:
        # ── Linux / Mac: use pty module ───────────────────────────────────────
        import pty as pty_mod, fcntl, termios, struct

        master_fd, slave_fd = pty_mod.openpty()
        try:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
                cwd=cwd, close_fds=True,
            )
        except Exception as e:
            os.close(master_fd)
            os.close(slave_fd)
            await websocket.send_text(f"\r\n\x1b[31m[Shell failed: {e}]\x1b[0m\r\n")
            await websocket.close()
            return
        os.close(slave_fd)

        async def _read():
            while True:
                try:
                    data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                    await websocket.send_text(data.decode("utf-8", errors="replace"))
                except Exception:
                    break

        async def _write():
            try:
                while True:
                    msg = await websocket.receive_text()
                    try:
                        ctrl = json.loads(msg)
                        if ctrl.get("type") == "resize":
                            rows = int(ctrl.get("rows", 24))
                            cols = int(ctrl.get("cols", 220))
                            fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                        struct.pack("HHHH", rows, cols, 0, 0))
                        continue
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                    os.write(master_fd, msg.encode("utf-8", errors="replace"))
            except (WebSocketDisconnect, Exception):
                pass

        try:
            await asyncio.gather(_read(), _write())
        finally:
            try:
                os.close(master_fd)
                process.terminate()
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except Exception:
                pass


# ── Agent API Routes ──────────────────────────────────────────────────────────


class AgentRollbackRequest(BaseModel):
    changeset_id: str
    file_path: Optional[str] = None  # None = rollback all


class AgentRespondRequest(BaseModel):
    response: str
    step_id: Optional[int] = None  # which step asked the question
    question: Optional[str] = None  # the question that was asked


@app.post("/agent/rollback")
async def agent_rollback(req: AgentRollbackRequest):
    """Rollback agent changes — all files or a single file."""
    from src.agent.changeset import Changeset

    changeset_data = state.active_changesets.get(req.changeset_id)
    if not changeset_data:
        raise HTTPException(status_code=404, detail="Changeset not found")

    # Reconstruct changeset from stored data for rollback
    # For now, we store the full changeset object
    changeset = changeset_data if isinstance(changeset_data, Changeset) else None
    if not changeset:
        raise HTTPException(status_code=400, detail="Changeset data is summary-only; full rollback requires active changeset")

    if req.file_path:
        workspace = state.workspace_path
        if not workspace:
            raise HTTPException(status_code=400, detail="No workspace initialized")
        abs_path = os.path.abspath(os.path.join(workspace, req.file_path))
        result = changeset.rollback_file(abs_path)
        return {"status": "rolled_back", "detail": result}
    else:
        results = changeset.rollback_all()
        return {"status": "rolled_back", "details": results}


@app.post("/agent/accept")
async def agent_accept(req: AgentRollbackRequest):
    """Accept agent changes — clean up backups."""
    from src.agent.changeset import Changeset

    changeset_data = state.active_changesets.get(req.changeset_id)
    if not changeset_data:
        raise HTTPException(status_code=404, detail="Changeset not found")

    if isinstance(changeset_data, Changeset):
        changeset_data.accept()
    state.active_changesets.pop(req.changeset_id, None)
    return {"status": "accepted"}


@app.post("/agent/cancel")
async def agent_cancel():
    """Cancel the currently running agent task."""
    executor = state.active_executor
    if executor:
        executor.cancel()
        return {"status": "cancelling"}
    return {"status": "no_active_task"}


@app.post("/agent/respond")
async def agent_respond(req: AgentRespondRequest):
    """Send a user response to a paused agent (human-in-the-loop)."""
    executor = state.active_executor
    if executor:
        # Store human feedback to session memory if available
        if (
            state.session_memory
            and state.active_session_id
            and state.active_task_id
            and req.step_id is not None
            and req.question is not None
        ):
            state.session_memory.add_human_feedback(
                session_id=state.active_session_id,
                task_id=state.active_task_id,
                step_id=req.step_id,
                question=req.question,
                response=req.response,
            )
        executor.provide_user_response(req.response)
        return {"status": "response_sent"}
    return {"status": "no_active_task"}


@app.get("/agent/changesets")
async def list_changesets():
    """List all active changesets."""
    summaries = []
    for cid, data in state.active_changesets.items():
        if isinstance(data, dict):
            summaries.append(data)
        else:
            summaries.append(data.to_dict())
    return {"changesets": summaries}


# entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
