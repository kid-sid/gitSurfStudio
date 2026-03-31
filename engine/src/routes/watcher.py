"""File watcher route: WebSocket-based filesystem change notifications."""

import asyncio
import contextlib
import json
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from src.engine_state import state
from src.routes import logger

router = APIRouter()

# Directories / extensions to ignore
_IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".cache", "venv",
    ".venv", ".next", "dist", "build", ".svelte-kit",
}
_IGNORE_EXTS = {".pyc", ".pyo", ".swp", ".swo", ".tmp", ".log"}


def _should_ignore(path: str) -> bool:
    """Return True if the path should be filtered out of watch events."""
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part in _IGNORE_DIRS:
            return True
    ext = os.path.splitext(path)[1].lower()
    return ext in _IGNORE_EXTS


@router.websocket("/watch")
async def watch_ws(websocket: WebSocket, path: str | None = None):
    """
    Watches the workspace directory for file changes and sends
    NDJSON events over WebSocket.

    Events:
      {"type": "change", "path": "relative/path.py", "change": "modified"}
      {"type": "change", "path": "relative/path.py", "change": "added"}
      {"type": "change", "path": "relative/path.py", "change": "deleted"}
    """
    await websocket.accept()

    watch_path = path or state.workspace_path
    if not watch_path or not os.path.isdir(watch_path):
        await websocket.send_text(
            json.dumps({"type": "error", "message": "No valid workspace path"})
        )
        await websocket.close()
        return

    watch_path = os.path.realpath(watch_path)
    logger.info("[FileWatcher] Starting watch on %s", watch_path)

    try:
        from watchfiles import Change, awatch

        change_map = {
            Change.added: "added",
            Change.modified: "modified",
            Change.deleted: "deleted",
        }

        # Send initial ready signal
        await websocket.send_text(
            json.dumps({"type": "ready", "path": watch_path})
        )

        # Keep-alive: also listen for client messages (ping/close)
        async def listen_client():
            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass

        client_task = asyncio.create_task(listen_client())

        try:
            async for changes in awatch(
                watch_path,
                watch_filter=lambda change, p: not _should_ignore(p),
                debounce=300,
                step=200,
                stop_event=asyncio.Event(),
            ):
                events = []
                for change_type, abs_path in changes:
                    try:
                        rel = os.path.relpath(abs_path, watch_path)
                    except ValueError:
                        rel = abs_path
                    rel = rel.replace("\\", "/")

                    if _should_ignore(rel):
                        continue

                    events.append({
                        "type": "change",
                        "path": rel,
                        "change": change_map.get(change_type, "modified"),
                    })

                for event in events:
                    try:
                        await websocket.send_text(json.dumps(event))
                    except Exception:
                        return
        finally:
            client_task.cancel()

    except WebSocketDisconnect:
        logger.info("[FileWatcher] Client disconnected")
    except ImportError:
        await websocket.send_text(
            json.dumps({"type": "error", "message": "watchfiles not installed"})
        )
        await websocket.close()
    except Exception as e:
        logger.error("[FileWatcher] Error: %s", e)
        with contextlib.suppress(Exception):
            await websocket.send_text(
                json.dumps({"type": "error", "message": str(e)})
            )
