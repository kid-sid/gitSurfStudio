"""
Orchestrator: Coordinates pipelines and the shared ReAct action loop.

  - run_code_aware_pipeline: GitHub repo search (8-step skeleton + dual graph pipeline)
  - run_local_pipeline: Local file system search (4-step pipeline)
  - execute_action_loop: Shared ReAct (Reason + Act) loop used by both pipelines
"""

import os
import re
import json
from typing import List, Dict, Optional

# ── Tier-0 fast-path: overview / context questions ─────────────────────────────
# Questions that can be answered by reading README + project docs directly —
# no search pipeline, no embeddings, no ripgrep needed.
_OVERVIEW_PATTERNS = [
    # What is / what does
    r'\bwhat\s+does\s+(this|the)?\s*(project|codebase|repo|code|app)\s+(do|mean|contain|provide)\b',
    r'\bwhat\s+is\s+(this|the)\s*(project|codebase|repo|code|app)\b',
    # Explain / describe / summarize
    r'\b(explain|describe|summarize?|overview\s+of|purpose\s+of)\b.{0,60}\b(project|codebase|repo|code|app|architecture)\b',
    r'\b(explain|describe)\s+(the\s+)?(main\s+)?(architecture|structure|design|overview|features?)\b',
    # Project overview
    r'\b(project|codebase|repo|app)\s+(overview|summary|purpose|goal)\b',
    r'\btell\s+me\s+about\s+(this\s+)?(project|codebase|repo|app)\b',
    r'\bhow\s+does\s+(this\s+)?(project|app|code|tool)\s+work\b',
    r'\bwhat\s+can\s+(this|the)\s+(app|project|tool)\s+do\b',
    r'\bmain\s+(purpose|goal|objective|functionality|feature)\b',
    r'\barchitecture\s+of\s+(this|the)\s*(project|code|app|system)\b',
    # How to run / start / install / setup / use
    r'\bhow\s+(to|do\s+i|can\s+i)\s+(run|start|launch|execute|install|setup|set\s+up|use|get\s+started|deploy|build)\b',
    r'\b(steps?|instructions?|guide|way)\s+(to|for)\s+(run|start|install|setup|use|deploy|build)\b',
    r'\bhow\s+do\s+i\s+(get\s+started|run\s+it|install|set\s+this\s+up)\b',
    r'\b(run|start|install|setup|launch)\s+(this\s+)?(project|app|code|repo|locally)\b',
    r'\brun\s+(the\s+)?(project|app|code|server)\s+(locally|now)?\b',
    r'\bget\s+(this\s+)?(running|started|working)\b',
    # Short / vague
    r'^\s*(what is this|what does this do|explain this|overview|how to run|how to use|getting started)\s*\??$',
]
_OVERVIEW_RE = [re.compile(p, re.IGNORECASE) for p in _OVERVIEW_PATTERNS]

# Files read for overview questions — ordered by priority
_OVERVIEW_FILES = [
    'README.md', 'README.rst', 'README.txt', 'README',
    'CLAUDE.md', 'ARCHITECTURE.md', 'OVERVIEW.md', 'DESIGN.md', 'CONTRIBUTING.md',
    'package.json', 'pyproject.toml', 'go.mod', 'Cargo.toml', 'setup.py',
]


def _is_overview_question(question: str) -> bool:
    return any(p.search(question.strip()) for p in _OVERVIEW_RE)


def _read_overview_files(search_path: str, max_chars: int = 6000) -> str:
    """Read README and project config files directly — no indexing needed."""
    parts = []
    for fname in _OVERVIEW_FILES:
        fpath = os.path.join(search_path, fname)
        if not os.path.isfile(fpath):
            continue
        try:
            with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read(max_chars)
            parts.append(f"### {fname}\n{content}")
            if sum(len(p) for p in parts) >= max_chars * 2:
                break
        except Exception:
            pass
    return "\n\n---\n\n".join(parts)

from src.agent.planner import AgentPlanner
from src.agent.executor import AgentExecutor
from src.guardrails import validate_answer, validate_action
from src.tools.search_tool import SearchTool
from src.tools.vector_search_tool import VectorSearchTool
from src.tools.bm25_search_tool import BM25SearchTool
from src.tools.targeted_retriever import TargetedRetriever
from src.tools.symbol_extractor import SymbolExtractor
from src.tools.call_graph import CallGraph
from src.tools.file_editor_tool import FileEditorTool
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
# Modern LLMs (Claude/GPT-4o) handle 128k+ tokens; 350k chars is ~85k tokens.
_MAX_CONTEXT_CHARS = 350_000
# Reserve this many chars for the initial context (targeted files).
_INITIAL_CONTEXT_RESERVE = 100_000


def _build_context_for_llm(
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
    initial_context_str: str = initial_context
    base = prefix + initial_context_str[:_INITIAL_CONTEXT_RESERVE]
    budget = _MAX_CONTEXT_CHARS - len(base)

    if budget <= 0:
        return base

    # Fit as many recent logs as possible within the remaining budget
    kept: List[str] = []
    for log in reversed(action_logs):
        if len(log) <= budget:
            kept.insert(0, log)
            budget -= len(log)
        elif budget > 2000: # If it doesn't fit, but we have space, truncate it
            # Truncate to use the rest of the budget
            log_str: str = log
            truncated_log = log_str[:budget-500] + "... [Log Truncated]"
            kept.insert(0, truncated_log)
            budget = 0
            break
        else:
            break
    
    # Safety: if we have NO logs because the recent one was huge and we had no budget,
    # force at least a partial view of the most recent observation.
    if not kept and action_logs:
        most_recent: str = action_logs[-1]
        truncated = most_recent[:15000] + "... [Log Truncated]"
        kept.append(truncated)

    dropped = len(action_logs) - len(kept)
    omission = f"\n\n[{dropped} earlier action(s) omitted to stay within context limit]\n" if dropped else ""
    return base + omission + "".join(kept)


#  Shared: ReAct Action Loop
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
    # _exact_calls:  normalized "tool.method:{sorted_args_json}" → count
    # _method_calls: normalized "tool.method" (ignoring args) → count
    # The LLM often varies args slightly (different query text, case changes)
    # to dodge exact-match detection, so we track both granularities.
    _exact_calls: Dict[str, int] = {}
    _method_calls: Dict[str, int] = {}

    for iteration in range(1, max_iterations + 1):
        print(f"   [Iteration {iteration}/{max_iterations}] Thinking...")

        context_for_llm = _build_context_for_llm(
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
            # Stream the answer token-by-token via stdout ([ANSWER_TOKEN] prefix)
            answer = llm.stream_final_answer(
                question, context_for_llm, history=history
            )
            break

        elif action_type == "tool_call":
            tool_name = decision.get("tool")
            method = decision.get("method")
            kwargs = decision.get("args", {})
            print(f"   Action -> {tool_name}.{method}({kwargs})")

            # ── Loop detection ──────────────────────────────────────────
            # Normalize: strip mcp__ prefixes, collapse hyphens/underscores
            # so alias calls (mcp__context7__resolve-library-id vs
            # resolve_library_id) are counted together.
            _norm_name = (tool_name or "").replace("mcp__context7__", "").replace("mcp__", "").replace("-", "_")
            _exact_key = f"{_norm_name}.{method}:{json.dumps(kwargs, sort_keys=True)}"
            _method_key = f"{_norm_name}.{method}"

            # Skip loop counting for AutoChain-handled tools — the orchestrator
            # already intercepts resolve-library-id and auto-calls query-docs,
            # so the LLM's first call is legitimate and should never be blocked.
            _is_autochain_target = (
                ("resolve" in _norm_name and "library" in _norm_name)
                or ("browser" in _norm_name and "navigate" in _norm_name)
            )

            if not _is_autochain_target:
                _exact_calls[_exact_key] = _exact_calls.get(_exact_key, 0) + 1
                _method_calls[_method_key] = _method_calls.get(_method_key, 0) + 1
            else:
                # Still count AutoChain targets, but only for hard-block safety
                _exact_calls[_exact_key] = _exact_calls.get(_exact_key, 0) + 1

            exact_count = _exact_calls.get(_exact_key, 0)
            method_count = _method_calls.get(_method_key, 0)

            # Thresholds:
            #   Soft block  = exact repeat 3+ OR same method 4+ (with varied args)
            #   Hard block  = exact repeat 4+ OR same method 5+
            # AutoChain targets only hard-block on exact repeat 3+ (they skip method counting)
            _soft_limit_exact = 3
            _soft_limit_method = 4
            _hard_limit_exact = 4
            _hard_limit_method = 5

            is_hard_loop = exact_count >= _hard_limit_exact or method_count >= _hard_limit_method
            is_soft_loop = (not _is_autochain_target) and (
                exact_count >= _soft_limit_exact or method_count >= _soft_limit_method
            )

            if is_hard_loop:
                print(f"   [LoopGuard] Hard block — {_norm_name}.{method} (exact={exact_count}, method={method_count})")
                observation = (
                    f"[LoopGuard] BLOCKED — {_norm_name}.{method} has been called too many times. "
                    f"Forcing final answer with available context."
                )
                action_logs.append(f"\n\n--- ACTION LOG ---\nAction taken: {tool_name}.{method}({kwargs})\nObservation: {observation}")
                context_for_llm = _build_context_for_llm(
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

            # ── Auto-chain: resolve-library-id → query-docs ─────────────
            # The LLM consistently fails to follow through to the second
            # Context7 call, so we do it programmatically: when resolve
            # succeeds, immediately call query-docs with the returned
            # ID and inject both results into the action log.
            if "resolve" in _norm_name and "library" in _norm_name:
                _id_match = re.search(r'library ID:\s*(\S+)', str(observation))
                if _id_match:
                    _lib_id = _id_match.group(1)
                    _topic = question[:120]
                    print(f"   [AutoChain] Resolved library ID: {_lib_id} → fetching docs for topic: \"{_topic}\"")

                    # Find the Context7 docs tool — the server exposes it as
                    # "query-docs" (not "get-library-docs" as older versions did)
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
                    # Fallback: scan all context7 tool keys that look doc-related
                    if not _docs_tool:
                        for _k, _v in tools.items():
                            _kn = _k.lower().replace("-", "_")
                            if "context7" in _kn and ("doc" in _kn or "query" in _kn) and "resolve" not in _kn:
                                _docs_tool = _v
                                break

                    if _docs_tool:
                        try:
                            # query-docs expects `libraryId` + `query`
                            _docs_result = _docs_tool.execute(
                                libraryId=_lib_id, query=_topic
                            )
                        except Exception as _e:
                            _docs_result = f"[Error] query-docs raised: {_e}"

                        _docs_preview = str(_docs_result)[:200]
                        print(f"   [AutoChain] Docs result: {_docs_preview}...")

                        # Log both steps as a single combined action
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
                        continue  # skip generic reflection, go to next iteration
                    else:
                        _mcp_keys = [k for k in tools if "mcp" in k.lower() or "library" in k.lower() or "context7" in k.lower()]
                        print(f"   [AutoChain] docs tool not found — forcing final answer. MCP-like keys: {_mcp_keys}")
                        # Docs tool missing — skip straight to final answer instead
                        # of giving the LLM another iteration (it will just retry resolve)
                        action_logs.append(
                            f"\n\n--- ACTION LOG ---\n"
                            f"Action taken: {tool_name}.{method}({kwargs})\n"
                            f"Observation: Resolved library ID: {_lib_id}. "
                            f"Docs-fetching tool is unavailable; answering with gathered context."
                        )
                        context_for_llm = _build_context_for_llm(
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
                        continue  # skip generic reflection

            # Update rel_path to path in iteration logs for consistency
            if "rel_path" in kwargs:
                kwargs["path"] = kwargs.pop("rel_path")
            
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
        context_for_llm = _build_context_for_llm(
            initial_context, action_logs, extra_prefix=extra_context_prefix
        )
        answer = llm.stream_final_answer(question, context_for_llm, history=history)

    # Validate final answer — redact secrets/PII, append safety notices
    answer, answer_warnings = validate_answer(answer)
    for w in answer_warnings:
        print(f"   [AnswerGuard] {w}")

    return answer


# Pipeline: Code-Aware (GitHub repos)
def run_code_aware_pipeline(
    question: str,
    search_path: str,
    llm,
    project_context: str,
    available_tools: str,
    tools: Dict,
    history=None,
    rebuild_index: bool = False,
    ctx: Optional[PipelineContext] = None,
) -> tuple:
    """
    8-step code-aware pipeline for GitHub repos.
    Returns (answer: str, full_context: str).
    """
    print("\n[Code-Aware Pipeline]")

    # Step 1: Load Project Skeleton + Symbol MiniMap
    print("[Step 1/8] Loading Project Skeleton...")
    project_structure = ""
    structure_path = os.path.join(search_path, "project_structure.txt")
    if os.path.exists(structure_path):
        try:
            with open(structure_path, "r", encoding="utf-8") as f:
                project_structure = f.read()
            print(f"   Loaded file tree ({len(project_structure.splitlines())} entries)")
        except Exception:
            pass

    symbol_minimap: Dict = {}
    minimap_path = os.path.join(search_path, "symbol_minimap.json")
    if os.path.exists(minimap_path):
        try:
            with open(minimap_path, "r", encoding="utf-8") as f:
                symbol_minimap = json.load(f)
            print(f"   Loaded Symbol MiniMap for {len(symbol_minimap)} files")
        except Exception:
            pass

    # Step 2: Query Refinement
    print("[Step 2/8] Refining Query (Technical Intent)...")
    refined_data = llm.refine_user_query(
        question, history=history, project_context=project_context, file_structure=project_structure
    )
    query_to_use = refined_data.get("refined_question", question)
    technical_intent = refined_data.get("intent", "General search")
    expansion_keywords = refined_data.get("keywords", [])
    is_action_request = refined_data.get("is_action_request", False)
    direct_tool_call = refined_data.get("direct_tool_call")
    print(f"   Intent: {technical_intent}")
    print(f"   Refined to: \"{query_to_use}\"")
    print(f"   Keywords: {expansion_keywords[:5]}")

    if direct_tool_call:
        print(f"   [Fast-Path] Executing direct tool call: {direct_tool_call}")
        tool_name = direct_tool_call.get("tool")
        method = direct_tool_call.get("method")
        args = direct_tool_call.get("args", {})
        tool_instance = tools.get(tool_name)
        if tool_instance:
            fn = getattr(tool_instance, method, None)
            if fn:
                try:
                    observation = fn(**args)
                    return f"Action taken: {tool_name}.{method}({args})\nObservation: {observation}", ""
                except Exception as e:
                    return f"[Error] Fast-Path failed: {e}", ""
        print("   [Fast-Path] Tool or method not found, falling back to full pipeline.")

    top_chunks: List[Dict] = []
    call_graph_context = ""
    skeleton_context = ""

    # Step 3: Skeleton Analysis
    print("[Step 3/8] Skeleton Analysis (identifying relevant files)...")
    targeted_files: List[str] = []
    if project_structure:
        targeted_files = llm.identify_relevant_files(
            query_to_use, project_structure, symbol_minimap=symbol_minimap
        )
        if targeted_files:
            skeleton_context = "Targeted files:\n" + "\n".join(
                f"  - {f}" for f in targeted_files
            )

    # Step 4: Targeted File Retrieval
    print("[Step 4/8] Targeted File Retrieval...")
    targeted_retriever = TargetedRetriever(cache_path=search_path)
    targeted_chunks: List[Dict] = []
    if targeted_files:
        targeted_chunks = targeted_retriever.retrieve_files(targeted_files)
        print(f"   Retrieved {len(targeted_chunks)} targeted file(s)")
    else:
        print("   No targeted files identified, relying on search only")

    if is_action_request:
        # Action requests: use skeleton + targeted files as context,
        # skip heavy search/embedding steps — the agent loop has tools to read more.
        top_chunks = list(targeted_chunks)
        print(f"   [Action-Path] Using {len(top_chunks)} targeted chunks. Skipping search pipeline (Steps 5-7).")
    else:
        # Step 5: Symbol Extraction + Call Graph
        print("[Step 5/8] Code Analysis (Symbols + Call Graph)...")
        sym_extractor = ctx.sym_extractor if ctx else SymbolExtractor(cache_dir=os.path.join(".cache", "symbols"))
        symbol_index = sym_extractor.extract_from_directory(
            search_path, force_rebuild=rebuild_index
        )
        cg = ctx.call_graph if ctx else CallGraph(cache_dir=os.path.join(".cache", "call_graph"))
        cg.build_from_symbols(symbol_index, force_rebuild=rebuild_index)

        # Step 6: Triple-Hybrid Search
        print("[Step 6/8] Triple-Hybrid Search (Skeleton-Guided)...")
        vector_tool = ctx.vector_tool if ctx else VectorSearchTool(
            embedding_client=EmbeddingClient(), cache_dir=os.path.join(".cache", "vector_index")
        )
        vector_tool.build_index_with_symbols(
            search_path, symbol_index, force_rebuild=rebuild_index
        )
        vector_results = vector_tool.search(query_to_use, top_k=20)

        bm25_tool = ctx.bm25_tool if ctx else BM25SearchTool(cache_dir=os.path.join(".cache", "bm25_index"))
        bm25_tool.build_index(vector_tool.metadata, force_rebuild=rebuild_index)
        bm25_results = bm25_tool.search(query_to_use, top_k=20)

        searcher = ctx.searcher if ctx else SearchTool()
        queries = llm.generate_search_queries(
            query_to_use, tool="ripgrep",
            project_context=project_context,
            file_structure=project_structure,
        )
        if expansion_keywords:
            queries = expansion_keywords[:3] + queries
        keyword_chunks: List[Dict] = []
        for q in queries[:5]:
            keyword_chunks.extend(searcher.search_and_chunk(q, search_path))

        print("   Applying Reciprocal Rank Fusion (RRF)...")
        search_candidates = reciprocal_rank_fusion(
            [vector_results, bm25_results, keyword_chunks]
        )

        # Step 7: Merge + Rerank
        print("[Step 7/8] Merging + Reranking...")
        print(f"   [Orchestrator] Keeping {len(targeted_chunks)} targeted chunks.")
        reranker = ctx.reranker if ctx else CrossEncoderReranker()
        slots_remaining = max(10 - len(targeted_chunks), 3)
        reranked_search = reranker.rerank(query_to_use, search_candidates, top_k=slots_remaining)

        top_chunks = list(targeted_chunks)
        seen_paths = {c["file"] for c in targeted_chunks}
        for chunk in reranked_search:
            if chunk["file"] not in seen_paths:
                top_chunks.append(chunk)
                seen_paths.add(chunk["file"])

        print(
            f"   Selected top {len(top_chunks)} chunks "
            f"(Targeted: {len(targeted_chunks)}, Search: {len(top_chunks) - len(targeted_chunks)})"
        )

        # Build call graph context for top symbols
        graph_parts = []
        seen_symbols: set = set()
        for chunk in top_chunks:
            sym = chunk.get("symbol", "")
            if sym and sym not in seen_symbols:
                seen_symbols.add(sym)
                graph_ctx = cg.get_context_for_function(sym, depth=2)
                if graph_ctx and "No call graph data" not in graph_ctx:
                    graph_parts.append(graph_ctx)
        call_graph_context = "\n\n---\n\n".join(graph_parts)

    # Step 8: Agentic Action Loop
    print("[Step 8/8] Agentic Action Loop (Synthesizing/Editing)...")
    initial_context = "\n\n---\n\n".join(c["content"] for c in top_chunks)
    extra_prefix = f"{skeleton_context}\n\n{call_graph_context}".strip()

    answer = execute_action_loop(
        question=question,
        initial_context=initial_context,
        llm=llm,
        tools=tools,
        available_tools=available_tools,
        project_structure=project_structure,
        extra_context_prefix=extra_prefix,
        history=history,
        max_iterations=8,
    )
    return answer, initial_context


#  Helpers: Local File Tree & Direct Retrieval
SKIP_DIRS_LOCAL = {
    'node_modules', '.git', '.cache', '__pycache__', 'venv', '.venv',
    'dist', 'build', 'target', 'bin', 'obj', 'vendor', '.idea', '.vscode',
    '.next', '.nuxt', 'coverage', '.tox', 'eggs', '.eggs', '.claude',
}

INDEXABLE_EXTENSIONS_LOCAL = {
    '.py', '.js', '.ts', '.jsx', '.tsx', '.go', '.java', '.c', '.cpp', '.h',
    '.cs', '.rb', '.php', '.rs', '.swift', '.kt', '.m', '.mm', '.sh', '.bat',
    '.md', '.txt', '.json', '.yaml', '.yml', '.toml', '.env', '.ini',
    '.cfg', '.conf', '.xml', '.rst', '.sql', '.html', '.css',
}


def build_local_file_tree(root_path: str, max_files: int = 500) -> str:
    """Build a lightweight file tree string for skeleton analysis."""
    tree_lines = []
    count: int = 0
    root_path = os.path.abspath(root_path)

    for dirpath, dirnames, filenames in os.walk(root_path):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS_LOCAL]

        rel_dir = os.path.relpath(dirpath, root_path)
        if rel_dir == ".":
            rel_dir = ""

        for filename in sorted(filenames):
            ext = os.path.splitext(filename)[1].lower()
            if ext not in INDEXABLE_EXTENSIONS_LOCAL:
                continue

            rel_path = os.path.join(rel_dir, filename) if rel_dir else filename
            rel_path = rel_path.replace("\\", "/")
            tree_lines.append(rel_path)
            count = count + 1
            if count >= max_files:
                tree_lines.append(f"... ({count}+ files, truncated)")
                return "\n".join(tree_lines)

    return "\n".join(tree_lines)


def retrieve_local_files(
    root_path: str,
    file_paths: List[str],
    max_chars_per_file: int = 100000,
) -> List[Dict]:
    """Read files directly from the local file system (no cache needed)."""
    chunks = []
    root_path = os.path.abspath(root_path)

    for fpath in file_paths:
        # Resolve the file path against root
        abs_path = os.path.join(root_path, fpath)
        if not os.path.isfile(abs_path):
            # Try as-is (might already be absolute or differently rooted)
            if not os.path.isfile(fpath):
                print(f"   [LocalRetriever] File not found: {fpath}")
                continue
            abs_path = fpath

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception as e:
            print(f"   [LocalRetriever] Error reading {fpath}: {e}")
            continue

        if len(content) > max_chars_per_file:
            content = content[:max_chars_per_file] + "\n... [truncated]"

        lines = content.split("\n")
        rel_path = os.path.relpath(abs_path, root_path).replace("\\", "/")

        chunks.append({
            "file": rel_path,
            "content": f"File: {rel_path}\n\n{content}",
            "start_line": 1,
            "end_line": len(lines),
            "source": "targeted",
            "symbol": "",
        })
        print(f"   [LocalRetriever] Retrieved: {rel_path} ({len(lines)} lines)")

    return chunks


#  Pipeline: General (Local File System)
def run_local_pipeline(
    question: str,
    search_path: str,
    llm,
    project_context: str,
    available_tools: str,
    tools: Dict,
    history=None,
    rebuild_index: bool = False,
    ctx: Optional[PipelineContext] = None,
) -> tuple:
    """
    Skeleton-first pipeline for local file searches.
    Uses LLM to identify relevant files FIRST, reads only those,
    and falls back to full search only when needed.
    Returns (answer: str, full_context: str).
    """
    print("\n[Smart Local Pipeline]")

    # ── Tier-0: Overview fast-path ────────────────────────────────────────────
    # For "what does this project do?" style questions: read README + config
    # files directly and answer in a single LLM call. Skip all search steps.
    if _is_overview_question(question):
        print("[Tier-0] Overview question detected — reading project docs directly...")
        project_structure = build_local_file_tree(search_path)
        overview_docs = _read_overview_files(search_path)
        if overview_docs:
            context_parts = []
            if project_context:
                context_parts.append(f"Project Summary:\n{project_context}")
            context_parts.append(f"File Structure:\n{project_structure[:1500]}")
            context_parts.append(overview_docs)
            initial_context = "\n\n---\n\n".join(context_parts)
            print(f"   [Tier-0] Using {len(initial_context)} chars from project docs. Answering directly.")
            answer = execute_action_loop(
                question=question,
                initial_context=initial_context,
                llm=llm,
                tools=tools,
                available_tools=available_tools,
                project_structure=project_structure,
                history=history,
                max_iterations=3,   # allow MCP tool calls (e.g. context7) before synthesizing
            )
            return answer, initial_context
        print("   [Tier-0] No README or project docs found. Falling through to full pipeline.")

    # Step 1: Build local file tree
    print("[Step 1/6] Building local file tree...")
    project_structure = build_local_file_tree(search_path)
    file_count = len([l for l in project_structure.splitlines() if not l.startswith("...")])
    print(f"   Found {file_count} indexable files")

    # Step 2: Query Refinement
    print("[Step 2/6] Refining Query...")
    refined_data = llm.refine_user_query(
        question, history=history, project_context=project_context, file_structure=project_structure
    )
    query_to_use = refined_data.get("refined_question", question)
    expansion_keywords = refined_data.get("keywords", [])
    is_action_request = refined_data.get("is_action_request", False)
    is_overview_question = refined_data.get("is_overview_question", False)
    direct_tool_call = refined_data.get("direct_tool_call")
    print(f"   Refined to: \"{query_to_use}\"")
    print(f"   Keywords: {expansion_keywords[:5]}")

    # ── Tier-1: LLM-classified overview fast-path ─────────────────────────────
    # Catches vague phrasings the regex didn't detect (e.g. "what does it do?",
    # "hm, explain this", "give me a summary", "any idea what this is?")
    if is_overview_question:
        print("   [Tier-1] LLM classified as overview question. Reading project docs...")
        overview_docs = _read_overview_files(search_path)
        if overview_docs:
            context_parts = []
            if project_context:
                context_parts.append(f"Project Summary:\n{project_context}")
            context_parts.append(f"File Structure:\n{project_structure[:1500]}")
            context_parts.append(overview_docs)
            initial_context = "\n\n---\n\n".join(context_parts)
            answer = execute_action_loop(
                question=question,
                initial_context=initial_context,
                llm=llm,
                tools=tools,
                available_tools=available_tools,
                project_structure=project_structure,
                history=history,
                max_iterations=3,   # allow MCP tool calls (e.g. context7) before synthesizing
            )
            return answer, initial_context
        print("   [Tier-1] No project docs found. Falling through to full pipeline.")

    if direct_tool_call:
        print(f"   [Fast-Path] Executing direct tool call: {direct_tool_call}")
        tool_name = direct_tool_call.get("tool")
        method = direct_tool_call.get("method")
        args = direct_tool_call.get("args", {})
        tool_instance = tools.get(tool_name)
        if tool_instance:
            fn = getattr(tool_instance, method, None)
            if fn:
                try:
                    observation = fn(**args)
                    return f"Action taken: {tool_name}.{method}({args})\nObservation: {observation}", ""
                except Exception as e:
                    return f"[Error] Fast-Path failed: {e}", ""
        print("   [Fast-Path] Tool or method not found, falling back to full pipeline.")

    top_chunks: List[Dict] = []

    # Step 3: Skeleton Analysis (LLM identifies key files)
    print("[Step 3/6] Skeleton Analysis (identifying relevant files)...")
    targeted_files = llm.identify_relevant_files(
        query_to_use, project_structure, symbol_minimap=None
    )

    # Step 4: Targeted Retrieval (read only those files)
    print("[Step 4/6] Targeted File Retrieval...")
    targeted_chunks: List[Dict] = []
    if targeted_files:
        targeted_chunks = retrieve_local_files(search_path, targeted_files)
        print(f"   Retrieved {len(targeted_chunks)} targeted file(s)")
    else:
        print("   No targeted files identified")

    if is_action_request:
        # Action requests: use skeleton + targeted files as context,
        # skip heavy keyword/embedding search — the agent loop has tools to read more.
        top_chunks = list(targeted_chunks)
        print(f"   [Action-Path] Using {len(top_chunks)} targeted chunks. Skipping keyword/embedding search.")
    else:
        # Step 5: Ripgrep for keyword matches
        print("[Step 5/6] Keyword Search (Ripgrep)...")
        searcher = ctx.searcher if ctx else SearchTool()
        queries = llm.generate_search_queries(
            query_to_use, tool="ripgrep",
            project_context=project_context,
            file_structure=project_structure,
        )
        if expansion_keywords:
            queries = expansion_keywords[:3] + queries
        keyword_chunks: List[Dict] = []
        for q in queries[:5]:
            keyword_chunks.extend(searcher.search_and_chunk(q, search_path))

        # Merge targeted + keyword results
        top_chunks = list(targeted_chunks)
        seen_files = {c["file"] for c in targeted_chunks}
        for chunk in keyword_chunks:
            if chunk["file"] not in seen_files:
                top_chunks.append(chunk)
                seen_files.add(chunk["file"])

        # If we have enough context, skip heavy FAISS/BM25
        if len(top_chunks) >= 3:
            print(f"   [Smart-Route] Sufficient context ({len(top_chunks)} chunks). Skipping full embedding index.")
            # Light reranking on the smaller result set
            if len(top_chunks) > 5:
                reranker = ctx.reranker if ctx else CrossEncoderReranker()
                top_chunks = (
                    list(targeted_chunks)
                    + reranker.rerank(query_to_use, keyword_chunks, top_k=max(5 - len(targeted_chunks), 2))
                )
        else:
            # Fallback: not enough from skeleton — run full FAISS + BM25
            print(f"   [Fallback] Only {len(top_chunks)} chunks found. Running full search index...")
            vector_tool = ctx.vector_tool if ctx else VectorSearchTool(
                embedding_client=EmbeddingClient(), cache_dir=os.path.join(".cache", "vector_index")
            )
            vector_tool.build_index(search_path, force_rebuild=rebuild_index)
            vector_results = vector_tool.search(query_to_use, top_k=20)

            bm25_tool = ctx.bm25_tool if ctx else BM25SearchTool(cache_dir=os.path.join(".cache", "bm25_index"))
            bm25_tool.build_index(vector_tool.metadata, force_rebuild=rebuild_index)
            bm25_results = bm25_tool.search(query_to_use, top_k=20)

            candidates = reciprocal_rank_fusion(
                [vector_results, bm25_results, keyword_chunks]
            )
            reranker = ctx.reranker if ctx else CrossEncoderReranker()
            search_chunks = reranker.rerank(query_to_use, candidates, top_k=5)

            # Merge with any targeted chunks
            for chunk in search_chunks:
                if chunk["file"] not in seen_files:
                    top_chunks.append(chunk)
                    seen_files.add(chunk["file"])

    print(f"   Final context: {len(top_chunks)} chunks")

    # Step 6: Agentic Action Loop
    print("[Step 6/6] Agentic Action Loop (Synthesizing/Editing)...")
    initial_context = "\n\n---\n\n".join(c["content"] for c in top_chunks)

    answer = execute_action_loop(
        question=question,
        initial_context=initial_context,
        llm=llm,
        tools=tools,
        available_tools=available_tools,
        project_structure=project_structure,
        history=history,
        max_iterations=8,
    )
    return answer, initial_context


# ── Pipeline: Agent Mode (Plan → Execute → Verify) ────────────────────────────

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
    state=None,
    session_id: Optional[str] = None,
    session_memory=None,
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
    # BUG FIX: assign to state so /agent/cancel and /agent/respond work
    if state is not None:
        state.active_executor = executor

    # Step 3.5: Initialize session memory for in-session recovery
    import uuid
    task_id = None
    if session_memory and session_id:
        task_id = uuid.uuid4().hex[:12]
        session_memory.start_task(session_id, task_id, plan.to_dict(), question)
        # Store session context in state for /agent/respond endpoint
        if state is not None:
            state.active_session_id = session_id
            state.active_task_id = task_id

    result = executor.execute(
        plan=plan,
        initial_context=initial_context,
        project_structure=project_structure,
        history=history,
        session_memory=session_memory,
        session_id=session_id,
        task_id=task_id,
    )

    print(f"   [Agent] Status: {result.status} "
          f"({result.plan.completed_steps}/{result.plan.total_steps} steps done)")

    if result.changeset.changes:
        print(f"   [Agent] Files changed: {len(result.changeset.changes)}")
        for change in result.changeset.changes:
            print(f"     {change.diff_summary}")

    # Clear active executor after completion
    if state is not None:
        state.active_executor = None

    return result.answer, result.changeset.to_dict()
