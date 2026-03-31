"""
GitSurf Studio — AI Engine Server

Thin assembly module: creates the FastAPI app, wires middleware,
and includes all route modules.
"""

import asyncio
import os
import platform
import sys

# ── Windows Subprocess Support ──────────────────────────────────────────
if platform.system() == "Windows":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass
# ───────────────────────────────────────────────────────────────────────

from dotenv import load_dotenv

if not load_dotenv():
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

# Ensure engine modules are importable
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.engine_state import state
from src.routes import limiter
from src.routes.agent import router as agent_router
from src.routes.auth import router as auth_router
from src.routes.cache import router as cache_router
from src.routes.chat import router as chat_router
from src.routes.git import router as git_router
from src.routes.health import router as health_router
from src.routes.lint import router as lint_router
from src.routes.preview import router as preview_router
from src.routes.symbols import router as symbols_router
from src.routes.terminal import router as terminal_router
from src.routes.watcher import router as watcher_router
from src.routes.workspace import router as workspace_router
from src.tool_registry import AVAILABLE_TOOLS

# ── App Assembly ──────────────────────────────────────────────────────────

app = FastAPI(
    title="GitSurf Studio Engine",
    description="AI-powered codebase reasoning engine",
    version="1.0.0",
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Seed the default tool descriptions
state.available_tools = AVAILABLE_TOOLS

# ── Register Routes ──────────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(workspace_router)
app.include_router(chat_router)
app.include_router(git_router)
app.include_router(auth_router)
app.include_router(symbols_router)
app.include_router(lint_router)
app.include_router(agent_router)
app.include_router(terminal_router)
app.include_router(watcher_router)
app.include_router(preview_router)
app.include_router(cache_router)


@app.on_event("shutdown")
async def _shutdown():
    if state.mcp_manager:
        state.mcp_manager.shutdown()
    # Clean stale search indexes on shutdown (repos preserved for fast restart)
    import contextlib
    with contextlib.suppress(Exception):
        state.cache_manager.cleanup_search_indexes()


# ── Entry Point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8002)
