"""
Agent module: Plan → Execute → Verify coding agent for GitSurf Studio.

Components:
  - planner: LLM-based task planning with structured step generation
  - executor: Step-by-step plan execution with verification and re-planning
  - changeset: File change tracking and rollback support
  - context_manager: Smart context budgeting for LLM calls
"""

from src.agent.planner import AgentPlanner, AgentPlan, PlanStep
from src.agent.executor import AgentExecutor, ExecutionResult
from src.agent.changeset import Changeset, FileChange

__all__ = [
    "AgentPlanner",
    "AgentPlan",
    "PlanStep",
    "AgentExecutor",
    "ExecutionResult",
    "Changeset",
    "FileChange",
]
