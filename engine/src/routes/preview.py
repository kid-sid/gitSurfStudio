"""Preview route: spawn and manage dev server processes for live preview."""

import asyncio
import contextlib
import os
import signal
import subprocess
import sys

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.engine_state import state
from src.routes import logger

router = APIRouter(prefix="/preview")

# Track running preview processes
_preview_procs: dict[str, subprocess.Popen] = {}


class PreviewStartRequest(BaseModel):
    workspace: str | None = None
    command: str | None = None
    port: int = 3000


class PreviewStopRequest(BaseModel):
    workspace: str | None = None


# Well-known dev server commands by project type
_DEV_COMMANDS = [
    # (detection file, command, default port)
    ("package.json", "npm run dev", 5173),
    ("vite.config.ts", "npm run dev", 5173),
    ("vite.config.js", "npm run dev", 5173),
    ("next.config.js", "npm run dev", 3000),
    ("next.config.mjs", "npm run dev", 3000),
    ("nuxt.config.ts", "npm run dev", 3000),
    ("svelte.config.js", "npm run dev", 5173),
    ("manage.py", "python manage.py runserver 0.0.0.0:8000", 8000),
    ("app.py", "python app.py", 5000),
    ("main.py", "python main.py", 8000),
]


def _detect_dev_command(workspace: str) -> tuple[str, int]:
    """Auto-detect the dev server command and port from project files."""
    for filename, cmd, port in _DEV_COMMANDS:
        if os.path.exists(os.path.join(workspace, filename)):
            return cmd, port
    return "", 0


@router.post("/start")
async def start_preview(req: PreviewStartRequest):
    """
    Start a dev server for live preview.
    Auto-detects the framework if no command is given.
    Returns the URL to load in the iframe.
    """
    workspace = req.workspace or state.workspace_path
    if not workspace or not os.path.isdir(workspace):
        raise HTTPException(status_code=400, detail="Invalid workspace path")

    # Stop any existing preview for this workspace
    _stop_process(workspace)

    # Determine command and port
    command = req.command
    port = req.port

    if not command:
        detected_cmd, detected_port = _detect_dev_command(workspace)
        if not detected_cmd:
            raise HTTPException(
                status_code=400,
                detail="Could not auto-detect dev server. "
                "Provide a command explicitly.",
            )
        command = detected_cmd
        if port == 3000:  # default, use detected
            port = detected_port

    logger.info(
        "[Preview] Starting: %s (port %d) in %s", command, port, workspace
    )

    try:
        is_win = sys.platform == "win32"
        proc = subprocess.Popen(
            command,
            shell=True,
            cwd=workspace,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Use process group so we can kill the whole tree
            creationflags=(
                subprocess.CREATE_NEW_PROCESS_GROUP if is_win else 0
            ),
            preexec_fn=None if is_win else os.setsid,
        )
        _preview_procs[workspace] = proc

        # Give the server a moment to start
        await asyncio.sleep(1.5)

        # Check it didn't immediately crash
        if proc.poll() is not None:
            stderr = proc.stderr.read().decode(errors="replace")[:500]
            raise HTTPException(
                status_code=500,
                detail=f"Dev server exited immediately: {stderr}",
            )

        preview_url = f"http://localhost:{port}"
        return {
            "status": "running",
            "url": preview_url,
            "port": port,
            "command": command,
            "pid": proc.pid,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[Preview] Failed to start: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/stop")
async def stop_preview(req: PreviewStopRequest):
    """Stop the running dev server for a workspace."""
    workspace = req.workspace or state.workspace_path
    if not workspace:
        raise HTTPException(status_code=400, detail="No workspace specified")

    stopped = _stop_process(workspace)
    return {"status": "stopped" if stopped else "not_running"}


@router.get("/status")
async def preview_status():
    """Check which previews are currently running."""
    active = {}
    dead_keys = []
    for ws, proc in _preview_procs.items():
        if proc.poll() is None:
            active[ws] = {"pid": proc.pid, "running": True}
        else:
            dead_keys.append(ws)
    for k in dead_keys:
        del _preview_procs[k]
    return {"previews": active}


def _stop_process(workspace: str) -> bool:
    """Kill the preview process for the given workspace. Returns True if stopped."""
    proc = _preview_procs.pop(workspace, None)
    if proc is None:
        return False
    try:
        if sys.platform == "win32":
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    except Exception:
        with contextlib.suppress(Exception):
            proc.kill()
    return True
