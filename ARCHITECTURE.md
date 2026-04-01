# Architecture: GitSurf Studio

This document describes the system architecture, data flows, and key design decisions of GitSurf Studio.

---

## System Overview

GitSurf Studio follows a **Thin Client, Smart Backend** architecture. The Svelte/Tauri frontend handles display and user interaction; all AI reasoning, file operations, and tool execution happen in the Python backend.

```
┌────────────────────────────────────────────────────────────────────┐
│  Svelte 5 / Tauri Frontend  (app/)                                 │
│  Monaco Editor · FileTree · ChatPanel · GitPanel · TerminalPanel   │
│  AgentProgress · DiffOverlay · PreviewPanel · ChangeReview         │
└───────────────────────────┬────────────────────────────────────────┘
                            │  REST + NDJSON streaming (SSE) + WebSocket
                            ▼
┌────────────────────────────────────────────────────────────────────┐
│  FastAPI Engine  (engine/server.py : port 8002)                    │
│  Route modules: chat · git · workspace · agent · auth              │
│                 lint · terminal · watcher · preview · cache        │
└───────────────────────────┬────────────────────────────────────────┘
                            │
                  ┌─────────┴─────────┐
                  ▼                   ▼
     ┌────────────────────┐  ┌─────────────────────────────┐
     │  EngineState       │  │  PRAR Orchestrator           │
     │  (engine_state.py) │  │  (orchestrator.py)           │
     │  llm · pipeline_ctx│  │  run_local_pipeline          │
     │  agent_tools · mcp │  │  run_code_aware_pipeline     │
     │  cache_manager     │  │  run_agent_pipeline          │
     │  repo_manager      │  │  execute_action_loop         │
     └────────────────────┘  │  (5–25 ReAct iterations)     │
                             └──────────┬──────────────────┘
                                        │
                           ┌────────────┴────────────┐
                     Built-in Tools             MCP Tools
                     ──────────────             ─────────
                     FileEditorTool             mcp__playwright__*
                     FindByNameTool             mcp__context7__*
                     GitTool                    mcp__sequential-thinking__*
                     SearchTool (ripgrep)
                     WebSearchTool (Tavily)
                     SymbolPeekerTool
                     BrowserTool (Playwright)
                     TerminalTool
                     NotifyUserTool
```

---

## Core Components

### 1. Frontend (`app/`)

| Component | Purpose |
|---|---|
| `App.svelte` | Root layout, workspace init, MCP status polling, auth gate |
| `AuthPage.svelte` | GitHub OAuth login UI |
| `ChatPanel.svelte` | Streaming AI chat, session management, @mention file injection |
| `ChatInput.svelte` | User message input with @mention support |
| `ChatMessage.svelte` | Individual message rendering (markdown, code blocks) |
| `SessionList.svelte` | Chat session history list |
| `CodeEditor.svelte` | Monaco editor, multi-tab, diff view, inline completions, linting decorations |
| `EditorTabBar.svelte` | File tab management |
| `DiffOverlay.svelte` | Side-by-side diff viewer |
| `FileTree.svelte` | Hierarchical workspace browser |
| `GitPanel.svelte` | Stage, commit, branch management |
| `ChangeReview.svelte` | Review AI changesets before accepting |
| `AgentProgress.svelte` | Agent step-by-step progress display |
| `StatusBar.svelte` | Engine status, workspace name, MCP readiness indicator |
| `TerminalPanel.svelte` | xterm.js terminal (Ctrl+` to toggle) |
| `PreviewPanel.svelte` | Dev server preview (iframe) |
| `SymbolBrowser.svelte` | Class/function tree with click-to-navigate |
| `CommandPalette.svelte` | Ctrl+K command palette |
| `ForkButton.svelte` | GitHub fork button |
| `lib/api.js` | All engine API calls (fetch + SSE streaming) |
| `lib/supabase.js` | Auth state, workspace persistence, recent repos |
| `lib/fileWatcher.js` | WebSocket file-system event listener |
| `lib/markdown.js` | Markdown rendering utilities |

**Streaming protocol** — `/chat` returns NDJSON lines:
```
{"type": "log", "content": "..."}          ← pipeline status updates
{"type": "ui_command", "command": "...", "args": "..."}
{"type": "answer_token", "content": "tok"} ← progressive streaming
{"type": "answer", "content": "..."}       ← fallback full answer
```

---

### 2. Engine State (`engine/src/engine_state.py`)

A single global `EngineState` singleton defined in `engine/src/engine_state.py`, initialized lazily on first `/init` call. `server.py` is now a thin assembly module that imports routes and registers middleware.

```python
state.llm             # LLMClient (fast + reasoning models)
state.pipeline_ctx    # PipelineContext (FAISS, BM25, reranker — lazy)
state.agent_tools     # Dict[str, tool] — built-in + MCP proxies merged
state.git_tool        # GitTool for direct REST endpoints
state.history         # HistoryManager (local JSON fallback)
state.chat_memory     # ChatMemory (Supabase-backed rolling summarization)
state.supabase_memory # SupabaseMemory (symbol graphs, call graphs by commit SHA)
state.mcp_manager     # MCPClientManager
state.mcp_ready       # bool — set true when MCP servers finish connecting
state.available_tools # AVAILABLE_TOOLS string + MCP tool descriptions
state.repo_manager    # RepoManager (clone/sync GitHub repos)
state.cache_manager   # CacheManager (evict old repos, clean search indexes)
state.symbol_extractor # SymbolExtractor (AST-based, all languages)
state.terminal_tool   # TerminalTool (allowlist-enforced shell)
state.active_changesets # Dict[id → changeset] for rollback
state.active_executor   # Current AgentExecutor (cancel/respond target)
state.prompt_guard    # PromptGuard (injection detection)
state.topic_guard     # TopicGuard (off-topic request filter)
state.workspace_path  # Absolute path to current workspace
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
7. **Action loop** — **8 iterations** for Q&A, **15 iterations** for action requests (`is_action_request=True`)

#### GitHub Pipeline (8 steps)
Extends local with symbol extraction, call graph construction, triple-hybrid search (Vector + BM25 + Ripgrep merged with RRF), and cross-encoder reranking.

#### Action Loop (`execute_action_loop`)
Each iteration:
1. Build context string (capped at 80k chars, drops oldest logs first)
2. LLM `decide_action()` → JSON `{"action": "tool_call"|"final_answer", ...}`
3. Dispatch via `getattr(tool_instance, method)(**kwargs)`
4. Append observation; loop or stream final answer

**Iteration budgets by mode:**
| Mode | Max iterations |
|---|---|
| Q&A requests | 8 |
| Action requests (`is_action_request=True`) | 15 |
| Agent mode — short plan (≤ 2 steps) | 8 |
| Agent mode — medium plan (≤ 5 steps) | 15 |
| Agent mode — long plan (> 5 steps) | 25 |
| Default / fallback | 5 |

**LoopGuard** prevents repetition: exact duplicate calls (same tool + method + args) are blocked after 3 occurrences (hard stop at 4). Method-level repeats are blocked after 5 (hard stop at 7). `read_file`, `search`, and MCP auto-chain targets are exempt from method counting.

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
| `FileEditorTool` | `read_file`, `write_file`, `replace_in_file`, `multi_replace_file_content`, `delete_file` — signals UI via stdout |
| `FindByNameTool` | `find_by_name` — glob pattern file search |
| `ListFilesTool` | `list_dir`, `list_recursive` — directory listing |
| `GitTool` | `get_status`, `stage_files`, `commit`, `checkout_branch`, `get_diff`, `stash_changes` — uses GitPython |
| `SearchTool` | `search`, `search_and_chunk` — ripgrep `--json` wrapper |
| `WebSearchTool` | `search` (Tavily), `fetch_url` (BeautifulSoup), `fetch_docs` |
| `SymbolPeekerTool` | `peek_symbol` — F12 Go-to-Definition, AST-based |
| `BrowserTool` | `verify_page`, `test_interaction`, `debug_page`, `scrape_rendered` — high-level Playwright wrapper |
| `TerminalTool` | `run_command`, `run_test`, `run_lint` — allowlist-enforced shell execution |
| `NotifyUserTool` | `notify_user` — pause agent execution and request human input |
| `EditorUITool` | `open_file` — open file in IDE tab |
| `LintTool` | Python (ruff via stdin), JS/TS/Svelte (eslint via stdin) — content-hash cache; used internally by AgentExecutor |

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
| `RedisSessionMemory` | Redis (optional) | Redis-backed session persistence for multi-instance deployments |

All Supabase writes are fire-and-forget background threads — never block the pipeline.

---

## Key API Endpoints

### Workspace & Health
| Endpoint | Purpose |
|---|---|
| `GET /health` | Engine liveness |
| `GET /mcp/status` | MCP readiness + tool list — poll after /init |
| `POST /init` | Initialize workspace (local or GitHub), start MCP background init |
| `GET /files` | List workspace file tree |
| `GET /read` | Read file content |
| `POST /write` | Write / create file |
| `POST /rename` | Rename file or directory |
| `POST /delete-file` | Delete file |
| `POST /delete-dir` | Delete directory |
| `POST /mkdir` | Create directory |
| `POST /restore` | Restore from `.bak` backup |
| `POST /index` | Rebuild search indexes (FAISS + BM25) |

### Chat
| Endpoint | Purpose |
|---|---|
| `POST /chat` | Streaming PRAR pipeline (NDJSON/SSE) |
| `POST /autocomplete` | Token autocomplete |
| `POST /complete` | Inline code completion |
| `POST /chat/sessions` | Create chat session |
| `GET /chat/sessions` | List sessions for user + repo |
| `GET /chat/sessions/{id}/messages` | Fetch session message history |
| `DELETE /chat/sessions/{id}` | Delete session |

### Git
| Endpoint | Purpose |
|---|---|
| `GET /status` | Staged/unstaged changes |
| `POST /stage` | Stage files |
| `POST /commit` | Commit staged changes |
| `GET /branch` | Current branch + branch list |
| `POST /checkout` | Checkout / create branch |
| `POST /stash` | Stash changes |
| `POST /discard` | Discard file changes |
| `POST /fork` | Fork GitHub repository |
| `GET /diff-lines` | Diff by line range |

### Agent
| Endpoint | Purpose |
|---|---|
| `POST /agent/rollback` | Roll back a changeset |
| `POST /agent/accept` | Accept a changeset |
| `POST /agent/cancel` | Cancel running agent |
| `POST /agent/respond` | Send human response to blocked agent (NotifyUser) |
| `GET /agent/changesets` | List active changesets |

### Developer Tools
| Endpoint | Purpose |
|---|---|
| `GET /symbols` | Extract symbols from file |
| `GET /peek-symbol` | F12 Go-to-Definition |
| `POST /lint` | Real-time linting (ruff / eslint) |
| `POST /fix-lint` | Auto-fix lint issues |
| `GET /auth/status` | GitHub OAuth status |
| `GET /auth/login` | Initiate OAuth flow |
| `GET /auth/callback` | OAuth callback |
| `WS /terminal` | Interactive xterm.js terminal |
| `WS /watch` | File system watcher events |
| `POST /start` | Start dev-server preview |
| `POST /stop` | Stop dev-server preview |
| `GET /cache/status` | Cached repo + index info |
| `POST /cache/cleanup` | Evict old cached repositories |
