"""
AgentExecutor: Step-by-step plan execution with verification and re-planning.

This is the core engine that replaces execute_action_loop() for agent-mode tasks.
It walks through a plan step by step, dispatches tools, verifies results,
and re-plans on failure.
"""

import json
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from src.agent.planner import AgentPlan, AgentPlanner, PlanStep
from src.agent.changeset import Changeset
from src.agent.context_manager import ContextManager
from src.prompts import verify_step_prompt, execute_step_prompt
from src.guardrails import validate_answer


@dataclass
class ExecutionResult:
    """Result of an agent task execution."""
    answer: str
    changeset: Changeset
    plan: AgentPlan
    status: str = "completed"  # completed | failed | cancelled | partial


class AgentExecutor:
    """
    Executes an AgentPlan step by step.

    For each step:
      1. Dispatch the tool call
      2. Run verification (lint/test/read-back) if specified
      3. On failure, ask the planner to re-plan remaining steps
      4. Stream progress events to the frontend

    Supports:
      - Human-in-the-loop (pauses for user confirmation)
      - Cancel/abort mid-execution
      - Changeset tracking for rollback
      - Auto-verify (lint after edits, test after code changes)
    """

    def __init__(
        self,
        llm,
        tools: Dict[str, Any],
        available_tools: str,
        planner: AgentPlanner,
        workspace_path: str,
        terminal_tool: Any = None,
    ):
        self.llm = llm
        self.tools = tools
        self.available_tools = available_tools
        self.planner = planner
        self.workspace_path = workspace_path
        self.terminal_tool = terminal_tool
        self.context_manager = ContextManager()

        # Cancellation support
        self._cancelled = threading.Event()

        # Human-in-the-loop support
        self._user_response_event = threading.Event()
        self._user_response: Optional[str] = None

    def cancel(self):
        """Signal the executor to stop after the current step."""
        self._cancelled.set()

    def provide_user_response(self, response: str):
        """Resume execution with the user's response."""
        self._user_response = response
        self._user_response_event.set()

    def execute(
        self,
        plan: AgentPlan,
        initial_context: str = "",
        project_structure: str = "",
        history: Optional[List[Dict]] = None,
        session_memory=None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
    ) -> ExecutionResult:
        """
        Execute a plan step by step.

        Supports resuming from previous checkpoint: if steps are already marked 'done'
        in session_memory, they are skipped and only pending steps are executed.

        Returns an ExecutionResult with the final answer, changeset, and plan state.
        """
        changeset = Changeset(workspace_path=self.workspace_path, goal=plan.goal)
        action_logs: List[str] = []
        replan_count: int = 0
        max_replans = 3

        # ── Resume from checkpoint if applicable ────────────────────────────
        if session_memory and session_id and task_id:
            exec_state = session_memory.get_execution_state(session_id, task_id)
            if exec_state:
                # Mark completed steps as done in the plan
                for log_entry in exec_state.get("execution_log", []):
                    for step in plan.steps:
                        if step.id == log_entry["step_id"]:
                            step.status = log_entry["status"]
                            step.observation = log_entry.get("observation", "")
                            step.error = log_entry.get("error", "")

                # Rebuild action logs from execution log
                for log_entry in exec_state.get("execution_log", []):
                    if log_entry["status"] == "done":
                        action_logs.append(
                            f"Step {log_entry['step_id']}: [resumed from checkpoint]\n"
                            f"Result: {log_entry.get('observation', '')[:500]}"
                        )

                # Restore changeset if available
                if exec_state.get("changeset"):
                    from src.agent.changeset import FileChange
                    cs_data = exec_state.get("changeset", {})
                    changeset.id = cs_data.get("id", changeset.id)
                    changeset.goal = cs_data.get("goal", changeset.goal)
                    changeset.status = cs_data.get("status", "active")
                    # Restore file changes
                    if "files" in cs_data:
                        for f in cs_data["files"]:
                            changeset.changes.append(
                                FileChange(
                                    path=f.get("path", ""),
                                    rel_path=f.get("rel_path", ""),
                                    action=f.get("action", ""),
                                    original_content=f.get("original_content"),
                                    new_content=f.get("new_content"),
                                    original_hash=f.get("original_hash"),
                                    step_id=f.get("step_id"),
                                )
                            )

                print("[Agent] Resuming from checkpoint")

        # Stream the plan to the frontend
        print(f"[UI_COMMAND] agent_plan {json.dumps(plan.to_dict())}")

        while True:
            step = plan.get_next_step()
            if step is None:
                break

            if self._cancelled.is_set():
                print("[Agent] Execution cancelled by user.")
                if session_memory and session_id and task_id:
                    session_memory.update_changeset(session_id, task_id, changeset.to_dict())
                    session_memory.finalize_execution(
                        session_id, task_id,
                        "Agent execution was cancelled. Partial changes may have been made.",
                        "cancelled"
                    )
                return ExecutionResult(
                    answer="Agent execution was cancelled. Partial changes may have been made.",
                    changeset=changeset,
                    plan=plan,
                    status="cancelled",
                )

            step.status = "running"
            print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': step.id, 'status': 'running', 'description': step.description})}")
            print(f"   [Agent Step {step.id}/{plan.total_steps}] {step.description}")

            # ── Special case: fallback to action loop ─────────────────
            if step.tool == "__action_loop__":
                observation = str(self._run_action_loop_step(
                    step, plan, initial_context, project_structure, history, action_logs,
                ))
                step.observation = observation
                step.status = "done"
                obs_preview = observation[:500]
                action_logs.append(f"Step {step.id}: {step.description}\nResult: {obs_preview}")
                print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': step.id, 'status': 'done'})}")

                # Log to session memory
                if session_memory and session_id and task_id:
                    session_memory.log_step_complete(
                        session_id, task_id, step.id, "done", obs_preview
                    )
                continue

            # ── Dispatch the tool call ────────────────────────────────
            observation = str(self._dispatch_step(step, changeset))
            step.observation = observation

            if observation.startswith("[Error]"):
                step.status = "failed"
                step.error = observation
                obs_limit = observation[:200]
                print(f"   [Agent] Step {step.id} failed: {obs_limit}")
                print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': step.id, 'status': 'failed', 'error': obs_limit})}")

                # Log to session memory
                if session_memory and session_id and task_id:
                    session_memory.log_step_complete(
                        session_id, task_id, step.id, "failed", "", obs_limit
                    )

                # Try re-planning
                if replan_count < max_replans:
                    replan_count = replan_count + 1
                    print(f"   [Agent] Re-planning (attempt {replan_count}/{max_replans})...")
                    plan = self.planner.replan(
                        original_plan=plan,
                        failed_step=step,
                        error_context=observation,
                        accumulated_context="\n".join(action_logs[-5:]),
                    )
                    print(f"[UI_COMMAND] agent_plan {json.dumps(plan.to_dict())}")
                    continue
                else:
                    print(f"   [Agent] Max re-plans reached. Stopping.")
                    break
            else:
                step.status = "done"
                print(f"   [Agent] Step {step.id} completed.")
                print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': step.id, 'status': 'done'})}")

                # Log to session memory
                if session_memory and session_id and task_id:
                    session_memory.log_step_complete(
                        session_id, task_id, step.id, "done", observation[:500]
                    )

            action_logs.append(
                f"Step {step.id}: {step.description}\n"
                f"Tool: {step.tool}.{step.method}\n"
                f"Result: {observation[:500]}"
            )

            # ── Auto-verify after file edits ──────────────────────────
            if step.verification:
                verify_result = str(self._run_verification(step, changeset))
                if verify_result:
                    action_logs.append(f"Verification for step {step.id}: {verify_result[:300]}")

        # ── Generate final answer ─────────────────────────────────────
        context_for_answer = self.context_manager.build_step_context(
            plan_summary=plan.summary(),
            current_step_desc="All steps completed. Generate final answer.",
            relevant_code="",
            action_logs=action_logs,
            project_structure=project_structure,
        )

        answer = self.llm.stream_final_answer(
            plan.goal,
            context_for_answer,
            history=history,
        )

        # Validate answer
        answer, warnings = validate_answer(answer)
        for w in warnings:
            print(f"   [AnswerGuard] {w}")

        # Stream changeset summary
        if changeset.changes:
            print(f"[UI_COMMAND] agent_changeset {json.dumps(changeset.to_dict())}")

        status = "completed"
        if plan.failed_steps > 0:
            status = "partial" if plan.completed_steps > 0 else "failed"

        # ── Final persistence ─────────────────────────────────────────────
        if session_memory and session_id and task_id:
            session_memory.update_changeset(session_id, task_id, changeset.to_dict())
            session_memory.finalize_execution(session_id, task_id, answer, status)

        return ExecutionResult(
            answer=answer,
            changeset=changeset,
            plan=plan,
            status=status,
        )

    def _dispatch_step(self, step: PlanStep, changeset: Changeset) -> str:
        """Dispatch a single tool call and track file changes."""
        tool_instance = self.tools.get(step.tool)
        method_name = step.method

        # Compatibility: some plans may emit "ToolName.method" in the tool field.
        # If so, split and try again, optionally using the suffix as method when absent.
        if not tool_instance and "." in step.tool:
            base, suffix = step.tool.split(".", 1)
            tool_instance = self.tools.get(base)
            if tool_instance and (not method_name or method_name == step.tool):
                method_name = suffix

        if not tool_instance:
            available = ', '.join(self.tools.keys())
            suggestion = ""
            if "FileSystem" in step.tool or step.tool == "FileSystemTool":
                suggestion = "\n→ Did you mean: SearchTool (for reading files) or TerminalTool (for directory ops)?"
            elif "change_directory" in step.method or "cd" in step.method:
                suggestion = "\n→ Use TerminalTool with cwd parameter instead, or SearchTool for file discovery"
            return f"[Error] Unknown tool: {step.tool}. Available: {available}{suggestion}"

        fn = getattr(tool_instance, method_name, None)
        if not fn:
            return f"[Error] {step.tool} has no method '{method_name}'"

        # Snapshot files before write/replace/delete operations
        if method_name in ("write_file", "replace_in_file") and "path" in step.args:
            import os
            path = step.args["path"]
            abs_path = os.path.abspath(os.path.join(self.workspace_path, path))

            # Check for concurrent edit conflicts
            if changeset.check_conflict(abs_path):
                return (
                    f"[Error] Conflict detected: {path} was modified externally since "
                    f"it was last read. Read the file again before editing."
                )

            changeset.snapshot_before_write(abs_path, path, step_id=step.id)

        try:
            observation = fn(**step.args)
        except Exception as e:
            return f"[Error] {step.tool}.{method_name} raised: {e}"

        # Record what was written
        if method_name in ("write_file", "replace_in_file") and "path" in step.args:
            import os
            path = step.args["path"]
            abs_path = os.path.abspath(os.path.join(self.workspace_path, path))
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    new_content = f.read()
                changeset.record_write(abs_path, new_content)
            except Exception:
                pass

        if method_name == "delete_file" and "path" in step.args:
            import os
            path = step.args["path"]
            abs_path = os.path.abspath(os.path.join(self.workspace_path, path))
            # Original content was already in the snapshot
            for change in changeset.changes:
                if change.path == abs_path:
                    break
            else:
                changeset.record_delete(abs_path, path, "", step_id=step.id)

        return str(observation)

    def _run_verification(self, step: PlanStep, changeset: Changeset) -> Optional[str]:
        """Run post-step verification (lint, test, read-back)."""
        if not step.verification:
            return None

        verification = step.verification.lower()
        results = []

        if "lint" in verification and self.terminal_tool:
            path = step.args.get("path", "")
            if path.endswith(".py"):
                lint_result = self.terminal_tool.run_lint(file_path=path)
                results.append(f"Lint: {lint_result[:300]}")
                if "[Error]" not in lint_result and "error" not in lint_result.lower():
                    print(f"   [Verify] Lint passed for {path}")
                else:
                    print(f"   [Verify] Lint issues in {path}: {lint_result[:100]}")
            elif path.endswith((".js", ".ts", ".svelte")):
                lint_result = self.terminal_tool.run_lint(file_path=path)
                results.append(f"Lint: {lint_result[:300]}")

        if "test" in verification and self.terminal_tool:
            test_result = self.terminal_tool.run_test()
            results.append(f"Tests: {test_result[:300]}")
            if "passed" in test_result.lower() or "ok" in test_result.lower():
                print(f"   [Verify] Tests passed")
            else:
                print(f"   [Verify] Test issues: {test_result[:100]}")

        if "read_back" in verification:
            path = step.args.get("path", "")
            if path:
                file_tool = self.tools.get("FileEditorTool")
                if file_tool:
                    content = file_tool.read_file(path)
                    if not content.startswith("[Error]"):
                        results.append(f"Read-back: File has {len(content.splitlines())} lines")

        return "\n".join(results) if results else None

    def _run_action_loop_step(
        self,
        step: PlanStep,
        plan: AgentPlan,
        initial_context: str,
        project_structure: str,
        history: Optional[List[Dict]],
        action_logs: List[str],
    ) -> str:
        """Fallback: run the existing ReAct action loop for complex/unstructured steps."""
        from src.orchestrator import execute_action_loop

        question = step.args.get("question", plan.goal)
        return execute_action_loop(
            question=question,
            initial_context=initial_context,
            llm=self.llm,
            tools=self.tools,
            available_tools=self.available_tools,
            project_structure=project_structure,
            history=history,
            max_iterations=plan.max_iterations,
        )

    def _ask_user(self, question: str, options: Optional[List[str]] = None) -> str:
        """
        Pause execution and ask the user a question.
        Blocks until provide_user_response() is called.
        """
        payload = {"question": question}
        if options:
            payload["options"] = options
        print(f"[UI_COMMAND] agent_ask {json.dumps(payload)}")

        self._user_response_event.clear()
        self._user_response = None

        # Block until user responds (or cancel)
        while not self._user_response_event.is_set():
            if self._cancelled.is_set():
                return "cancelled"
            self._user_response_event.wait(timeout=1.0)

        return self._user_response or ""
