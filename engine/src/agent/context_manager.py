"""
ContextManager: Smart context budgeting for agent LLM calls.

Instead of dumping the full 80k chars into every LLM call, this module
builds context intelligently based on what the current step needs.
"""

from typing import Dict, List, Optional


# Total context budget (chars) — ~20k tokens at ~4 chars/token
MAX_CONTEXT_CHARS = 80_000

# Allocation ratios
PLAN_BUDGET_RATIO = 0.20      # plan + current step description
CODE_BUDGET_RATIO = 0.45      # relevant code for current step
HISTORY_BUDGET_RATIO = 0.20   # action logs from completed steps
ERROR_BUDGET_RATIO = 0.15     # error context (never summarized)


class ContextManager:
    """Builds focused context for each agent step's LLM call."""

    def __init__(self, max_chars: int = MAX_CONTEXT_CHARS):
        self.max_chars = max_chars

    def build_step_context(
        self,
        plan_summary: str,
        current_step_desc: str,
        relevant_code: str,
        action_logs: List[str],
        error_context: str = "",
        project_structure: str = "",
    ) -> str:
        """
        Build context for a single step's LLM call.
        Prioritizes: errors > current step > relevant code > history.
        """
        sections = []

        # 1. Error context — always kept at full fidelity
        error_budget = int(self.max_chars * ERROR_BUDGET_RATIO)
        if error_context:
            sections.append(f"<error_context>\n{error_context[:error_budget]}\n</error_context>")
            # Reduce other budgets proportionally
            remaining = self.max_chars - len(error_context[:error_budget])
        else:
            remaining = self.max_chars

        # 2. Plan + current step
        plan_budget = int(remaining * (PLAN_BUDGET_RATIO / (1 - ERROR_BUDGET_RATIO)))
        plan_section = f"<plan>\n{plan_summary}\n</plan>\n\n<current_step>\n{current_step_desc}\n</current_step>"
        if project_structure:
            plan_section += f"\n\n<project_structure>\n{project_structure[:1500]}\n</project_structure>"
        sections.append(plan_section[:plan_budget])

        # 3. Relevant code for this step
        code_budget = int(remaining * (CODE_BUDGET_RATIO / (1 - ERROR_BUDGET_RATIO)))
        if relevant_code:
            sections.append(f"<relevant_code>\n{relevant_code[:code_budget]}\n</relevant_code>")

        # 4. Action history — summarize old steps, keep recent ones detailed
        history_budget = int(remaining * (HISTORY_BUDGET_RATIO / (1 - ERROR_BUDGET_RATIO)))
        if action_logs:
            history = self._compress_history(action_logs, history_budget)
            sections.append(f"<action_history>\n{history}\n</action_history>")

        return "\n\n".join(sections)

    def _compress_history(self, action_logs: List[str], budget: int) -> str:
        """
        Keep recent logs detailed, summarize older ones.
        Always keep at least the last 2 logs in full.
        """
        if not action_logs:
            return ""

        # Keep last 3 logs in full
        recent = action_logs[-3:]
        older = action_logs[:-3]

        recent_text = "\n".join(recent)
        if len(recent_text) > budget:
            # Even recent logs exceed budget — keep only the last one
            return action_logs[-1][:budget]

        remaining_budget = budget - len(recent_text)

        if older and remaining_budget > 200:
            # Summarize older logs to one line each
            summaries = []
            for log in older:
                # Extract just the action line from "Action taken: X\nObservation: Y"
                first_line = log.strip().split("\n")[0] if log.strip() else ""
                if "Action taken:" in first_line:
                    summaries.append(first_line)
                else:
                    summaries.append(first_line[:100])
            older_text = "[Earlier steps summary]\n" + "\n".join(summaries)
            return older_text[:remaining_budget] + "\n\n" + recent_text

        return recent_text

    def estimate_complexity(self, plan_steps: int) -> str:
        """Estimate task complexity from step count."""
        if plan_steps <= 2:
            return "simple"
        if plan_steps <= 5:
            return "moderate"
        return "complex"
