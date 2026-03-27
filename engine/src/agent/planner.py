"""
AgentPlanner: LLM-based task planning for the coding agent.

Generates structured plans from user requests, with steps, dependencies,
and verification criteria. Supports re-planning on failure.
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.prompts import plan_task_prompt, replan_on_failure_prompt


@dataclass
class PlanStep:
    """A single step in an agent plan."""
    id: int
    description: str
    tool: str
    method: str
    args: Dict = field(default_factory=dict)
    depends_on: List[int] = field(default_factory=list)
    verification: Optional[str] = None  # e.g. "run_lint", "run_test", "read_back"
    status: str = "pending"  # pending | running | done | failed | skipped
    observation: str = ""
    error: str = ""

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "description": self.description,
            "tool": self.tool,
            "method": self.method,
            "args": self.args,
            "depends_on": self.depends_on,
            "verification": self.verification,
            "status": self.status,
        }

    def to_full_dict(self) -> Dict:
        """Full serialization including observation and error (for persistence)."""
        d = self.to_dict()
        d["observation"] = self.observation
        d["error"] = self.error
        return d


@dataclass
class AgentPlan:
    """A structured plan for the coding agent to execute."""
    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    complexity: str = "simple"  # simple | moderate | complex
    estimated_files: int = 0

    @property
    def max_iterations(self) -> int:
        """Dynamic iteration limit based on plan complexity."""
        n = len(self.steps)
        if n <= 2:
            return 8
        if n <= 5:
            return 15
        return 25

    @property
    def total_steps(self) -> int:
        return len(self.steps)

    @property
    def completed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "done")

    @property
    def failed_steps(self) -> int:
        return sum(1 for s in self.steps if s.status == "failed")

    def get_next_step(self) -> Optional[PlanStep]:
        """Get the next step whose dependencies are all satisfied."""
        done_ids = {s.id for s in self.steps if s.status in ("done", "skipped")}
        for step in self.steps:
            if step.status != "pending":
                continue
            if all(dep_id in done_ids for dep_id in step.depends_on):
                return step
        return None

    def to_dict(self) -> Dict:
        return {
            "goal": self.goal,
            "complexity": self.complexity,
            "estimated_files": self.estimated_files,
            "steps": [s.to_dict() for s in self.steps],
        }

    def to_full_dict(self) -> Dict:
        """Full serialization including step observations and errors (for persistence)."""
        return {
            "goal": self.goal,
            "complexity": self.complexity,
            "estimated_files": self.estimated_files,
            "steps": [s.to_full_dict() for s in self.steps],
        }

    def summary(self) -> str:
        """Human-readable plan summary for streaming to frontend."""
        lines = [f"**Plan:** {self.goal}"]
        lines.append(f"**Complexity:** {self.complexity} ({len(self.steps)} steps)")
        for step in self.steps:
            status_icon = {
                "pending": "○",
                "running": "●",
                "done": "✓",
                "failed": "✗",
                "skipped": "-",
            }.get(step.status, "?")
            deps = f" (after step {step.depends_on})" if step.depends_on else ""
            lines.append(f"  {status_icon} Step {step.id}: {step.description}{deps}")
        return "\n".join(lines)


def _extract_json(text: str) -> Optional[Dict]:
    """Extract first JSON object from LLM response."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


class AgentPlanner:
    """Generates and revises structured plans using the LLM."""

    def __init__(self, llm):
        self.llm = llm

    def create_plan(
        self,
        user_request: str,
        project_context: str,
        file_structure: str,
        available_tools: str,
        history: Optional[List[Dict]] = None,
    ) -> AgentPlan:
        """Generate a structured plan from the user's request."""
        history_str = ""
        if history:
            history_str = "\n".join(
                f"{m['role'].upper()}: {m['content']}" for m in history[-5:]
            )

        prompt = plan_task_prompt(
            user_request=user_request,
            project_context=project_context,
            file_structure=file_structure[:3000],
            available_tools=available_tools,
            history_str=history_str,
        )

        try:
            content = self.llm._call(
                [
                    {
                        "role": "system",
                        "content": (
                            "You are a coding agent planner. Return ONLY a valid JSON object. "
                            "No markdown fences. No explanation."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                self.llm.reasoning_model,
                temperature=0.1,
            )
            data = _extract_json(content)
            if data:
                return self._parse_plan(data)
        except Exception as e:
            print(f"[AgentPlanner] Error generating plan: {e}")

        # Fallback: single-step plan that uses the existing action loop
        return AgentPlan(
            goal=user_request,
            steps=[
                PlanStep(
                    id=1,
                    description="Execute the user's request using available tools",
                    tool="__action_loop__",
                    method="execute",
                    args={"question": user_request},
                )
            ],
            complexity="simple",
        )

    def replan(
        self,
        original_plan: AgentPlan,
        failed_step: PlanStep,
        error_context: str,
        accumulated_context: str,
    ) -> AgentPlan:
        """Re-plan remaining steps after a failure."""
        completed = [s for s in original_plan.steps if s.status == "done"]
        remaining = [s for s in original_plan.steps if s.status == "pending"]

        completed_summary = "\n".join(
            f"  ✓ Step {s.id}: {s.description} → {s.observation[:200]}"
            for s in completed
        )

        prompt = replan_on_failure_prompt(
            goal=original_plan.goal,
            completed_summary=completed_summary,
            failed_step=f"Step {failed_step.id}: {failed_step.description}",
            error=error_context,
            remaining_steps="\n".join(f"  Step {s.id}: {s.description}" for s in remaining),
            context=accumulated_context[:8000],
        )

        try:
            content = self.llm._call(
                [
                    {
                        "role": "system",
                        "content": "You are a coding agent planner. Return ONLY valid JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                self.llm.reasoning_model,
                temperature=0.1,
            )
            data = _extract_json(content)
            if data:
                new_plan = self._parse_plan(data)
                # Preserve completed steps
                new_plan.steps = completed + new_plan.steps
                # Re-number steps
                for i, step in enumerate(new_plan.steps):
                    step.id = i + 1
                return new_plan
        except Exception as e:
            print(f"[AgentPlanner] Error re-planning: {e}")

        # Fallback: skip the failed step and continue with remaining
        failed_step.status = "skipped"
        return original_plan

    # Tools that actually exist in the agent's tool registry
    _VALID_TOOLS = {
        "FileEditorTool", "GitTool", "EditorUITool", "SearchTool",
        "WebSearchTool", "SymbolPeekerTool", "BrowserTool", "TerminalTool",
        "__action_loop__",
    }

    # Methods that are no-ops (checking if dir exists, navigating, etc.) —
    # these never make progress and should be dropped from plans.
    _NOOP_METHODS = {
        "change_directory", "cd", "navigate", "check_exists",
        "list_directory", "ls", "pwd", "get_cwd",
    }

    # Known hallucinated tool names → replacement tool
    _TOOL_ALIASES = {
        "FileSystemTool": "TerminalTool",
        "FileTool": "FileEditorTool",
        "NodeTool": "TerminalTool",
        "PipTool": "TerminalTool",
        "NpmTool": "TerminalTool",
        "ShellTool": "TerminalTool",
        "BashTool": "TerminalTool",
        "CommandTool": "TerminalTool",
        "DockerTool": "TerminalTool",
        "PackageTool": "TerminalTool",
        "DirectoryTool": "TerminalTool",
        "CodeSearchTool": "SearchTool",
        "GrepTool": "SearchTool",
        "ReadFileTool": "FileEditorTool",
        "WriteFileTool": "FileEditorTool",
    }

    def _sanitize_step(self, step_data: Dict, index: int) -> Optional[Dict]:
        """
        Validate and fix a single step dict from LLM output.
        Returns None if the step should be dropped entirely.
        """
        tool = step_data.get("tool", "FileEditorTool")
        method = step_data.get("method", "read_file")

        # Remap hallucinated tool names to real ones
        if tool not in self._VALID_TOOLS:
            remapped = self._TOOL_ALIASES.get(tool)
            if remapped:
                print(f"   [Planner] Remapped {tool} → {remapped}")
                tool = remapped
                step_data = dict(step_data, tool=tool)
            else:
                # Unknown tool with no alias → delegate to action loop which handles it
                print(f"   [Planner] Unknown tool '{tool}', delegating to __action_loop__")
                return {
                    "description": step_data.get("description", f"Step {index}"),
                    "tool": "__action_loop__",
                    "method": "execute",
                    "args": {"question": step_data.get("description", f"Step {index}: {tool}.{method}")},
                    "depends_on": step_data.get("depends_on", []),
                    "verification": step_data.get("verification"),
                }

        # Drop pure no-op navigation / existence-check steps
        if method in self._NOOP_METHODS:
            print(f"   [Planner] Dropped no-op step: {tool}.{method}")
            return None

        # Fix FileSystemTool.change_directory specifically
        if tool == "TerminalTool" and method in self._NOOP_METHODS:
            print(f"   [Planner] Dropped no-op terminal step: {method}")
            return None

        return step_data

    def _parse_plan(self, data: Dict) -> AgentPlan:
        """Parse LLM JSON output into an AgentPlan, sanitizing invalid steps."""
        steps = []
        for i, step_data in enumerate(data.get("steps", []), start=1):
            sanitized = self._sanitize_step(step_data, i)
            if sanitized is None:
                continue  # Drop no-op steps
            steps.append(PlanStep(
                id=len(steps) + 1,  # re-number after drops
                description=sanitized.get("description", f"Step {i}"),
                tool=sanitized.get("tool", "FileEditorTool"),
                method=sanitized.get("method", "read_file"),
                args=sanitized.get("args", {}),
                depends_on=sanitized.get("depends_on", []),
                verification=sanitized.get("verification"),
            ))

        return AgentPlan(
            goal=data.get("goal", "Execute task"),
            steps=steps,
            complexity=data.get("complexity", "moderate"),
            estimated_files=data.get("estimated_files", len(steps)),
        )
