"""Git routes: status, stage, commit, branch, checkout, stash, discard, fork, diff-lines."""

import os
import re
import subprocess

from fastapi import APIRouter, HTTPException

from src.engine_state import _safe_path, state
from src.models import (
    BranchResponse,
    GitCheckoutRequest,
    GitCommitRequest,
    GitDiscardRequest,
    GitForkRequest,
    GitMessageResponse,
    GitStageRequest,
    GitStashRequest,
    GitStatusResponse,
)
from src.routes import _ensure_initialized

router = APIRouter(prefix="/git")


@router.get("/status", response_model=GitStatusResponse)
async def git_status(path: str):
    git = state.git_tool
    if not git:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    status = git.get_status()
    return {"status": status}


@router.post("/stage", response_model=GitMessageResponse)
async def git_stage(req: GitStageRequest):
    git = state.git_tool
    if not git:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    result = git.stage_files(req.files)
    return {"message": result}


@router.post("/commit", response_model=GitMessageResponse)
async def git_commit(req: GitCommitRequest):
    git = state.git_tool
    if not git:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    result = git.commit(req.message)
    return {"message": result}


@router.get("/branch", response_model=BranchResponse)
async def git_branch(path: str):
    _ensure_initialized(path)
    git_tool = state.git_tool
    if not git_tool:
        raise HTTPException(status_code=400, detail="GitTool not initialized")
    return git_tool.get_branches()


@router.post("/checkout")
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


@router.post("/stash")
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


@router.post("/discard")
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


@router.post("/fork")
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


@router.get("/diff-lines")
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
