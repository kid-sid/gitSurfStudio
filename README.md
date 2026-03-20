# 🌊 GitSurf Studio

**The blazing-fast, lightweight AI-powered IDE for autonomous codebase evolution.**

GitSurf Studio transforms the power of the GitSurf AI engine into a professional, native desktop experience. Built with a focus on speed, efficiency, and deep codebase reasoning, it allows you to explore any local folder or GitHub repository with a persistent AI assistant.

---

## ✨ Key Features

- 🚀 **Zero Cold-Start AI**: Persistent FastAPI daemon keeps models and search indexes ready in RAM for instant responses.
- 🤖 **Upgraded PRAR Engine**: A "Perceive-Reason-Act-Reflect" agent with enhanced prompt engineering for precise tool usage and target file prediction.
- 📂 **Multi-Tab Support**: Seamlessly switch between multiple files with a modern VS Code-inspired tabbed interface.
- 🛡️ **Safe File Editing**: Robust `replace_in_file` tool with occurrence validation and automatic `.bak` backups to prevent accidental code loss.
- 🧩 **Global Project Context**: Automatic README analysis during initialization to ground AI reasoning in your project's specific jargon and architecture.
- 🐚 **Ultra-Lightweight Frontend**: Built with **Tauri** and **Svelte 5** (Runes) for a native feel with minimal memory footprint.

---

## 🏗️ Architecture

GitSurf Studio follows a **Thin Client, Smart Backend** monorepo structure:

```text
gitSurfStudio/
├── engine/              # Python AI Backend (FastAPI + PRAR Pipeline)
│   ├── src/             # Core logic: orchestrator, llm_client, tools
│   ├── .cache/          # Cloned GitHub repositories
│   ├── server.py        # The AI daemon (REST + JSON Streaming)
│   └── .env             # API Configuration (OpenAI, GitHub)
│
└── app/                 # Native Desktop Frontend
    ├── src/             # Svelte 5 UI (App, FileTree, ChatPanel, Editor)
    └── src-tauri/       # Rust native shell & Sidecar configuration
```

---

## 🚀 Getting Started

### 1. Prerequisites
- **Python 3.10+** (for the AI Engine)
- **Node.js 18+** (for the Frontend)
- **Rust** (for Tauri native build)

### 2. Configure the Engine
Navigate to the `engine` directory and set up your environment:
```powershell
cd engine
pip install -r requirements.txt
# Create a .env file with your keys:
# OPENAI_API_KEY=sk-...
# GITHUB_TOKEN=github_pat_...
```

### 3. Launch GitSurf Studio
In one terminal, start the AI Engine:
```powershell
cd engine
uvicorn server:app --host 127.0.0.1 --port 8002
```

In another terminal, launch the Desktop App:
```powershell
cd app
npm install
npm run tauri dev
```

### 4. 🐳 Run with Docker (Recommended)

**First-time setup:**

On **Windows (PowerShell):**
```powershell
# 1. Create your .env from the template
Copy-Item .env.example .env

# 2. Open and fill in your real API keys (at minimum: OPENAI_API_KEY)
notepad .env

# 3. Build and start
docker compose up --build
```

On **Mac / Linux:**
```bash
cp .env.example .env
nano .env   # add your real OPENAI_API_KEY
docker compose up --build
```

**Day-to-day (images already built):**
```powershell
docker compose up
docker compose down
docker compose restart
```

**Rebuild only the backend** (e.g. after a dependency change):
```powershell
docker compose up --build backend
```

**View live logs:**
```powershell
docker compose logs -f backend
docker compose logs -f frontend
```

| Service | URL |
|---------|-----|
| 🌐 Frontend (Svelte UI) | http://localhost |
| ⚙️ Backend (AI Engine) | http://localhost:8002 |
| ❤️ Health check | http://localhost:8002/health |

> **Minimum required:** Only `OPENAI_API_KEY` is needed to start. `GITHUB_TOKEN`, `GITHUB_CLIENT_ID/SECRET`, and `TAVILY_API_KEY` are optional.

---

## 🛠️ Technology Stack
- **Frontend**: [Svelte 5](https://svelte.dev/) (Runes), [Tauri](https://tauri.app/), [Vite](https://vitejs.dev/)
- **Backend**: [FastAPI](https://fastapi.tiangolo.com/), [Uvicorn](https://www.uvicorn.org/)
- **AI Core**: [OpenAI GPT-4o](https://openai.com/), [Ripgrep](https://github.com/BurntSushi/ripgrep), [BM25/FAISS](https://github.com/facebookresearch/faiss)

## 📄 License
MIT License. Built with 🌊 by Sidhartha.
