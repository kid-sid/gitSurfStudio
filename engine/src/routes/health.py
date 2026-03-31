"""Health and MCP status routes."""

from fastapi import APIRouter

from src.engine_state import state
from src.models import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health():
    return {"status": "ok", "workspace": state.workspace_path}


@router.get("/mcp/status")
async def mcp_status():
    """Returns MCP readiness and list of available tool names. Poll after /init."""
    tools = []
    if state.mcp_manager:
        tools = [t["name"] for t in state.mcp_manager.list_all_tools()]
    return {"ready": state.mcp_ready, "tools": tools, "count": len(tools)}
