"""
PipelineContext and shared utilities for all pipelines.
"""

import os
from typing import List, Dict

from src.tools.search_tool import SearchTool
from src.tools.vector_search_tool import VectorSearchTool
from src.tools.bm25_search_tool import BM25SearchTool
from src.tools.symbol_extractor import SymbolExtractor
from src.tools.call_graph import CallGraph
from src.embeddings import EmbeddingClient
from src.reranker import CrossEncoderReranker


class PipelineContext:
    """Holds initialized tool instances to avoid re-instantiation across queries.
    Create once in main.py and pass into pipeline calls."""

    def __init__(self, search_path: str, rebuild_index: bool = False):
        self.search_path = os.path.abspath(search_path)
        self.rebuild_index = rebuild_index

        # Resolve cache dirs relative to the engine directory, not CWD
        _engine_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._cache_base = os.path.join(_engine_dir, ".cache")

        # Lazily initialized tools (populated on first search query)
        self._emb_client = None
        self._vector_tool = None
        self._bm25_tool = None
        self._sym_extractor = None
        self._call_graph = None
        self._reranker = None
        self._searcher = None

        # Track if indexes have been built at least once
        self._indexes_built = False

    @property
    def emb_client(self):
        if self._emb_client is None:
            self._emb_client = EmbeddingClient()
        return self._emb_client

    @property
    def vector_tool(self):
        if self._vector_tool is None:
            self._vector_tool = VectorSearchTool(
                embedding_client=self.emb_client,
                cache_dir=os.path.join(self._cache_base, "vector_index"),
            )
        return self._vector_tool

    @property
    def bm25_tool(self):
        if self._bm25_tool is None:
            self._bm25_tool = BM25SearchTool(cache_dir=os.path.join(self._cache_base, "bm25_index"))
        return self._bm25_tool

    @property
    def sym_extractor(self):
        if self._sym_extractor is None:
            self._sym_extractor = SymbolExtractor(cache_dir=os.path.join(self._cache_base, "symbols"))
        return self._sym_extractor

    @property
    def call_graph(self):
        if self._call_graph is None:
            self._call_graph = CallGraph(cache_dir=os.path.join(self._cache_base, "call_graph"))
        return self._call_graph

    @property
    def reranker(self):
        if self._reranker is None:
            self._reranker = CrossEncoderReranker()
        return self._reranker

    @property
    def searcher(self):
        if self._searcher is None:
            self._searcher = SearchTool()
        return self._searcher


def reciprocal_rank_fusion(results_lists: List[List[Dict]], k: int = 60) -> List[Dict]:
    """Standard RRF algorithm to merge multiple ranked result lists."""
    scores: Dict = {}
    doc_map: Dict = {}

    for results in results_lists:
        for rank, doc in enumerate(results):
            key = (doc["file"], doc["start_line"], doc["end_line"])
            scores.setdefault(key, 0.0)
            doc_map[key] = doc
            scores[key] += 1.0 / (k + rank + 1)

    sorted_keys = sorted(scores, key=lambda x: scores[x], reverse=True)
    merged = []
    for key in sorted_keys:
        doc = doc_map[key]
        doc["rrf_score"] = scores[key]
        merged.append(doc)
    return merged


# Maximum characters sent to the LLM in a single action loop iteration.
_MAX_CONTEXT_CHARS = 80_000
# Reserve this many chars for the initial context even when truncating logs.
_INITIAL_CONTEXT_RESERVE = 40_000


def build_context_for_llm(
    initial_context: str,
    action_logs: List[str],
    extra_prefix: str = "",
) -> str:
    """
    Build the context string passed to the LLM, respecting _MAX_CONTEXT_CHARS.
    Always keeps the initial context. Drops the oldest action logs first when
    the budget is exceeded, printing a notice so the LLM knows steps were omitted.
    """
    prefix = f"{extra_prefix}\n\n" if extra_prefix else ""
    base = prefix + str(initial_context)[:_INITIAL_CONTEXT_RESERVE]
    budget = _MAX_CONTEXT_CHARS - len(base)

    if budget <= 0:
        return base

    # Fit as many recent logs as possible within the remaining budget
    kept: List[str] = []
    for log in reversed(action_logs):
        if len(log) > budget:
            break
        kept.insert(0, log)
        budget -= len(log)

    dropped = len(action_logs) - len(kept)
    omission = f"\n\n[{dropped} earlier action(s) omitted to stay within context limit]\n" if dropped else ""
    return base + omission + "".join(kept)
