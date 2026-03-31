"""
Shared ReAct (Reason + Act) action loop used by all pipelines.
"""

import re
import json
from typing import List, Dict

from src.guardrails import validate_answer, validate_action
from src.pipelines.context import build_context_for_llm


def execute_action_loop(
    question: str,
    initial_context: str,
    llm,
    tools: Dict,
    available_tools: str,
    project_structure: str = "",
    extra_context_prefix: str = "",
    history=None,
    max_iterations: int = 5,
) -> str:
    """
    PRAR (Perceive-Reason-Act-Reflect) agent loop.

    Args:
        tools: Dict mapping tool names to instances, e.g.
               {"FileEditorTool": file_editor, "SearchTool": searcher, ...}
    """
    action_logs: List[str] = []
    answer = None
    # ── Loop detection state ───────────────────────────────────────────
    _exact_calls: Dict[str, int] = {}
    _method_calls: Dict[str, int] = {}
    _file_writes: Dict[str, int] = {}  # track per-file write counts

    for iteration in range(1, max_iterations + 1):
        print(f"   [Iteration {iteration}/{max_iterations}] Thinking...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 100 + iteration, 'status': 'running', 'description': f'Iteration {iteration}: Reasoning...'})}")

        context_for_llm = build_context_for_llm(
            initial_context, action_logs, extra_prefix=extra_context_prefix
        )

        # Reason: LLM decides next action via CoT
        decision = llm.decide_action(
            question,
            context_for_llm,
            project_structure=project_structure,
            history=history,
            available_tools=available_tools,
            current_iteration=iteration,
            max_iterations=max_iterations,
        )

        # Validate action JSON schema (fixes malformed dicts in-place)
        decision, action_warnings = validate_action(decision)
        for w in action_warnings:
            print(f"   [ActionGuard] {w}")

        action_type = decision.get("action")
        thought = decision.get("thought", "No thought provided.")
        print(f"   Thought: {thought}")

        if action_type == "final_answer":
            print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 100 + iteration, 'status': 'done'})}")
            print(f"[UI_COMMAND] agent_action {json.dumps({'iteration': iteration, 'total': max_iterations, 'thought': thought, 'action': 'final_answer', 'status': 'done'})}")
            answer = llm.stream_final_answer(
                question, context_for_llm, history=history
            )
            break

        elif action_type == "tool_call":
            tool_name = decision.get("tool")
            method = decision.get("method")
            kwargs = decision.get("args", {})
            print(f"   Action -> {tool_name}.{method}({kwargs})")

            # Emit structured tool call event (running)
            _action_event = {
                "iteration": iteration,
                "total": max_iterations,
                "thought": thought,
                "action": "tool_call",
                "tool": tool_name,
                "method": method,
                "args": kwargs,
                "status": "running",
            }
            print(f"[UI_COMMAND] agent_action {json.dumps(_action_event)}")

            # ── Loop detection ──────────────────────────────────────────
            _norm_name = (tool_name or "").replace("mcp__context7__", "").replace("mcp__", "").replace("-", "_")
            _exact_key = f"{_norm_name}.{method}:{json.dumps(kwargs, sort_keys=True)}"
            _method_key = f"{_norm_name}.{method}"

            _is_autochain_target = (
                ("resolve" in _norm_name and "library" in _norm_name)
                or ("browser" in _norm_name and "navigate" in _norm_name)
            )

            _multi_target_methods = {
                "read_file", "write_file", "replace_in_file", "delete_file",
                "search", "search_and_chunk", "peek_symbol",
                "list_dir", "list_recursive", "list_files",
            }
            _skip_method_counting = (method or "") in _multi_target_methods or _is_autochain_target

            _exact_calls[_exact_key] = _exact_calls.get(_exact_key, 0) + 1
            if not _skip_method_counting:
                _method_calls[_method_key] = _method_calls.get(_method_key, 0) + 1

            exact_count = _exact_calls.get(_exact_key, 0)
            method_count = _method_calls.get(_method_key, 0)

            _soft_limit_exact = 3
            _soft_limit_method = 5
            _hard_limit_exact = 4
            _hard_limit_method = 7

            is_hard_loop = exact_count >= _hard_limit_exact or method_count >= _hard_limit_method
            is_soft_loop = (not _skip_method_counting) and (
                exact_count >= _soft_limit_exact or method_count >= _soft_limit_method
            )

            if is_hard_loop:
                print(f"   [LoopGuard] Hard block — {_norm_name}.{method} (exact={exact_count}, method={method_count})")
                observation = (
                    f"[LoopGuard] BLOCKED — {_norm_name}.{method} has been called too many times. "
                    f"Forcing final answer with available context."
                )
                action_logs.append(f"\n\n--- ACTION LOG ---\nAction taken: {tool_name}.{method}({kwargs})\nObservation: {observation}")
                context_for_llm = build_context_for_llm(
                    initial_context, action_logs, extra_prefix=extra_context_prefix
                )
                answer = llm.stream_final_answer(question, context_for_llm, history=history)
                break

            if is_soft_loop:
                loop_reason = (
                    f"exact repeat #{exact_count}" if exact_count >= _soft_limit_exact
                    else f"same tool.method called {method_count}x with varied args"
                )
                print(f"   [LoopGuard] Soft block ({loop_reason}): {_norm_name}.{method}")
                observation = (
                    f"[LoopGuard] {_norm_name}.{method} has already been called {max(exact_count, method_count)} time(s) "
                    f"and returned results. The data you need is in the action logs above. "
                    f"STOP calling this tool. Use the data from the previous "
                    f"observation to call a DIFFERENT tool, or "
                    f"provide your final_answer now."
                )
                action_str = f"Action taken: {tool_name}.{method}({kwargs})\nObservation: {observation}"
                action_logs.append(f"\n\n--- ACTION LOG ---\n{action_str}")
                continue

            # ── Per-file write guard (catches rewriting the same file) ──
            if method == "write_file":
                _write_path = kwargs.get("rel_path", kwargs.get("path", ""))
                _file_writes[_write_path] = _file_writes.get(_write_path, 0) + 1
                if _file_writes[_write_path] > 2:
                    print(f"   [LoopGuard] File rewrite block — {_write_path} written {_file_writes[_write_path]}x")
                    observation = (
                        f"[LoopGuard] BLOCKED — you already wrote '{_write_path}' {_file_writes[_write_path] - 1} time(s). "
                        f"The file is done. Move on to the NEXT file in your plan, or use "
                        f"replace_in_file() for targeted edits, or provide your final_answer."
                    )
                    action_str = f"Action taken: {tool_name}.{method}({kwargs})\nObservation: {observation}"
                    action_logs.append(f"\n\n--- ACTION LOG ---\n{action_str}")
                    continue

            # Act: Generic tool dispatch
            tool_instance = tools.get(tool_name)
            if tool_instance:
                fn = getattr(tool_instance, method, None)
                if fn:
                    try:
                        observation = fn(**kwargs)
                    except Exception as e:
                        observation = f"[Error] {tool_name}.{method} raised: {e}"
                else:
                    observation = f"[Error] {tool_name} has no method '{method}'"
            else:
                observation = f"[Error] Unknown tool: {tool_name}. Available: {', '.join(tools.keys())}"

            obs_preview = str(observation)[:200]
            print(f"   Observation: {obs_preview}...")
            print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 100 + iteration, 'status': 'done'})}")

            # Emit structured tool result event (done/error)
            _result_status = "error" if str(observation).startswith("[Error]") else "done"
            print(f"[UI_COMMAND] agent_action {json.dumps({'iteration': iteration, 'total': max_iterations, 'action': 'tool_call', 'tool': tool_name, 'method': method, 'status': _result_status, 'observation': obs_preview})}")

            # ── Auto-chain: resolve-library-id → query-docs ─────────────
            if "resolve" in _norm_name and "library" in _norm_name:
                _id_match = re.search(r'library ID:\s*(\S+)', str(observation))
                if _id_match:
                    _lib_id = _id_match.group(1)
                    _topic = question[:120]
                    print(f"   [AutoChain] Resolved library ID: {_lib_id} → fetching docs for topic: \"{_topic}\"")

                    _docs_tool = None
                    for _candidate_key in [
                        "mcp__context7__query-docs",
                        "query_docs",
                        "query-docs",
                        "mcp__context7__get-library-docs",
                        "get_library_docs",
                        "get-library-docs",
                    ]:
                        _docs_tool = tools.get(_candidate_key)
                        if _docs_tool:
                            break
                    if not _docs_tool:
                        for _k, _v in tools.items():
                            _kn = _k.lower().replace("-", "_")
                            if "context7" in _kn and ("doc" in _kn or "query" in _kn) and "resolve" not in _kn:
                                _docs_tool = _v
                                break

                    if _docs_tool:
                        try:
                            _docs_result = _docs_tool.execute(
                                libraryId=_lib_id, query=_topic
                            )
                        except Exception as _e:
                            _docs_result = f"[Error] query-docs raised: {_e}"

                        _docs_preview = str(_docs_result)[:200]
                        print(f"   [AutoChain] Docs result: {_docs_preview}...")

                        action_str = (
                            f"Action taken: {tool_name}.{method}({kwargs})\n"
                            f"Observation: Resolved library ID: {_lib_id}\n\n"
                            f"[AutoChain] Automatically fetched docs for \"{_topic}\":\n"
                            f"{_docs_result}"
                        )
                        reflection = (
                            "\n--- REFLECT ---\n"
                            "Library docs have been fetched. Use this documentation to "
                            "provide a comprehensive final_answer."
                        )
                        action_logs.append(f"\n\n--- ACTION LOG ---\n{action_str}{reflection}")
                        continue
                    else:
                        _mcp_keys = [k for k in tools if "mcp" in k.lower() or "library" in k.lower() or "context7" in k.lower()]
                        print(f"   [AutoChain] docs tool not found — forcing final answer. MCP-like keys: {_mcp_keys}")
                        action_logs.append(
                            f"\n\n--- ACTION LOG ---\n"
                            f"Action taken: {tool_name}.{method}({kwargs})\n"
                            f"Observation: Resolved library ID: {_lib_id}. "
                            f"Docs-fetching tool is unavailable; answering with gathered context."
                        )
                        context_for_llm = build_context_for_llm(
                            initial_context, action_logs, extra_prefix=extra_context_prefix
                        )
                        answer = llm.stream_final_answer(question, context_for_llm, history=history)
                        break

            # ── Auto-chain: browser_navigate → browser_snapshot ────────
            if "browser" in _norm_name and "navigate" in _norm_name:
                if not str(observation).startswith("[Error]"):
                    print("   [AutoChain] browser_navigate succeeded → auto-calling browser_snapshot")

                    _snapshot_tool = None
                    for _candidate_key in [
                        "mcp__playwright__browser_snapshot",
                        "browser_snapshot",
                    ]:
                        _snapshot_tool = tools.get(_candidate_key)
                        if _snapshot_tool:
                            break

                    if _snapshot_tool:
                        try:
                            _snapshot_result = _snapshot_tool.execute()
                        except Exception as _e:
                            _snapshot_result = f"[Error] browser_snapshot raised: {_e}"

                        _snap_preview = str(_snapshot_result)[:200]
                        print(f"   [AutoChain] Snapshot result: {_snap_preview}...")

                        action_str = (
                            f"Action taken: {tool_name}.{method}({kwargs})\n"
                            f"Observation: {observation}\n\n"
                            f"[AutoChain] Automatically captured page snapshot:\n"
                            f"{_snapshot_result}"
                        )
                        reflection = (
                            "\n--- REFLECT ---\n"
                            "Page navigated and snapshot captured. Analyze the snapshot to determine "
                            "if you need to interact further or can provide a final_answer."
                        )
                        action_logs.append(f"\n\n--- ACTION LOG ---\n{action_str}{reflection}")
                        continue

            # Reflect: record this step as a log entry
            action_str = f"Action taken: {tool_name}.{method}({kwargs})\nObservation: {observation}"
            reflection = (
                "\n--- REFLECT ---\n"
                "Evaluate: Did this action achieve the goal? "
                "If yes, provide the final answer. If not, decide the next action."
            )
            action_logs.append(f"\n\n--- ACTION LOG ---\n{action_str}{reflection}")

        else:
            print(f"   [Warning] Unknown action type: {action_type}")
            answer = "Error: Invalid agent action."
            break

    if answer is None:
        print("   [Warning] Max iterations reached without final_answer — forcing answer synthesis.")
        context_for_llm = build_context_for_llm(
            initial_context, action_logs, extra_prefix=extra_context_prefix
        )
        answer = llm.stream_final_answer(question, context_for_llm, history=history)

    # Validate final answer — redact secrets/PII, append safety notices
    answer, answer_warnings = validate_answer(answer)
    for w in answer_warnings:
        print(f"   [AnswerGuard] {w}")

    return answer
