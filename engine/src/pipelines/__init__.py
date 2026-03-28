"""
Pipeline modules for GitSurf Studio.

Re-exports the three pipeline entry points and PipelineContext.
"""

from src.pipelines.context import PipelineContext, reciprocal_rank_fusion
from src.pipelines.action_loop import execute_action_loop
from src.pipelines.local_pipeline import run_local_pipeline
from src.pipelines.code_aware_pipeline import run_code_aware_pipeline
from src.pipelines.agent_pipeline import run_agent_pipeline

__all__ = [
    "PipelineContext",
    "reciprocal_rank_fusion",
    "execute_action_loop",
    "run_local_pipeline",
    "run_code_aware_pipeline",
    "run_agent_pipeline",
]
