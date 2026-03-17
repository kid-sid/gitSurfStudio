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
import requests
from typing import Optional, List, Dict
from contextlib import redirect_stdout
import queue
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, RedirectResponse, HTMLResponse
from pydantic import BaseModel

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
from src.orchestrator import run_code_aware_pipeline, run_local_pipeline, PipelineContext

app = FastAPI(
    title="GitSurf Studio Engine",
    description="AI-powered codebase reasoning engine",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tauri app runs on localhost
    allow_methods=["*"],
    allow_headers=["*"],
)

class EngineState:
    def __init__(self):
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

    def get_active_token(self) -> Optional[str]:
        return self.github_token or os.getenv("GITHUB_TOKEN")

state = EngineState()


class ChatRequest(BaseModel):
    query: str
    path: str  # Absolute path to the workspace/project folder
    history: Optional[List[Dict[str, str]]] = []

class IndexRequest(BaseModel):
    path: str

class InitRequest(BaseModel):
    input: str # Local path or GitHub URL

class AutocompleteRequest(BaseModel):
    code_context: str  # Last N lines of code before the cursor
    file_path: str     # Current file being edited
    path: str          # Workspace root

class WriteRequest(BaseModel):
    path: str
    content: str

class GitStatusRequest(BaseModel):
    path: str

class GitStageRequest(BaseModel):
    path: str
    files: List[str]

class GitCommitRequest(BaseModel):
    path: str
    message: str

class GitForkRequest(BaseModel):
    path: str
    repo_name: str


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
    if state.workspace_path == workspace_path and state.llm is not None:
        return

    state.workspace_path = workspace_path
    state.llm = LLMClient()
    state.history = HistoryManager(
        history_file=os.path.join(workspace_path, ".gitsurf_history.json")
    )

    # ── Wire project context ──────────────────────────────────────────
    # Check for README variants in priority order
    readme_candidates = ["README.md", "readme.md", "README.rst", "README.txt"]
    state.project_context = ""
    for candidate in readme_candidates:
        readme_path = os.path.join(workspace_path, candidate)
        if os.path.exists(readme_path):
            try:
                with open(readme_path, "r", encoding="utf-8", errors="replace") as f:
                    readme_content = f.read()
                print(f"[Init] Analyzing project context from {candidate}...")
                state.project_context = state.llm.analyze_project_context(readme_content)
                print(f"[Init] Project context ready ({len(state.project_context)} chars)")
            except Exception as e:
                print(f"[Init] Warning: Could not analyze README: {e}")
            break   # stop at first found
    # ─────────────────────────────────────────────────────────────────

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


@app.get("/health")
async def health():
    return {"status": "ok", "workspace": state.workspace_path}


@app.post("/init")
async def init_workspace(req: InitRequest):
    target_path = req.input.strip()

    if "github.com" in target_path:
        repo_name = target_path.split("github.com/")[-1].strip("/")
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
            
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

@app.get("/git/status")
async def git_status(path: str):
    _ensure_initialized(path)
    if not state.git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    return {"status": state.git_tool.get_status()}

@app.post("/git/stage")
async def git_stage(req: GitStageRequest):
    _ensure_initialized(req.path)
    if not state.git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    result = state.git_tool.stage_files(req.files)
    return {"message": result}

@app.post("/git/commit")
async def git_commit(req: GitCommitRequest):
    _ensure_initialized(req.path)
    if not state.git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    result = state.git_tool.commit(req.message)
    return {"message": result}

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
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")

    def build_tree(current_path):
        name = os.path.basename(current_path)
        if not name and current_path: # Handle root path
             name = current_path
             
        if os.path.isfile(current_path):
            return {"name": name, "type": "file", "path": current_path}
        
        children = []
        try:
            for item in sorted(os.listdir(current_path)):
                if item in {".git", "__pycache__", "node_modules", ".cache", "venv", ".venv"}:
                    continue
                children.append(build_tree(os.path.join(current_path, item)))
        except PermissionError:
            pass
            
        return {"name": name, "type": "dir", "path": current_path, "children": children}

    return build_tree(path)


@app.get("/read")
async def read_file(path: str):
    """Reads a file's content from the local disk."""
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
    
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return {"content": f.read()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/write")
async def write_file(req: WriteRequest):
    """Writes content to a file on local disk."""
    abs_path = os.path.abspath(req.path)
    
    # Safety check: Ensure the path is within the workspace if one is active
    if state.workspace_path:
        if not abs_path.startswith(state.workspace_path):
             raise HTTPException(status_code=403, detail="Path outside of workspace")
    
    try:
        # Ensure directories exist
        os.makedirs(os.path.dirname(abs_path), exist_ok=True)
        
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
async def chat(req: ChatRequest):
    _ensure_initialized(req.path)

    async def stream_response():
        log_queue = queue.Queue()
        result_holder = {}

        def run_pipeline():
            writer = QueueWriter(log_queue)
            with redirect_stdout(writer):
                answer, context = run_local_pipeline(
                    question=req.query,
                    search_path=req.path,
                    llm=state.llm,
                    project_context=state.project_context,
                    available_tools=AVAILABLE_TOOLS,
                    tools=state.agent_tools,
                    history=req.history,
                    ctx=state.pipeline_ctx,
                )
            writer.flush()
            result_holder["answer"] = answer
            result_holder["context"] = context
            log_queue.put(None)  # Sentinel: pipeline done

        loop = asyncio.get_event_loop()
        loop.run_in_executor(None, run_pipeline)

        # Poll the queue and yield each log line in real-time
        while True:
            await asyncio.sleep(0.05)  # 50ms polling interval
            while not log_queue.empty():
                line = log_queue.get_nowait()
                if line is None:
                    # Pipeline finished — yield the final answer
                    answer = result_holder.get("answer", "")
                    yield json.dumps({"type": "answer", "content": answer}) + "\n"
                    if state.history:
                        state.history.add_interaction(req.query, answer)
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
async def reindex(req: IndexRequest):
    """Trigger a full re-index of the workspace."""
    _ensure_initialized(req.path)

    # Force rebuild
    state.pipeline_ctx = PipelineContext(
        search_path=req.path,
        rebuild_index=True,
    )

    return {"status": "reindex_triggered", "path": req.path}


@app.post("/autocomplete")
async def autocomplete(req: AutocompleteRequest):
    _ensure_initialized(req.path)

    if not state.llm or not state.llm.client:
        raise HTTPException(status_code=503, detail="No LLM client configured")

    prompt = f"""You are an expert code completion engine.
Given the code context below, predict the next 3-5 lines of code.
Return ONLY the code, no explanations.

<file_path>{req.file_path}</file_path>
<code_context>
{req.code_context[-3000:]}
</code_context>

Complete the code:"""

    try:
        response = state.llm.client.chat.completions.create(
            model=state.llm.fast_model,   # was hardcoded "gpt-4o-mini"
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
