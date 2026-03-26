# 🌊 GitSurf Studio

**The blazing-fast, lightweight AI-powered IDE for autonomous codebase evolution.**

GitSurf Studio transforms the power of the GitSurf AI engine into a professional, native desktop experience. Built with a focus on speed, efficiency, and deep codebase reasoning, it allows you to explore any local folder or GitHub repository with a persistent AI assistant that can read, edit, search, browse the web, and reason across external tools.

---

## ✨ Key Features

- 🚀 **Zero Cold-Start AI**: Persistent FastAPI daemon keeps models and search indexes ready in RAM for instant responses.
- 🤖 **PRAR Agent Engine**: A "Perceive-Reason-Act-Reflect" loop with up to 8 reasoning iterations, precise tool routing, and target file prediction.
- 🔌 **MCP Tool Integration**: Native Model Context Protocol client — connects to Playwright (browser automation), Context7 (live library docs), and Sequential Thinking (structured reasoning) out of the box. Extensible via `engine/mcp_config.json`.
- 📂 **Multi-Tab Editor**: Monaco Editor with syntax highlighting, inline completions, real-time linting (ruff / eslint), and git gutter indicators.
- 🛡️ **Safe File Editing**: `replace_in_file` with occurrence validation and automatic `.bak` backups; AI diffs shown with Keep/Reject UI.
- 🧩 **Persistent Chat Memory**: Multi-session conversation history per repo stored in Supabase with rolling LLM summarization.
- 🐚 **Ultra-Lightweight Frontend**: Built with **Tauri** and **Svelte 5** (Runes) for a native feel with minimal memory footprint.
- 🔍 **Symbol Navigation**: F12 Go-to-Definition across Python, JS/TS, and C-family files.
- 🖥️ **Integrated Terminal**: xterm.js terminal panel (Ctrl+`) scoped to workspace root.

---

## 🏗️ Architecture

GitSurf Studio follows a **Thin Client, Smart Backend** monorepo structure:

```text
gitSurfStudio/
├── engine/                  # Python AI Backend (FastAPI + PRAR Pipeline)
│   ├── src/
│   │   ├── orchestrator.py  # PRAR pipeline + ReAct action loop
│   │   ├── llm_client.py    # Dual-model LLM abstraction (gpt-4o / gpt-4o-mini)
│   │   ├── prompts.py       # All prompt templates + MCP routing rules
│   │   ├── tools/           # FileEditorTool, GitTool, SearchTool, LintTool, etc.
│   │   ├── mcp/             # MCP client: MCPClientManager + MCPToolProxy
│   │   └── memory/          # Supabase-backed symbol cache + chat sessions
│   ├── mcp_config.json      # MCP server declarations (Playwright, Context7, Sequential Thinking)
│   ├── server.py            # FastAPI server + tool registry + MCP background init
│   └── .env                 # API keys (OpenAI, GitHub, Supabase, Tavily)
│
└── app/                     # Native Desktop Frontend
    ├── src/
    │   ├── App.svelte        # Root layout + workspace init + MCP status polling
    │   ├── lib/api.js        # All engine API calls
    │   ├── lib/supabase.js   # Auth + workspace persistence
    │   └── components/       # ChatPanel, CodeEditor, FileTree, GitPanel, StatusBar, Terminal
    └── src-tauri/            # Rust native shell
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Python 3.10+** (for the AI Engine)
- **Node.js 18+** (for the Frontend and MCP servers)
- **Rust** (for Tauri native build)
- **npx** available in PATH (for MCP servers — included with Node.js)

### 2. Configure the Engine
```bash
cd engine
pip install -r requirements.txt
cp ../.env.example .env
# Edit .env — minimum required: OPENAI_API_KEY
```

### 3. Launch GitSurf Studio
In one terminal, start the AI Engine:
```bash
cd engine
uvicorn server:app --host 127.0.0.1 --port 8002 --reload
```

In another terminal, launch the Desktop App:
```bash
cd app
npm install
npm run tauri dev
```

The MCP servers (Playwright, Context7, Sequential Thinking) start automatically in the background after the first workspace is opened. Watch the status bar for `MCP ready (N tools)`.

### 4. 🐳 Run with Docker (Recommended)

**First-time setup:**

On **Windows (PowerShell):**
```powershell
Copy-Item .env.example .env
notepad .env          # add OPENAI_API_KEY at minimum
docker compose up --build
```

On **Mac / Linux:**
```bash
cp .env.example .env
nano .env
docker compose up --build
```

**Day-to-day:**
```bash
docker compose up
docker compose down
docker compose logs -f backend
```

| Service | URL |
|---------|-----|
| 🌐 Frontend (Svelte UI) | http://localhost |
| ⚙️ Backend (AI Engine) | http://localhost:8002 |
| ❤️ Health check | http://localhost:8002/health |
| 🔌 MCP status | http://localhost:8002/mcp/status |

---

## 🔌 MCP Tools

The agent automatically routes to external MCP tools when appropriate:

| Tool | Triggers when… |
|------|---------------|
| **Context7** | Asking about library/framework APIs or syntax (e.g. "how does $state work in Svelte 5?") |
| **Playwright** | Navigating URLs, taking screenshots, scraping JS-rendered pages, UI testing |
| **Sequential Thinking** | Planning multi-file refactors, debugging across files, architecture design |

To add more MCP servers, edit `engine/mcp_config.json` — no code changes needed.

---

## 🛠️ Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | Yes | LLM reasoning (gpt-4o / gpt-4o-mini) |
| `GITHUB_TOKEN` | No | Repo cloning & git operations |
| `GITHUB_CLIENT_ID` / `GITHUB_CLIENT_SECRET` | No | GitHub OAuth flow |
| `TAVILY_API_KEY` | No | Web search tool |
| `SUPABASE_URL` / `SUPABASE_SERVICE_KEY` | No | Persistent chat memory & symbol cache |
| `EMBEDDING_PROVIDER=openai` | No | Use OpenAI embeddings instead of local sentence-transformers |

---

## 🛠️ Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Svelte 5 (Runes), Tauri, Monaco Editor, xterm.js |
| Backend | FastAPI, Uvicorn, Python 3.10+ |
| AI Core | OpenAI gpt-4o / gpt-4o-mini |
| Search | FAISS, BM25, Ripgrep (hybrid + RRF merge) |
| MCP | Playwright, Context7, Sequential Thinking (via `mcp` SDK) |
| Persistence | Supabase (chat sessions, symbol graphs, workspaces) |

## 📄 License
MIT License. Built with 🌊 by Sidhartha.
