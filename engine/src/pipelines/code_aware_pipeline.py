"""
Code-aware pipeline for GitHub repos — 8-step skeleton + dual graph search.
"""

import os
import json
from typing import List, Dict, Optional

from src.tools.search_tool import SearchTool
from src.tools.vector_search_tool import VectorSearchTool
from src.tools.bm25_search_tool import BM25SearchTool
from src.tools.targeted_retriever import TargetedRetriever
from src.tools.symbol_extractor import SymbolExtractor
from src.tools.call_graph import CallGraph
from src.embeddings import EmbeddingClient
from src.reranker import CrossEncoderReranker
from src.pipelines.context import PipelineContext, reciprocal_rank_fusion
from src.pipelines.action_loop import execute_action_loop


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

    code_aware_plan = {
        "goal": question,
        "complexity": "complex",
        "steps": [
            {"id": 1, "description": "Loading Project Skeleton", "status": "pending"},
            {"id": 2, "description": "Refining Query", "status": "pending"},
            {"id": 3, "description": "Skeleton Analysis", "status": "pending"},
            {"id": 4, "description": "Targeted File Retrieval", "status": "pending"},
            {"id": 5, "description": "Code Analysis (Symbols + Call Graph)", "status": "pending"},
            {"id": 6, "description": "Triple-Hybrid Search", "status": "pending"},
            {"id": 7, "description": "Merging + Reranking", "status": "pending"},
            {"id": 8, "description": "Agentic Action Loop", "status": "pending"},
        ]
    }
    print(f"[UI_COMMAND] agent_plan {json.dumps(code_aware_plan)}")

    # Step 1: Load Project Skeleton + Symbol MiniMap
    print("[Step 1/8] Loading Project Skeleton...")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 1, 'status': 'running'})}")
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
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 1, 'status': 'done'})}")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'running'})}")
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

    if is_action_request:
        print("   [Fast-Path] Action command detected. Skipping search pipeline (Steps 3-7).")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'done'})}")
    else:
        # Step 3: Skeleton Analysis
        print("[Step 3/8] Skeleton Analysis (identifying relevant files)...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'running'})}")
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
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'running'})}")
        targeted_retriever = TargetedRetriever(cache_path=search_path)
        targeted_chunks: List[Dict] = []
        if targeted_files:
            targeted_chunks = targeted_retriever.retrieve_files(targeted_files)
            print(f"   Retrieved {len(targeted_chunks)} targeted file(s)")
        else:
            print("   No targeted files identified, relying on search only")

        # Step 5: Symbol Extraction + Call Graph
        print("[Step 5/8] Code Analysis (Symbols + Call Graph)...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'running'})}")
        sym_extractor = ctx.sym_extractor if ctx else SymbolExtractor(cache_dir=os.path.join(".cache", "symbols"))
        symbol_index = sym_extractor.extract_from_directory(
            search_path, force_rebuild=rebuild_index
        )
        cg = ctx.call_graph if ctx else CallGraph(cache_dir=os.path.join(".cache", "call_graph"))
        cg.build_from_symbols(symbol_index, force_rebuild=rebuild_index)

        # Step 6: Triple-Hybrid Search
        print("[Step 6/8] Triple-Hybrid Search (Skeleton-Guided)...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 6, 'status': 'running'})}")
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
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 6, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 7, 'status': 'running'})}")
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
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 7, 'status': 'done'})}")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 8, 'status': 'running'})}")
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
