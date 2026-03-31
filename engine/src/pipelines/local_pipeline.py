"""
Local file system pipeline — skeleton-first search for local workspaces.
"""

import os
import json
from typing import List, Dict, Optional

from src.tools.search_tool import SearchTool
from src.tools.glob_tool import GlobTool
from src.tools.vector_search_tool import VectorSearchTool
from src.tools.bm25_search_tool import BM25SearchTool
from src.embeddings import EmbeddingClient
from src.reranker import CrossEncoderReranker
from src.pipelines.context import PipelineContext, reciprocal_rank_fusion
from src.pipelines.action_loop import execute_action_loop


# ── Local file tree helpers ────────────────────────────────────────────────────

SKIP_DIRS_LOCAL = {
    'node_modules', '.git', '.cache', '__pycache__', 'venv', '.venv',
    'dist', 'build', 'target', 'bin', 'obj', 'vendor', '.idea', '.vscode',
    '.next', '.nuxt', 'coverage', '.tox', 'eggs', '.eggs',
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
    count = 0
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
            count += 1
            if count >= max_files:
                tree_lines.append(f"... ({count}+ files, truncated)")
                return "\n".join(tree_lines)

    return "\n".join(tree_lines)


def retrieve_local_files(
    root_path: str,
    file_paths: List[str],
    max_chars_per_file: int = 15000,
) -> List[Dict]:
    """Read files directly from the local file system (no cache needed)."""
    chunks = []
    root_path = os.path.abspath(root_path)

    for fpath in file_paths:
        abs_path = os.path.join(root_path, fpath)
        if not os.path.isfile(abs_path):
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


# ── Pipeline entry point ───────────────────────────────────────────────────────

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
    Returns (answer: str, full_context: str).
    """
    print("\n[Smart Local Pipeline]")

    # Step 1: Build local file tree
    print("[Step 1/6] Building local file tree...")

    local_plan = {
        "goal": question,
        "complexity": "moderate",
        "steps": [
            {"id": 1, "description": "Building local file tree", "status": "pending"},
            {"id": 2, "description": "Refining Query", "status": "pending"},
            {"id": 3, "description": "Searching codebase", "status": "pending"},
            {"id": 4, "description": "Gathering context", "status": "pending"},
            {"id": 5, "description": "Analyzing results", "status": "pending"},
            {"id": 6, "description": "Agentic Action Loop", "status": "pending"},
        ]
    }
    print(f"[UI_COMMAND] agent_plan {json.dumps(local_plan)}")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 1, 'status': 'running'})}")
    project_structure = build_local_file_tree(search_path)
    file_count = len([l for l in project_structure.splitlines() if not l.startswith("...")])
    print(f"   Found {file_count} indexable files")

    # Step 2: Query Refinement
    print("[Step 2/6] Refining Query...")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 1, 'status': 'done'})}")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'running'})}")
    refined_data = llm.refine_user_query(
        question, history=history, project_context=project_context, file_structure=project_structure
    )
    query_to_use = refined_data.get("refined_question", question)
    expansion_keywords = refined_data.get("keywords", [])
    is_action_request = refined_data.get("is_action_request", False)
    direct_tool_call = refined_data.get("direct_tool_call")
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

    if is_action_request:
        print("   [Action-Path] Gathering context for action request...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'running'})}")

        targeted_files = llm.identify_relevant_files(
            query_to_use, project_structure, symbol_minimap=None
        )
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'running'})}")

        if targeted_files:
            top_chunks = retrieve_local_files(search_path, targeted_files)
            print(f"   [Action-Path] Pre-read {len(top_chunks)} relevant file(s): {[c['file'] for c in top_chunks]}")
        else:
            print("   [Action-Path] No targeted files identified, agent will read as needed")

        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'done'})}")
    else:
        # ── Step 3: Quick Search (Glob + Grep) ─────────────────────────
        # Try lightweight file-name matching and content grep before
        # falling back to the heavier skeleton-analysis / embedding path.
        print("[Step 3/6] Quick Search (Glob + Grep)...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'running'})}")

        light_chunks: List[Dict] = []
        seen_files: set = set()

        # 3a: Use target_files already identified during query refinement
        target_files_hint = refined_data.get("target_files", [])
        if target_files_hint:
            tgt_chunks = retrieve_local_files(search_path, target_files_hint)
            for c in tgt_chunks:
                if c["file"] not in seen_files:
                    light_chunks.append(c)
                    seen_files.add(c["file"])
            if light_chunks:
                print(f"   [Target] {len(light_chunks)} file(s) from refinement: {[c['file'] for c in light_chunks]}")

        # 3b: Glob for files whose *name* matches the top keywords
        if len(light_chunks) < 5 and expansion_keywords:
            _glob = GlobTool(search_path)
            for kw in expansion_keywords[:3]:
                if len(kw) < 3:
                    continue
                glob_matches = _glob.list_files(f"*{kw}*")
                new_files = [
                    f for f in glob_matches
                    if not f.startswith("[Error]") and f not in seen_files
                ]
                if new_files:
                    for c in retrieve_local_files(search_path, new_files[:3]):
                        if c["file"] not in seen_files:
                            light_chunks.append(c)
                            seen_files.add(c["file"])
            if light_chunks:
                print(f"   [Glob] {len(light_chunks)} total after glob: {[c['file'] for c in light_chunks]}")

        # 3c: Grep for top keywords in file contents
        if len(light_chunks) < 8 and expansion_keywords:
            _searcher = ctx.searcher if ctx else SearchTool()
            for kw in expansion_keywords[:3]:
                if len(kw) < 3:
                    continue
                results = _searcher.search_and_chunk(kw, search_path)
                for chunk in results[:5]:
                    if chunk["file"] not in seen_files:
                        light_chunks.append(chunk)
                        seen_files.add(chunk["file"])
                    if len(light_chunks) >= 10:
                        break
                if len(light_chunks) >= 10:
                    break
            print(f"   [Grep] {len(light_chunks)} total after grep")

        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'done'})}")
        print(f"   [Quick Search] Found {len(light_chunks)} chunk(s). Identifying targeted files...")

        # ── Step 4: Targeted file identification (always runs) ────────
        print("[Step 4/6] Identifying targeted files...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'running'})}")

        targeted_files = llm.identify_relevant_files(
            query_to_use, project_structure, symbol_minimap=None
        )
        targeted_chunks: List[Dict] = []
        if targeted_files:
            targeted_chunks = retrieve_local_files(search_path, targeted_files)
            for c in targeted_chunks:
                if c["file"] not in seen_files:
                    light_chunks.append(c)
                    seen_files.add(c["file"])
            print(f"   [Targeted] Retrieved {len(targeted_chunks)} file(s): {[c['file'] for c in targeted_chunks]}")

        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'done'})}")

        # ── Step 5: Deep search (only if still insufficient) ──────────
        if len(light_chunks) < 8:
            print(f"[Step 5/6] Deep search ({len(light_chunks)} chunks < 8)...")
            print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'running'})}")

            _searcher = ctx.searcher if ctx else SearchTool()
            queries = llm.generate_search_queries(
                query_to_use, tool="ripgrep",
                project_context=project_context,
                file_structure=project_structure,
            )
            if expansion_keywords:
                queries = expansion_keywords[:3] + queries
            keyword_chunks: List[Dict] = []
            for q in queries[:5]:
                results = _searcher.search_and_chunk(q, search_path)
                keyword_chunks.extend(results[:20])

            deduped_keyword: List[Dict] = []
            for chunk in keyword_chunks:
                if chunk["file"] not in seen_files:
                    deduped_keyword.append(chunk)
                    seen_files.add(chunk["file"])

            all_found = list(light_chunks) + deduped_keyword
            if len(all_found) >= 3:
                if len(deduped_keyword) > 5:
                    reranker = ctx.reranker if ctx else CrossEncoderReranker()
                    deduped_keyword = reranker.rerank(
                        query_to_use, deduped_keyword,
                        top_k=max(8 - len(light_chunks), 3),
                    )
                top_chunks = list(light_chunks) + deduped_keyword
            else:
                print(f"   [Fallback] Only {len(all_found)} chunks. Running full search index...")
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

                top_chunks = list(light_chunks)
                top_seen = {c["file"] for c in top_chunks}
                for chunk in search_chunks:
                    if chunk["file"] not in top_seen:
                        top_chunks.append(chunk)
                        top_seen.add(chunk["file"])

            print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'done'})}")
        else:
            print(f"   [Quick+Targeted] Sufficient context ({len(light_chunks)} chunks). Skipping deep search.")
            top_chunks = light_chunks
            print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'done'})}")

        print(f"   Final context: {len(top_chunks)} chunks")

    # Step 6: Agentic Action Loop
    print("[Step 6/6] Agentic Action Loop (Synthesizing/Editing)...")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'done'})}")
    print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 6, 'status': 'running'})}")
    initial_context = "\n\n---\n\n".join(c["content"] for c in top_chunks)

    loop_iterations = 15 if is_action_request else 8

    answer = execute_action_loop(
        question=question,
        initial_context=initial_context,
        llm=llm,
        tools=tools,
        available_tools=available_tools,
        project_structure=project_structure,
        history=history,
        max_iterations=loop_iterations,
    )
    return answer, initial_context
