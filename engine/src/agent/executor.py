"""
AgentExecutor: Step-by-step plan execution with verification and re-planning.

This is the core engine that replaces execute_action_loop() for agent-mode tasks.
It walks through a plan step by step, dispatches tools, verifies results,
and re-plans on failure.
"""

import json
import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

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
        tools: Dict,
        available_tools: str,
        planner: AgentPlanner,
        workspace_path: str,
        terminal_tool=None,
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
    ) -> ExecutionResult:
        """
        Execute a plan step by step.

        Returns an ExecutionResult with the final answer, changeset, and plan state.
        """
        changeset = Changeset(workspace_path=self.workspace_path, goal=plan.goal)
        action_logs: List[str] = []
        replan_count = 0
        max_replans = 3

        # Stream the plan to the frontend
        print(f"[UI_COMMAND] agent_plan {json.dumps(plan.to_dict())}")

        while True:
            step = plan.get_next_step()
            if step is None:
                break

            if self._cancelled.is_set():
                print("[Agent] Execution cancelled by user.")
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
                observation = self._run_action_loop_step(
                    step, plan, initial_context, project_structure, history, action_logs,
                )
                step.observation = observation
                step.status = "done"
                action_logs.append(f"Step {step.id}: {step.description}\nResult: {observation[:500]}")
                print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': step.id, 'status': 'done'})}")
                continue

            # ── Dispatch the tool call ────────────────────────────────
            observation = self._dispatch_step(step, changeset)
            step.observation = observation

            if observation.startswith("[Error]"):
                step.status = "failed"
                step.error = observation
                print(f"   [Agent] Step {step.id} failed: {observation[:200]}")
                print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': step.id, 'status': 'failed', 'error': observation[:200]})}")

                # Try re-planning
                if replan_count < max_replans:
                    replan_count += 1
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

            action_logs.append(
                f"Step {step.id}: {step.description}\n"
                f"Tool: {step.tool}.{step.method}\n"
                f"Result: {observation[:500]}"
            )

            # ── Auto-verify after file edits ──────────────────────────
            if step.verification:
                verify_result = self._run_verification(step, changeset)
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

        return ExecutionResult(
            answer=answer,
            changeset=changeset,
            plan=plan,
            status=status,
        )

    def _dispatch_step(self, step: PlanStep, changeset: Changeset) -> str:
        """Dispatch a single tool call and track file changes."""
        tool_instance = self.tools.get(step.tool)
        if not tool_instance:
            return f"[Error] Unknown tool: {step.tool}. Available: {', '.join(self.tools.keys())}"

        fn = getattr(tool_instance, step.method, None)
        if not fn:
            return f"[Error] {step.tool} has no method '{step.method}'"

        # Snapshot files before write/replace/delete operations
        if step.method in ("write_file", "replace_in_file") and "rel_path" in step.args:
            import os
            rel_path = step.args["rel_path"]
            abs_path = os.path.abspath(os.path.join(self.workspace_path, rel_path))

            # Check for concurrent edit conflicts
            if changeset.check_conflict(abs_path):
                return (
                    f"[Error] Conflict detected: {rel_path} was modified externally since "
                    f"it was last read. Read the file again before editing."
                )

            changeset.snapshot_before_write(abs_path, rel_path, step_id=step.id)

        try:
            observation = fn(**step.args)
        except Exception as e:
            return f"[Error] {step.tool}.{step.method} raised: {e}"

        # Record what was written
        if step.method in ("write_file", "replace_in_file") and "rel_path" in step.args:
            import os
            rel_path = step.args["rel_path"]
            abs_path = os.path.abspath(os.path.join(self.workspace_path, rel_path))
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    new_content = f.read()
                changeset.record_write(abs_path, new_content)
            except Exception:
                pass

        if step.method == "delete_file" and "rel_path" in step.args:
            import os
            rel_path = step.args["rel_path"]
            abs_path = os.path.abspath(os.path.join(self.workspace_path, rel_path))
            # Original content was already in the snapshot
            for change in changeset.changes:
                if change.path == abs_path:
                    break
            else:
                changeset.record_delete(abs_path, rel_path, "", step_id=step.id)

        return str(observation)

    def _run_verification(self, step: PlanStep, changeset: Changeset) -> Optional[str]:
        """Run post-step verification (lint, test, read-back)."""
        if not step.verification:
            return None

        verification = step.verification.lower()
        results = []

        if "lint" in verification and self.terminal_tool:
            rel_path = step.args.get("rel_path", "")
            if rel_path.endswith(".py"):
                lint_result = self.terminal_tool.run_lint(file_path=rel_path)
                results.append(f"Lint: {lint_result[:300]}")
                if "[Error]" not in lint_result and "error" not in lint_result.lower():
                    print(f"   [Verify] Lint passed for {rel_path}")
                else:
                    print(f"   [Verify] Lint issues in {rel_path}: {lint_result[:100]}")
            elif rel_path.endswith((".js", ".ts", ".svelte")):
                lint_result = self.terminal_tool.run_lint(file_path=rel_path)
                results.append(f"Lint: {lint_result[:300]}")

        if "test" in verification and self.terminal_tool:
            test_result = self.terminal_tool.run_test()
            results.append(f"Tests: {test_result[:300]}")
            if "passed" in test_result.lower() or "ok" in test_result.lower():
                print(f"   [Verify] Tests passed")
            else:
                print(f"   [Verify] Test issues: {test_result[:100]}")

        if "read_back" in verification:
            rel_path = step.args.get("rel_path", "")
            if rel_path:
                file_tool = self.tools.get("FileEditorTool")
                if file_tool:
                    content = file_tool.read_file(rel_path)
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
