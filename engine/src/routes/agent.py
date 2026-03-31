"""Agent routes: rollback, accept, cancel, respond, list changesets."""

import os

from fastapi import APIRouter, HTTPException

from src.engine_state import state
from src.models import AgentRespondRequest, AgentRollbackRequest

router = APIRouter(prefix="/agent")


@router.post("/rollback")
async def agent_rollback(req: AgentRollbackRequest):
    """Rollback agent changes — all files or a single file."""
    from src.agent.changeset import Changeset

    changeset_data = state.active_changesets.get(req.changeset_id)
    if not changeset_data:
        raise HTTPException(status_code=404, detail="Changeset not found")

    changeset = changeset_data if isinstance(changeset_data, Changeset) else None
    if not changeset:
        raise HTTPException(
            status_code=400,
            detail="Changeset data is summary-only; full rollback requires active changeset",
        )

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


@router.post("/accept")
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


@router.post("/cancel")
async def agent_cancel():
    """Cancel the currently running agent task."""
    executor = state.active_executor
    if executor:
        executor.cancel()
        return {"status": "cancelling"}
    return {"status": "no_active_task"}


@router.post("/respond")
async def agent_respond(req: AgentRespondRequest):
    """Send a user response to a paused agent (human-in-the-loop)."""
    executor = state.active_executor
    if executor:
        executor.provide_user_response(req.response)
        return {"status": "response_sent"}
    return {"status": "no_active_task"}


@router.get("/changesets")
async def list_changesets():
    """List all active changesets."""
    summaries = []
    for cid, data in state.active_changesets.items():
        if isinstance(data, dict):
            summaries.append(data)
        else:
            summaries.append(data.to_dict())
    return {"changesets": summaries}
