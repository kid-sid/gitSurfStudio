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
from typing import Optional, List, Dict
from contextlib import redirect_stdout
from io import StringIO
from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
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
    """Holds initialized components across requests (zero cold-start)."""
    def __init__(self):
        self.llm: Optional[LLMClient] = None
        self.history: Optional[HistoryManager] = None
        self.pipeline_ctx: Optional[PipelineContext] = None
        self.agent_tools: Dict = {}
        self.workspace_path: Optional[str] = None
        self.repo_manager = RepoManager(cache_dir=os.path.join(os.path.dirname(__file__), ".cache"))

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


AVAILABLE_TOOLS = """
Tool: FileEditorTool
Description: Read, write, modify, or delete files inside the project directory.
Methods:
  - read_file(rel_path, start_line=None, end_line=None)
  - write_file(rel_path, content)
  - replace_in_file(rel_path, target, replacement)
  - delete_file(rel_path)

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
    """Initialize or re-initialize engine components for the given workspace."""
    if state.workspace_path == workspace_path and state.llm is not None:
        return  # Already initialized for this workspace

    state.workspace_path = workspace_path
    state.llm = LLMClient()
    state.history = HistoryManager(
        history_file=os.path.join(workspace_path, ".gitsurf_history.json")
    )

    file_editor = FileEditorTool(root_path=workspace_path)
    searcher = SearchTool()
    web_tool = WebSearchTool()

    state.agent_tools = {
        "FileEditorTool": file_editor,
        "SearchTool": searcher,
        "WebSearchTool": web_tool,
    }

    state.pipeline_ctx = PipelineContext(
        search_path=workspace_path,
        rebuild_index=False,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "workspace": state.workspace_path}


@app.post("/init")
async def init_workspace(req: InitRequest):
    """
    Initializes a workspace from a local path or GitHub URL.
    """
    target_path = req.input.strip()

    # Detect GitHub URL
    if "github.com" in target_path:
        repo_name = target_path.split("github.com/")[-1].strip("/")
        try:
            target_path = state.repo_manager.sync_repo(repo_name)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to clone repository: {e}")
    else:
        # Local path validation
        target_path = os.path.abspath(target_path)
        if not os.path.exists(target_path):
            raise HTTPException(status_code=404, detail="Local path does not exist")

    _ensure_initialized(target_path)
    return {"status": "success", "workspace_path": target_path}


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


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Run the full PRAR pipeline for a user query.
    Returns a streaming response with the agent's thought process and final answer.
    """
    _ensure_initialized(req.path)

    async def stream_response():
        # Capture stdout from the pipeline (step labels, thoughts, observations)
        captured = StringIO()

        def run_pipeline():
            with redirect_stdout(captured):
                answer, context = run_local_pipeline(
                    question=req.query,
                    search_path=req.path,
                    llm=state.llm,
                    project_context="",
                    available_tools=AVAILABLE_TOOLS,
                    tools=state.agent_tools,
                    history=req.history,
                    ctx=state.pipeline_ctx,
                )
            return answer, context

        # Run the blocking pipeline in a thread to keep FastAPI async
        loop = asyncio.get_event_loop()
        answer, context = await loop.run_in_executor(None, run_pipeline)

        # Stream the captured pipeline logs first
        logs = captured.getvalue()
        if logs:
            yield json.dumps({"type": "log", "content": logs}) + "\n"

        # Then stream the final answer
        yield json.dumps({"type": "answer", "content": answer}) + "\n"

        # Save to history
        if state.history:
            state.history.add_interaction(req.query, answer)

    return StreamingResponse(
        stream_response(),
        media_type="application/x-ndjson",
    )


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
    """
    Fast inline code completion. Skips the heavy PRAR pipeline
    and hits the LLM directly with the code context.
    """
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
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=200,
            temperature=0.2,
        )
        completion = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if completion.startswith("```"):
            lines = completion.split("\n")
            completion = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])

        return {"completion": completion}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Autocomplete failed: {e}")

# entry point
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
