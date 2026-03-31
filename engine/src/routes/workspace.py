"""Workspace routes: init, files, read, write, mkdir, restore, cleanup, delete, rename, index."""

import os
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from src.engine_state import _safe_path, state
from src.memory.supabase_memory import SupabaseMemory
from src.models import (
    IndexRequest,
    InitRequest,
    InitResponse,
    MkdirRequest,
    ReadResponse,
    RenameRequest,
    RestoreRequest,
    WriteRequest,
    WriteResponse,
)
from src.orchestrator import PipelineContext
from src.routes import _ensure_initialized, limiter, logger

router = APIRouter()


@router.post("/init", response_model=InitResponse)
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
            _engine_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            cache_base = os.path.join(_engine_dir, ".cache")
            injected = state.supabase_memory.load_and_inject_cache(
                user_id, repo_id,
                symbol_cache_dir=os.path.join(cache_base, "symbols"),
                call_graph_cache_dir=os.path.join(cache_base, "call_graph"),
            )
            if injected:
                logger.info(
                    "[SupabaseMemory] Loaded symbol cache from Supabase (SHA: %s)",
                    current_sha,
                )
        else:
            state._pending_memory_save = {
                "user_id": user_id,
                "repo_id": repo_id,
                "repo_display": (
                    repo_name if (is_github and repo_name)
                    else os.path.basename(target_path)
                ),
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


@router.get("/files")
async def get_files(path: str):
    """Returns a JSON tree of the workspace files."""
    if state.workspace_path:
        path = _safe_path(state.workspace_path, path)
    else:
        path = os.path.realpath(path)

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Path not found")

    def build_tree(current_path):
        current_path_str = str(current_path)
        name = os.path.basename(current_path_str)
        if not name and current_path_str:
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


@router.get("/read", response_model=ReadResponse)
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


@router.post("/write", response_model=WriteResponse)
async def write_file(req: WriteRequest):
    """Writes content to a file on local disk."""
    if state.workspace_path:
        abs_path = _safe_path(state.workspace_path, req.path)
    else:
        abs_path = os.path.realpath(req.path)

    try:
        parent_dir = os.path.dirname(abs_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(req.content)
        return {"status": "success", "message": f"Wrote to {abs_path}"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/mkdir")
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


@router.post("/restore")
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


@router.post("/cleanup-backup")
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


@router.post("/delete-file")
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


@router.post("/rename")
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


@router.post("/delete-dir")
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


@router.post("/index")
@limiter.limit("5/minute")
async def reindex(request: Request, req: IndexRequest):
    """Trigger a full re-index of the workspace."""
    _ensure_initialized(req.path)

    state.pipeline_ctx = PipelineContext(
        search_path=req.path,
        rebuild_index=True,
    )

    return {"status": "reindex_triggered", "path": req.path}
