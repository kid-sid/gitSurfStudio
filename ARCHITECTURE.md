# Architecture: GitSurf Studio

This document describes the system architecture, data flows, and key design decisions of GitSurf Studio.

---

## System Overview

GitSurf Studio follows a **Thin Client, Smart Backend** architecture. The Svelte/Tauri frontend handles display and user interaction; all AI reasoning, file operations, and tool execution happen in the Python backend.

```
┌─────────────────────────────────────────────────────────────┐
│  Svelte 5 / Tauri Frontend  (app/)                          │
│  Monaco Editor · FileTree · ChatPanel · GitPanel · Terminal  │
└────────────────────────┬────────────────────────────────────┘
                         │  REST + NDJSON streaming (SSE)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI Engine  (engine/server.py : port 8002)             │
│  Global EngineState · Tool Registry · MCP Manager           │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│  PRAR Orchestrator  (engine/src/orchestrator.py)            │
│  run_local_pipeline · run_code_aware_pipeline               │
│  execute_action_loop (up to 8 ReAct iterations)             │
└──────────┬───────────────────────────────────┬──────────────┘
           │                                   │
    Built-in Tools                        MCP Tools
    ──────────────                        ─────────
    FileEditorTool                        mcp__playwright__*
    GitTool                               mcp__context7__*
    SearchTool (ripgrep)                  mcp__sequential-thinking__*
    WebSearchTool (Tavily)
    SymbolPeekerTool
    LintTool
```

---

## Core Components

### 1. Frontend (`app/`)

| Component | Purpose |
|---|---|
| `App.svelte` | Root layout, workspace init, MCP status polling, auth gate |
| `ChatPanel.svelte` | Streaming AI chat, session management, @mention file injection |
| `CodeEditor.svelte` | Monaco editor, multi-tab, diff view, inline completions, linting decorations |
| `FileTree.svelte` | Hierarchical workspace browser |
| `GitPanel.svelte` | Stage, commit, branch management |
| `StatusBar.svelte` | Engine status, workspace name, MCP readiness indicator |
| `TerminalPanel.svelte` | xterm.js terminal (Ctrl+` to toggle) |
| `SymbolBrowser.svelte` | Class/function tree with click-to-navigate |
| `lib/api.js` | All engine API calls (fetch + SSE streaming) |
| `lib/supabase.js` | Auth state, workspace persistence, recent repos |

**Streaming protocol** — `/chat` returns NDJSON lines:
```
{"type": "log", "content": "..."}          ← pipeline status updates
{"type": "ui_command", "command": "...", "args": "..."}
{"type": "answer_token", "content": "tok"} ← progressive streaming
{"type": "answer", "content": "..."}       ← fallback full answer
```

---

### 2. Engine State (`engine/server.py`)

A single global `EngineState` singleton, initialized lazily on first `/init` call:

```python
state.llm            # LLMClient (fast + reasoning models)
state.pipeline_ctx   # PipelineContext (FAISS, BM25, reranker — lazy)
state.agent_tools    # Dict[str, tool] — built-in + MCP proxies merged
state.git_tool       # GitTool for direct REST endpoints
state.history        # HistoryManager (local JSON)
state.chat_memory    # ChatMemory (Supabase-backed)
state.mcp_manager    # MCPClientManager
state.mcp_ready      # bool — set true when MCP servers finish connecting
state.available_tools # AVAILABLE_TOOLS string + MCP tool descriptions
```

`_ensure_initialized()` registers built-in tools synchronously, then fires `_init_mcp_background()` in a daemon thread so `/init` returns immediately (< 2s). MCP servers connect in parallel via `asyncio.gather`.

---

### 3. PRAR Orchestrator (`engine/src/orchestrator.py`)

Two pipeline entry points share a common action loop:

#### Local Pipeline (6 steps)
1. **Tier-0/1 fast-path** — detect project overview questions, answer from README in 1 iteration
2. Build local file tree (up to 500 files)
3. Query refinement — LLM classifies intent, extracts keywords, detects action requests
4. Skeleton analysis — LLM identifies 3–8 relevant files
5. Targeted retrieval — read only those files directly
6. Keyword search — ripgrep; fallback to FAISS + BM25 if < 3 chunks found
7. **Action loop** (up to 8 iterations)

#### GitHub Pipeline (8 steps)
Extends local with symbol extraction, call graph construction, triple-hybrid search (Vector + BM25 + Ripgrep merged with RRF), and cross-encoder reranking.

#### Action Loop (`execute_action_loop`)
Each iteration:
1. Build context string (capped at 80k chars, drops oldest logs first)
2. LLM `decide_action()` → JSON `{"action": "tool_call"|"final_answer", ...}`
3. Dispatch via `getattr(tool_instance, method)(**kwargs)`
4. Append observation; loop or stream final answer

**Max iterations raised to 8** to accommodate MCP tool workflows (e.g. Context7 requires resolve + fetch = 2 calls minimum).

---

### 4. MCP Integration (`engine/src/mcp/`)

#### Architecture
```
MCPClientManager
  ├── _ServerConnection("playwright")   → npx @playwright/mcp
  ├── _ServerConnection("context7")     → npx @upstash/context7-mcp
  └── _ServerConnection("sequential-thinking") → npx @modelcontextprotocol/server-sequential-thinking

MCPToolProxy (one per discovered tool)
  .execute(**kwargs) → manager.call_tool(server, tool, kwargs)
  .__getattr__(name) → routes any method name through execute()
```

#### Key design decisions

**Background init** — `MCPClientManager.initialize()` runs in a daemon thread after `/init` returns. This keeps workspace init latency under 2s even though npx startup takes 3–5s. The frontend polls `/mcp/status` and shows a loading indicator until `mcp_ready = True`.

**Parallel connection** — all MCP servers connect simultaneously via `asyncio.gather` inside a dedicated background event loop thread. Reduces 3-server startup from ~15s sequential to ~5s parallel.

**Unified dispatch** — `MCPToolProxy` exposes an `execute()` method so MCP tools slot into the same `getattr(tool, method)(**kwargs)` dispatch the orchestrator already uses for built-in tools. No orchestrator changes needed. `__getattr__` fallback accepts any method name the LLM might use.

**Tool naming** — each MCP tool is registered under both its full key (`mcp__playwright__browser_navigate`) and a shorthand alias (`browser_navigate`). The prompt enforces `"method": "execute"` for all MCP tools.

**Rich schema hints** — parameter descriptions in `available_tools` mark required args with `*` and optional with `?`, include enum values, and truncated descriptions so the LLM constructs valid payloads.

#### MCP Tool Routing (in `decide_action_prompt`)
- **Context7** — library/framework API questions; falls back to `WebSearchTool` if not yet available
- **Playwright** — URL navigation, screenshots, JS-rendered scraping, UI testing
- **Sequential Thinking** — multi-file planning, architecture design, complex debugging

---

### 5. Tool System (`engine/src/tools/`)

| Tool | Key methods |
|---|---|
| `FileEditorTool` | `read_file`, `write_file`, `replace_in_file`, `delete_file` — signals UI via stdout |
| `GitTool` | `get_status`, `stage_files`, `commit`, `checkout_branch`, `get_diff` — uses GitPython |
| `SearchTool` | `search`, `search_and_chunk` — ripgrep `--json` wrapper |
| `WebSearchTool` | `search` (Tavily), `fetch_url` (BeautifulSoup) |
| `SymbolPeekerTool` | `peek_symbol` — F12 Go-to-Definition, AST-based |
| `LintTool` | Python (ruff via stdin), JS/TS (eslint via stdin) — content-hash cache |

---

### 6. Search Pipeline

Hybrid search merges three sources with **Reciprocal Rank Fusion (RRF)**:

```
Vector (FAISS)  ──┐
BM25            ──┼──► RRF merge ──► Cross-Encoder Reranker ──► top-k chunks
Ripgrep         ──┘
```

- **Vector index**: FAISS over code chunks; sentence-transformers locally or OpenAI embeddings (`EMBEDDING_PROVIDER=openai`)
- **BM25**: NLTK-based statistical search; index cached as `bm25_index.joblib`
- **RRF score**: `1 / (k + rank + 1)` where k = 60
- **Reranker**: `cross-encoder/ms-marco-MiniLM-L-6-v2` — final re-scores before action loop

In the local pipeline, if skeleton analysis returns ≥ 3 targeted chunks, FAISS/BM25 are skipped entirely (smart routing).

---

### 7. LLM Client (`engine/src/llm_client.py`)

Dual-model strategy:

| Model | Role |
|---|---|
| `gpt-4o-mini` (fast) | Query refinement, intent classification, search query generation |
| `gpt-4o` (reasoning) | Action loop decisions, final answer generation |

All prompts live in `engine/src/prompts.py`. LLM responses are always parsed as JSON with a regex fallback extractor. Final answers are streamed token-by-token via stdout `[ANSWER_TOKEN]` prefix → captured by server → forwarded as SSE.

---

### 8. Persistent Memory (`engine/src/memory/`)

| Component | Backend | Purpose |
|---|---|---|
| `SupabaseMemory` | Supabase | Symbol graphs + call graphs — cached by commit SHA to skip re-indexing |
| `ChatMemory` | Supabase | Multi-session conversations with rolling LLM summarization (keeps last 6 messages verbatim) |
| `HistoryManager` | Local JSON | In-process session history fallback (`.gitsurf_history.json`) |

All Supabase writes are fire-and-forget background threads — never block the pipeline.

---

## Key API Endpoints

| Endpoint | Purpose |
|---|---|
| `POST /init` | Initialize workspace, start MCP background init |
| `POST /chat` | Streaming PRAR pipeline (NDJSON/SSE) |
| `GET /mcp/status` | MCP readiness + tool list — poll after /init |
| `GET /health` | Engine liveness |
| `POST /lint` | Real-time linting (ruff / eslint) |
| `POST /complete` | Inline code completion |
| `GET /peek-symbol` | F12 Go-to-Definition |
| `GET/POST /git/*` | Status, stage, commit, branch, diff |
| `GET/POST /chat/sessions*` | Session CRUD + message history |
