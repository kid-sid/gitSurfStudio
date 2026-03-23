"""
GitSurf Studio — AI Engine Server

A FastAPI server that wraps the GitSurf PRAR pipeline, providing:
  - POST /chat       — Streaming agent responses (SSE)
  - POST /index      — Trigger re-indexing of the workspace
  - POST /autocomplete — Fast inline code completions
  - GET  /health     — Health check
"""

import os
import sys
import json
import asyncio
import threading
import requests
from typing import Optional, List, Dict, Any, Callable, cast
from contextlib import redirect_stdout
import queue
from dotenv import load_dotenv

# Try to load .env from current directory, then from parent (project root)
if not load_dotenv():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI, HTTPException, Request
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
from src.verifier import AnswerVerifier
from src.tools.file_editor_tool import FileEditorTool
from src.tools.search_tool import SearchTool
from src.tools.web_tool import WebSearchTool
from src.tools.repo_manager import RepoManager
from src.tools.git_tool import GitTool
from src.tools.editor_ui_tool import EditorUITool
from src.tools.symbol_extractor import SymbolExtractor
from src.orchestrator import run_code_aware_pipeline, run_local_pipeline, PipelineContext

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
    resolved = os.path.realpath(user_path)
    if not resolved.startswith(workspace_real):
        raise HTTPException(status_code=403, detail="Path outside of workspace")
    return resolved


class ChatRequest(BaseModel):
    query: str
    path: str
    history: Optional[List[Dict[str, str]]] = []

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

AVAILABLE_TOOLS = """
Tool: FileEditorTool
Description: Read, write, modify, or delete files inside the project directory.
Methods:
  - read_file(rel_path, start_line=None, end_line=None)
  - write_file(rel_path, content)
  - replace_in_file(rel_path, target, replacement, allow_multiple=False)
    NOTE: target must match exactly once unless allow_multiple=True.
    On ambiguity, returns the line numbers of all matches to help refine the target.
  - delete_file(rel_path)

Tool: EditorUITool
Description: Control the IDE user interface.
Methods:
  - open_file(rel_path)
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
"""


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

        state.agent_tools = {
            "FileEditorTool": file_editor,
            "GitTool": git_tool,
            "EditorUITool": editor_ui,
            "SearchTool": searcher,
            "WebSearchTool": web_tool,
        }
        state.git_tool = git_tool

        state.pipeline_ctx = PipelineContext(
            search_path=workspace_path,
            rebuild_index=False,
        )


@app.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "workspace": state.workspace_path}


@app.post("/init", response_model=InitResponse)
@limiter.limit("10/minute")
async def init_workspace(request: Request, req: InitRequest):
    target_path = req.input.strip()

    if "github.com" in target_path:
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

    _ensure_initialized(target_path)

    return {
        "status": "success",
        "workspace_path": target_path,
        "has_project_context": bool(state.project_context),
        "is_github": "github.com" in req.input.lower(),
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
                if item in {".git", "__pycache__", "node_modules", ".cache", "venv", ".venv"}:
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
        # Ensure parent directories exist (dirname is empty for root-level paths)
        parent_dir = os.path.dirname(abs_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        
        # Use FileEditorTool if it exists to preserve its logic (e.g. rel_path handling)
        if state.workspace_path and "FileEditorTool" in state.agent_tools:
            editor = state.agent_tools["FileEditorTool"]
            try:
                rel_path = os.path.relpath(abs_path, state.workspace_path)
                res = editor.write_file(rel_path, req.content)
                if "[Error]" in res:
                    raise Exception(res)
                return {"status": "success", "message": res}
            except ValueError:
                # Path might be absolute but outside root or just tricky relpath
                pass

        # Fallback to direct write
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "success", "message": f"Wrote to {abs_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
    _ensure_initialized(req.path)

    async def stream_response():
        log_queue = queue.Queue()
        result_holder = {}
        
        # Use local copies of state objects to satisfy type checkers
        llm = state.llm
        project_context = state.project_context
        agent_tools = state.agent_tools
        pipeline_ctx = state.pipeline_ctx
        history = state.history

        def run_pipeline():
            writer = QueueWriter(log_queue)
            try:
                with redirect_stdout(writer):
                    answer, context = run_local_pipeline(
                        question=req.query,
                        search_path=req.path,
                        llm=llm,
                        project_context=project_context,
                        available_tools=AVAILABLE_TOOLS,
                        tools=agent_tools,
                        history=req.history,
                        ctx=pipeline_ctx or PipelineContext(req.path),
                    )
                writer.flush()
                result_holder["answer"] = answer
                result_holder["context"] = context
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
            await asyncio.sleep(0.05)  # 50ms polling interval
            while not log_queue.empty():
                line = log_queue.get_nowait()
                if line is None:
                    # Pipeline finished — yield the final answer
                    answer = result_holder.get("answer", "")
                    yield json.dumps({"type": "answer", "content": answer}) + "\n"
                    if history:
                        history.add_interaction(str(req.query), str(answer))
                    return

                if line.startswith("[UI_COMMAND] "):
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

# entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
