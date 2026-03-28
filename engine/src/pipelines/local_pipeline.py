"""
Local file system pipeline — skeleton-first search for local workspaces.
"""

import os
import re
import json
from typing import List, Dict, Optional

from src.tools.search_tool import SearchTool
from src.tools.vector_search_tool import VectorSearchTool
from src.tools.bm25_search_tool import BM25SearchTool
from src.embeddings import EmbeddingClient
from src.reranker import CrossEncoderReranker
from src.pipelines.context import PipelineContext, reciprocal_rank_fusion
from src.pipelines.action_loop import execute_action_loop


# ── Tier-0 fast-path: overview / context questions ─────────────────────────────
_OVERVIEW_PATTERNS = [
    r'\bwhat\s+does\s+(this|the)?\s*(project|codebase|repo|code|app)\s+(do|mean|contain|provide)\b',
    r'\bwhat\s+is\s+(this|the)\s*(project|codebase|repo|code|app)\b',
    r'\b(explain|describe|summarize?|overview\s+of|purpose\s+of)\b.{0,60}\b(project|codebase|repo|code|app|architecture)\b',
    r'\b(explain|describe)\s+(the\s+)?(main\s+)?(architecture|structure|design|overview|features?)\b',
    r'\b(project|codebase|repo|app)\s+(overview|summary|purpose|goal)\b',
    r'\btell\s+me\s+about\s+(this\s+)?(project|codebase|repo|app)\b',
    r'\bhow\s+does\s+(this\s+)?(project|app|code|tool)\s+work\b',
    r'\bwhat\s+can\s+(this|the)\s+(app|project|tool)\s+do\b',
    r'\bmain\s+(purpose|goal|objective|functionality|feature)\b',
    r'\barchitecture\s+of\s+(this|the)\s*(project|code|app|system)\b',
    r'\bhow\s+(to|do\s+i|can\s+i)\s+(run|start|launch|execute|install|setup|set\s+up|use|get\s+started|deploy|build)\b',
    r'\b(steps?|instructions?|guide|way)\s+(to|for)\s+(run|start|install|setup|use|deploy|build)\b',
    r'\bhow\s+do\s+i\s+(get\s+started|run\s+it|install|set\s+this\s+up)\b',
    r'\b(run|start|install|setup|launch)\s+(this\s+)?(project|app|code|repo|locally)\b',
    r'\brun\s+(the\s+)?(project|app|code|server)\s+(locally|now)?\b',
    r'\bget\s+(this\s+)?(running|started|working)\b',
    r'^\s*(what is this|what does this do|explain this|overview|how to run|how to use|getting started)\s*\??$',
]
_OVERVIEW_RE = [re.compile(p, re.IGNORECASE) for p in _OVERVIEW_PATTERNS]

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
    max_chars_per_file: int = 100000,
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

    # ── Tier-0: Overview fast-path ────────────────────────────────────────────
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
                max_iterations=3,
            )
            return answer, initial_context
        print("   [Tier-0] No README or project docs found. Falling through to full pipeline.")

    # Step 1: Build local file tree
    print("[Step 1/6] Building local file tree...")

    local_plan = {
        "goal": question,
        "complexity": "moderate",
        "steps": [
            {"id": 1, "description": "Building local file tree", "status": "pending"},
            {"id": 2, "description": "Refining Query", "status": "pending"},
            {"id": 3, "description": "Skeleton Analysis", "status": "pending"},
            {"id": 4, "description": "Targeted File Retrieval", "status": "pending"},
            {"id": 5, "description": "Keyword Search (Ripgrep)", "status": "pending"},
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
    is_overview_question = refined_data.get("is_overview_question", False)
    direct_tool_call = refined_data.get("direct_tool_call")
    print(f"   Refined to: \"{query_to_use}\"")
    print(f"   Keywords: {expansion_keywords[:5]}")

    # ── Tier-1: LLM-classified overview fast-path ─────────────────────────────
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
                max_iterations=3,
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
        # Step 3: Skeleton Analysis
        print("[Step 3/6] Skeleton Analysis (identifying relevant files)...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 2, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'running'})}")
        targeted_files = llm.identify_relevant_files(
            query_to_use, project_structure, symbol_minimap=None
        )

        # Step 4: Targeted Retrieval
        print("[Step 4/6] Targeted File Retrieval...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 3, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'running'})}")
        targeted_chunks: List[Dict] = []
        if targeted_files:
            targeted_chunks = retrieve_local_files(search_path, targeted_files)
            print(f"   Retrieved {len(targeted_chunks)} targeted file(s)")
        else:
            print("   No targeted files identified")

        # Step 5: Ripgrep
        print("[Step 5/6] Keyword Search (Ripgrep)...")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 4, 'status': 'done'})}")
        print(f"[UI_COMMAND] agent_step {json.dumps({'step_id': 5, 'status': 'running'})}")
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

        if len(top_chunks) >= 3:
            print(f"   [Smart-Route] Sufficient context ({len(top_chunks)} chunks). Skipping full embedding index.")
            if len(top_chunks) > 5:
                reranker = ctx.reranker if ctx else CrossEncoderReranker()
                top_chunks = (
                    list(targeted_chunks)
                    + reranker.rerank(query_to_use, keyword_chunks, top_k=max(5 - len(targeted_chunks), 2))
                )
        else:
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

            for chunk in search_chunks:
                if chunk["file"] not in seen_files:
                    top_chunks.append(chunk)
                    seen_files.add(chunk["file"])

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
