"""
Orchestrator — backward-compatible re-export shim.

All pipeline logic has been moved to src/pipelines/.
This module re-exports the public API so existing imports continue to work.
"""

from src.pipelines.context import PipelineContext, reciprocal_rank_fusion  # noqa: F401
from src.pipelines.action_loop import execute_action_loop  # noqa: F401
from src.pipelines.local_pipeline import (  # noqa: F401
    run_local_pipeline,
    build_local_file_tree,
    retrieve_local_files,
)
from src.pipelines.code_aware_pipeline import run_code_aware_pipeline  # noqa: F401
from src.pipelines.agent_pipeline import run_agent_pipeline  # noqa: F401
