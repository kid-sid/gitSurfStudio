"""Chat routes: sessions CRUD, streaming chat, autocomplete, code completion."""

import asyncio
import json
import os
import queue
from contextlib import redirect_stdout
from typing import Any, Callable, Optional, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.engine_state import state
from src.memory.supabase_memory import SupabaseMemory
from src.models import (
    AutocompleteRequest,
    AutocompleteResponse,
    ChatRequest,
    CompleteRequest,
    NewSessionRequest,
)
from src.orchestrator import PipelineContext, run_agent_pipeline, run_local_pipeline
from src.routes import _ensure_initialized, limiter, logger
from src.security import TopicGuard
from src.security.supabase_logger import log_security_event
from src.tool_registry import AVAILABLE_TOOLS

router = APIRouter()


# ── Chat Session Endpoints ───────────────────────────────────────────────────

@router.post("/chat/sessions")
async def create_chat_session(req: NewSessionRequest):
    """Create a new chat session for this user+repo."""
    if not state.chat_memory:
        return {"session_id": None, "error": "ChatMemory not initialized"}
    session_id = state.chat_memory.create_session(req.user_id, req.repo_identifier, req.title)
    return {"session_id": session_id}


@router.get("/chat/sessions")
async def list_chat_sessions(user_id: str, repo_identifier: str):
    """List recent sessions for a user+repo (newest first)."""
    if not state.chat_memory:
        return {"sessions": []}
    sessions = state.chat_memory.list_sessions(user_id, repo_identifier)
    return {"sessions": sessions}


@router.get("/chat/sessions/{session_id}/messages")
async def get_session_messages(session_id: str):
    """Load messages for a session (for frontend display)."""
    if not state.chat_memory:
        return {"messages": []}
    messages = state.chat_memory.load_messages_for_display(session_id)
    return {"messages": messages}


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session(session_id: str):
    """Delete a chat session and all its messages."""
    if not state.chat_memory:
        return {"status": "ok"}
    ok = state.chat_memory.delete_session(session_id)
    return {"status": "ok" if ok else "error"}


# ── QueueWriter ──────────────────────────────────────────────────────────────

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


# ── Main Chat Endpoint ───────────────────────────────────────────────────────

@router.post("/chat")
@limiter.limit("10/minute")
async def chat(request: Request, req: ChatRequest):
    # ── Prompt injection guard ────────────────────────────────
    guard_result = state.prompt_guard.scan(req.query)
    if guard_result.should_log:
        ip = request.client.host if request.client else None
        log_security_event(
            query=req.query,
            result=guard_result,
            user_id=req.user_id,
            ip_address=ip,
            blocked=not guard_result.is_safe,
        )
    if not guard_result.is_safe:
        raise HTTPException(
            status_code=400,
            detail="Query blocked by security policy. This attempt has been logged.",
        )
    # ─────────────────────────────────────────────────────────
    _ensure_initialized(req.path)

    # ── Topic / content policy ────────────────────────────────
    if state.topic_guard is None:
        state.topic_guard = TopicGuard(llm=state.llm)

    topic_result = state.topic_guard.classify(req.query)
    if not topic_result.allowed:
        logger.info(
            "[TopicGuard] Rejected query (reason=%s tier=%s): %.80s",
            topic_result.reason, topic_result.tier, req.query,
        )
        raise HTTPException(status_code=400, detail=topic_result.refusal_message)
    # ─────────────────────────────────────────────────────────

    async def stream_response():
        log_queue: queue.Queue = queue.Queue()
        result_holder: dict = {}

        llm = state.llm
        project_context = state.project_context
        agent_tools = state.agent_tools
        pipeline_ctx = state.pipeline_ctx
        history = state.history
        chat_memory = state.chat_memory

        # ── Persistent chat session ─────────────────────────────────────────
        session_id: Optional[str] = None
        effective_history = req.history

        if req.user_id and chat_memory:
            repo_id = SupabaseMemory.make_repo_identifier(req.path, is_github=False)
            session_id = chat_memory.get_or_create_session(req.user_id, repo_id)
            if session_id:
                persistent_ctx = chat_memory.get_context_for_llm(session_id)
                if persistent_ctx:
                    effective_history = persistent_ctx
        # ───────────────────────────────────────────────────────────────────

        pending_save = state._pending_memory_save
        supabase_mem = state.supabase_memory

        def run_pipeline():
            writer = QueueWriter(log_queue)
            try:
                with redirect_stdout(writer):
                    if req.agent_mode:
                        answer, changeset_dict = run_agent_pipeline(
                            question=req.query,
                            search_path=req.path,
                            llm=llm,
                            project_context=project_context,
                            available_tools=state.available_tools or AVAILABLE_TOOLS,
                            tools=agent_tools,
                            history=effective_history,
                            ctx=pipeline_ctx or PipelineContext(req.path),
                            terminal_tool=state.terminal_tool,
                        )
                        context = ""
                        if changeset_dict and changeset_dict.get("id"):
                            state.active_changesets[changeset_dict["id"]] = changeset_dict
                    else:
                        answer, context = run_local_pipeline(
                            question=req.query,
                            search_path=req.path,
                            llm=llm,
                            project_context=project_context,
                            available_tools=state.available_tools or AVAILABLE_TOOLS,
                            tools=agent_tools,
                            history=effective_history,
                            ctx=pipeline_ctx or PipelineContext(req.path),
                        )
                    writer.flush()
                    result_holder["answer"] = answer
                    result_holder["context"] = context

                    # ── Save symbol graph to Supabase after first build ────────
                    if pending_save and pipeline_ctx and pipeline_ctx._sym_extractor:
                        symbols = pipeline_ctx._sym_extractor.symbols
                        call_graph_data = None
                        if pipeline_ctx._call_graph:
                            try:
                                cg = pipeline_ctx._call_graph
                                call_graph_data = {
                                    "callees": {k: list(v) for k, v in cg.callees.items()},
                                    "callers": {k: list(v) for k, v in cg.callers.items()},
                                    "node_info": cg.node_info,
                                }
                            except Exception:
                                pass
                        if symbols:
                            supabase_mem.schedule_save(
                                user_id=pending_save["user_id"],
                                repo_identifier=pending_save["repo_id"],
                                repo_display=pending_save["repo_display"],
                                commit_sha=pending_save["commit_sha"],
                                symbols=symbols,
                                call_graph=call_graph_data,
                            )
                            state._pending_memory_save = None
                    # ──────────────────────────────────────────────────────────

            except Exception as e:
                import traceback
                traceback.print_exc()
                result_holder["answer"] = f"Pipeline error: {e}"
                result_holder["context"] = ""
            finally:
                log_queue.put(None)  # Sentinel: ALWAYS signal completion

        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, cast(Callable[..., Any], run_pipeline))

        while True:
            await asyncio.sleep(0.02)
            while not log_queue.empty():
                line = log_queue.get_nowait()
                if line is None:
                    answer = result_holder.get("answer", "")
                    if not result_holder.get("answer_streamed"):
                        yield json.dumps({"type": "answer", "content": answer}) + "\n"
                    if history:
                        history.add_interaction(str(req.query), str(answer))
                    if session_id and chat_memory:
                        chat_memory.add_message(session_id, "user", str(req.query))
                        chat_memory.add_message(session_id, "assistant", str(answer))
                    return

                if line.startswith("[ANSWER_TOKEN]"):
                    try:
                        token = json.loads(line[len("[ANSWER_TOKEN]"):])
                    except Exception:
                        token = line[len("[ANSWER_TOKEN]"):]
                    result_holder["answer_streamed"] = True
                    yield json.dumps({"type": "answer_token", "content": token}) + "\n"
                elif line.startswith("[UI_COMMAND] "):
                    parts = line.replace("[UI_COMMAND] ", "").split(" ", 1)
                    cmd = parts[0]
                    args = parts[1] if len(parts) > 1 else ""
                    yield json.dumps({"type": "ui_command", "command": cmd, "args": args}) + "\n"
                else:
                    yield json.dumps({"type": "log", "content": line}) + "\n"

    return StreamingResponse(stream_response(), media_type="application/x-ndjson")


# ── Autocomplete / Completion ─────────────────────────────────────────────────

@router.post("/autocomplete", response_model=AutocompleteResponse)
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
            model=llm.fast_model,
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


@router.post("/complete")
async def complete_code(req: CompleteRequest):
    """Fast inline code completion using the LLM fast model."""
    if not state.llm:
        raise HTTPException(
            status_code=400,
            detail="Engine not initialized — open a workspace first",
        )

    filename = os.path.basename(req.path.replace("\\", "/").replace("file:///", ""))
    prompt = (
        f"You are a code completion engine for {req.language}. "
        f"File: {filename}\n\n"
        f"Code before cursor:\n{req.prefix[-900:]}\n"
        f"Code after cursor:\n{req.suffix[:200]}\n\n"
        "Complete the code at the cursor position. "
        "Return ONLY the inserted text (1–4 lines max). No explanation, no markdown fences."
    )

    try:
        completion = state.llm._call(
            messages=[{"role": "user", "content": prompt}],
            model=state.llm.fast_model,
            temperature=0.1,
            max_tokens=120,
        )
        return {"completion": completion.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
