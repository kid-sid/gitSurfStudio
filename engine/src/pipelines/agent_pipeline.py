"""
Agent-mode pipeline: Plan → Execute → Verify with changeset tracking.
"""

from typing import Dict, Optional

from src.agent.planner import AgentPlanner
from src.agent.executor import AgentExecutor
from src.pipelines.context import PipelineContext
from src.pipelines.local_pipeline import build_local_file_tree, retrieve_local_files


def run_agent_pipeline(
    question: str,
    search_path: str,
    llm,
    project_context: str,
    available_tools: str,
    tools: Dict,
    history=None,
    ctx: Optional[PipelineContext] = None,
    terminal_tool=None,
) -> tuple:
    """
    Agent-mode pipeline: Plan → Execute → Verify.

    Unlike the Q&A pipelines, this creates a structured plan first,
    then executes it step-by-step with verification and re-planning.
    Returns (answer: str, changeset_dict: dict).
    """
    print("\n[Agent Pipeline] Plan → Execute → Verify")

    # Step 1: Build file tree for context
    print("[Step 1] Building project context...")
    project_structure = build_local_file_tree(search_path)

    # Step 2: Gather initial context from targeted files
    print("[Step 2] Gathering initial context...")
    refined_data = llm.refine_user_query(
        question, history=history, project_context=project_context,
        file_structure=project_structure,
    )
    target_files = refined_data.get("target_files", [])

    initial_context = ""
    if target_files:
        targeted_chunks = retrieve_local_files(search_path, target_files)
        initial_context = "\n\n---\n\n".join(c["content"] for c in targeted_chunks)
        print(f"   Retrieved {len(targeted_chunks)} targeted file(s)")

    # Step 3: Generate plan
    print("[Step 3] Generating execution plan...")
    planner = AgentPlanner(llm)
    plan = planner.create_plan(
        user_request=question,
        project_context=project_context,
        file_structure=project_structure,
        available_tools=available_tools,
        history=history,
    )
    print(f"   Plan: {plan.goal} ({plan.total_steps} steps, {plan.complexity})")

    # Step 4: Execute plan
    print("[Step 4] Executing plan...")
    executor = AgentExecutor(
        llm=llm,
        tools=tools,
        available_tools=available_tools,
        planner=planner,
        workspace_path=search_path,
        terminal_tool=terminal_tool,
    )

    result = executor.execute(
        plan=plan,
        initial_context=initial_context,
        project_structure=project_structure,
        history=history,
    )

    print(f"   [Agent] Status: {result.status} "
          f"({result.plan.completed_steps}/{result.plan.total_steps} steps done)")

    if result.changeset.changes:
        print(f"   [Agent] Files changed: {len(result.changeset.changes)}")
        for change in result.changeset.changes:
            print(f"     {change.diff_summary}")

    return result.answer, result.changeset.to_dict()
