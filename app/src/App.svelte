<script>
  import "./styles.css";
  import { onMount } from "svelte";
  import FileTree from "./components/FileTree.svelte";
  import CodeEditor from "./components/CodeEditor.svelte";
  import ChatPanel from "./components/ChatPanel.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import ForkButton from "./components/ForkButton.svelte";
  import GitPanel from "./components/GitPanel.svelte";
  import SymbolBrowser from "./components/SymbolBrowser.svelte";
  import AuthPage from "./components/AuthPage.svelte";
  import TerminalPanel from "./components/TerminalPanel.svelte";
  import { supabase, saveWorkspace, getRecentWorkspaces, deleteWorkspace } from "./lib/supabase.js";
  import { initWorkspace, checkHealth } from "./lib/api.js";

  const ENGINE_URL = (typeof window !== "undefined" && window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1")
    ? `${window.location.protocol}//${window.location.hostname}:8002`
    : "http://127.0.0.1:8002";

  let activeFile = $state("");
  let openFiles = $state([]);
  let workspacePath = $state("");
  let engineOnline = $state(false);
  let isInitializing = $state(false);
  let initError = $state("");
  let initInput = $state("");
  let isGitHubRepo = $state(false);
  let isAuthenticated = $state(false);
  let activeSidebarView = $state("explorer"); // "explorer" or "git"
  let showTerminal = $state(false);
  let recentWorkspaces = $state([]);
  let mcpReady = $state(false);
  let mcpToolCount = $state(0);

  // Ensure activeFile is always in openFiles
  $effect(() => {
    if (activeFile && !openFiles.includes(activeFile)) {
      openFiles = [...openFiles, activeFile];
    }
  });

  onMount(async () => {
    engineOnline = await checkHealth();

    // Resolve current session on load
    const { data: { session } } = await supabase.auth.getSession();
    isAuthenticated = !!session;

    // Keep auth state in sync (handles OAuth redirects, sign-outs, token refresh)
    supabase.auth.onAuthStateChange((_event, session) => {
      isAuthenticated = !!session;
      if (session) getRecentWorkspaces().then(ws => { recentWorkspaces = ws; }).catch(() => {});
    });

    if (isAuthenticated) {
      recentWorkspaces = await getRecentWorkspaces().catch(() => []);
    }

    // Auto-ping engine every 10 s
    setInterval(async () => {
      engineOnline = await checkHealth();
    }, 10000);

    // Ctrl+` — toggle terminal panel
    window.addEventListener("keydown", (e) => {
      if (e.ctrlKey && e.code === "Backquote") {
        e.preventDefault();
        showTerminal = !showTerminal;
      }
    });

    // Cross-file F12 navigation: open file then scroll to line
    window.addEventListener("navigate-to-file-line", (e) => {
      const { path, line } = e.detail;
      if (!openFiles.includes(path)) openFiles = [...openFiles, path];
      activeFile = path;
      // Give the editor time to mount/swap the model before scrolling
      setTimeout(() => {
        window.dispatchEvent(new CustomEvent("navigate-to-line", { detail: { path, line } }));
      }, 150);
    });
  });

  async function handleSignOut() {
    await supabase.auth.signOut();
    recentWorkspaces = [];
    goHome();
  }

  async function handleDeleteWorkspace(id, event) {
    event.stopPropagation();
    await deleteWorkspace(id);
    recentWorkspaces = recentWorkspaces.filter(w => w.id !== id);
  }

  async function handleInit() {
    if (!initInput.trim() || isInitializing) return;
    
    isInitializing = true;
    initError = "";
    
    try {
      const { data: { user: initUser } } = await supabase.auth.getUser();
      const res = await initWorkspace(initInput.trim(), initUser?.id ?? null);
      workspacePath = res.workspace_path;
      isGitHubRepo = res.is_github;

      // Bug 3: guard against 409 race condition — upsert is idempotent, silence duplicates
      try {
        await saveWorkspace(initInput.trim(), res.is_github);
      } catch (e) {
        if (!e.message?.includes("409") && !e.message?.includes("duplicate")) {
          console.warn("[Workspace] saveWorkspace:", e.message);
        }
      }
      // Small settle delay before reading back
      await new Promise(r => setTimeout(r, 150));
      try { recentWorkspaces = await getRecentWorkspaces(); } catch (_) {}

      // Bug 4D: poll /mcp/status until MCP servers finish initializing
      mcpReady = false;
      mcpToolCount = 0;
      (async function pollMcpStatus() {
        for (let i = 0; i < 20; i++) {
          await new Promise(r => setTimeout(r, 1000));
          try {
            const s = await fetch(`${ENGINE_URL}/mcp/status`).then(r => r.json());
            if (s.ready) { mcpReady = true; mcpToolCount = s.count ?? 0; return; }
          } catch (_) {}
        }
      })();
    } catch (e) {
      initError = e.message;
    } finally {
      isInitializing = false;
    }
  }

  function handleFileSelect(event) {
    const path = event.detail.path;
    if (!openFiles.includes(path)) {
      openFiles = [...openFiles, path];
    }
    activeFile = path;
  }

  function handleWorkspaceOpen(event) {
    // User triggered an open from the file tree
    workspacePath = ""; // Go back to splash screen
    initInput = event.detail.path || "";
    openFiles = [];
    activeFile = "";
  }
  function handleUICommand(command, args) {
    if (command === "open_file") {
      const path = args.trim();
      handleFileSelect({ detail: { path } });
    } else if (command === "file_changed") {
      const path = args.trim();
      // Ensure the file is open, then signal CodeEditor to show diff
      if (!openFiles.includes(path)) {
        openFiles = [...openFiles, path];
        activeFile = path;
      }
      window.dispatchEvent(new CustomEvent("ai-file-changed", { detail: { path } }));
    } else if (command === "file_created") {
      const path = args.trim();
      if (!openFiles.includes(path)) {
        openFiles = [...openFiles, path];
        activeFile = path;
      }
      window.dispatchEvent(new CustomEvent("ai-file-created", { detail: { path } }));
    } else if (command === "ai_writing_start") {
      const path = args.trim();
      if (!openFiles.includes(path)) {
        openFiles = [...openFiles, path];
        activeFile = path;
      }
      window.dispatchEvent(new CustomEvent("ai-writing-start", { detail: { path } }));
    }
  }

  function goHome() {
    workspacePath = "";
    activeFile = "";
    openFiles = [];
    isGitHubRepo = false;
    initInput = "";
  }
</script>

<div class="studio-layout">
  {#if !isAuthenticated}
    <!-- Auth gate — shown before anything else -->
    <AuthPage />
  {:else}

  <!-- Title Bar -->
  <header class="title-bar">
    <div class="title-bar__left">
      <span class="title-bar__logo">🌊</span>
      <span class="title-bar__name">GitSurf Studio</span>
    </div>
    <div class="title-bar__center">
      {#if workspacePath}
        <button class="home-btn" onclick={goHome} title="Go to Home">🏠</button>
      {/if}
      <span class="title-bar__file">{activeFile || workspacePath || "Welcome"}</span>
    </div>
    <div class="title-bar__right">
      <span class="engine-badge" class:online={engineOnline}>
        ● {engineOnline ? "Engine Connected" : "Engine Offline"}
      </span>
      {#if workspacePath}
        <button
          class="terminal-toggle-btn"
          class:active={showTerminal}
          onclick={() => showTerminal = !showTerminal}
          title="Toggle Terminal (Ctrl+`)"
        >&gt;_</button>
      {/if}
      {#if workspacePath && isGitHubRepo}
        <ForkButton {workspacePath} isGitHub={isGitHubRepo} />
      {/if}
      <button class="signout-btn" onclick={handleSignOut} title="Sign Out">Sign Out</button>
    </div>
  </header>

  {#if !workspacePath}
    <!-- Startup / Welcome Screen -->
    <div class="welcome-screen">
      <div class="welcome-card">
        <div class="welcome-logo">🌊</div>
        <h1>GitSurf Studio</h1>
        <p>The AI-native IDE for understanding and evolving codebases.</p>
        
        <div class="init-box">
          <input 
            type="text" 
            placeholder="Search path (e.g. C:\project) or GitHub URL" 
            bind:value={initInput}
            onkeydown={(e) => e.key === 'Enter' && handleInit()}
            disabled={isInitializing}
          />
          <button onclick={handleInit} disabled={isInitializing || !initInput.trim()}>
            {isInitializing ? 'Initializing...' : 'Open Project'}
          </button>
        </div>
        
        {#if initError}
          <div class="init-error">⚠️ {initError}</div>
        {/if}

        <div class="welcome-footer">
          <span>Tip: You can paste a full GitHub repo link.</span>
        </div>

        {#if recentWorkspaces.length > 0}
          <div class="recent-workspaces">
            <p class="recent-title">Recent</p>
            {#each recentWorkspaces as ws}
              <div
                class="recent-item"
                onclick={() => { initInput = ws.path; handleInit(); }}
                onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") { initInput = ws.path; handleInit(); } }}
                role="button"
                tabindex="0"
              >
                <span class="recent-icon">{ws.is_github ? "🐙" : "📁"}</span>
                <div class="recent-info">
                  <span class="recent-name">{ws.name}</span>
                  <span class="recent-path">{ws.path}</span>
                </div>
                <button
                  class="recent-delete"
                  onclick={(e) => handleDeleteWorkspace(ws.id, e)}
                  title="Remove"
                >✕</button>
              </div>
            {/each}
          </div>
        {/if}
      </div>
    </div>
  {:else}
    <!-- Main IDE Content -->
    <div class="main-content">
      <nav class="activity-bar">
        <button 
          class="activity-btn" 
          class:active={activeSidebarView === 'explorer'} 
          onclick={() => activeSidebarView = 'explorer'}
          title="Explorer"
        >📂</button>
        <button 
          class="activity-btn" 
          class:active={activeSidebarView === 'git'} 
          onclick={() => activeSidebarView = 'git'}
          title="Source Control"
        >🌿</button>
        <button 
          class="activity-btn" 
          class:active={activeSidebarView === 'symbols'} 
          onclick={() => activeSidebarView = 'symbols'}
          title="Symbol Browser"
        >🧩</button>
      </nav>

      <aside class="sidebar">
        {#if activeSidebarView === 'explorer'}
          <FileTree {workspacePath} onfileselect={handleFileSelect} onworkspaceopen={handleWorkspaceOpen} />
        {:else if activeSidebarView === 'git'}
          <GitPanel {workspacePath} onfileselect={handleFileSelect} />
        {:else if activeSidebarView === 'symbols'}
          <SymbolBrowser {workspacePath} bind:activeFile={activeFile} />
        {/if}
      </aside>

      <div class="editor-column">
        <main class="editor-area">
          <CodeEditor
            bind:activeFile={activeFile}
            bind:openFiles={openFiles}
            {workspacePath}
          />
        </main>
        {#if showTerminal}
          <div class="terminal-area">
            <div class="terminal-area__header">
              <span class="terminal-area__title">Terminal</span>
              <span class="terminal-area__cwd">{workspacePath}</span>
              <button class="terminal-area__close" onclick={() => showTerminal = false} title="Close Terminal">×</button>
            </div>
            <div class="terminal-area__body">
              <TerminalPanel {workspacePath} bind:isOpen={showTerminal} />
            </div>
          </div>
        {/if}
      </div>

      <aside class="chat-panel">
        <ChatPanel 
          {workspacePath} 
          bind:engineOnline 
          oncommand={handleUICommand} 
        />
      </aside>
    </div>
  {/if}

  <StatusBar currentFile={activeFile} {engineOnline} {workspacePath} {mcpReady} {mcpToolCount} />

  {/if} <!-- end auth gate -->
</div>

<style>
  .studio-layout {
    display: flex;
    flex-direction: column;
    height: 100vh;
    width: 100vw;
    overflow: hidden;
  }

  .title-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 40px;
    padding: 0 16px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    user-select: none;
    z-index: 100;
  }
  .title-bar__left { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
  .title-bar__logo { font-size: 16px; }
  .title-bar__name { font-weight: 600; font-size: 11px; color: var(--text-secondary); letter-spacing: 0.5px; text-transform: uppercase; }
  .title-bar__center {
    flex: 1; display: flex; align-items: center; justify-content: center;
    gap: 8px; font-size: 12px; color: var(--text-secondary);
    min-width: 0; overflow: hidden;
  }
  .title-bar__file { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 400px; }
  .title-bar__right { display: flex; align-items: center; gap: 8px; }
  .engine-badge {
    font-size: 11px; padding: 3px 10px; border-radius: var(--radius-sm);
    background: rgba(248, 81, 73, 0.15); color: var(--accent-red);
    white-space: nowrap;
  }
  .engine-badge.online { background: rgba(63, 185, 80, 0.15); color: var(--accent-green); }

  /* Welcome Screen */
  .welcome-screen {
    flex: 1;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg-primary);
  }
  .welcome-card {
    text-align: center;
    max-width: 500px;
    width: 90%;
    animation: fadeIn 0.4s ease-out;
  }
  @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } }
  .welcome-logo { font-size: 64px; margin-bottom: 20px; }
  .welcome-card h1 { font-size: 32px; font-weight: 700; margin-bottom: 12px; color: var(--text-primary); }
  .welcome-card p { font-size: 16px; color: var(--text-secondary); margin-bottom: 32px; line-height: 1.5; }
  
  .init-box {
    display: flex;
    gap: 8px;
    background: var(--bg-secondary);
    padding: 12px;
    border-radius: var(--radius-lg);
    border: 1px solid var(--border);
    box-shadow: 0 8px 24px rgba(0,0,0,0.2);
  }
  .init-box input {
    flex: 1;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    color: var(--text-primary);
    padding: 10px 16px;
    border-radius: var(--radius-md);
    outline: none;
    font-size: 14px;
  }
  .init-box input:focus { border-color: var(--accent-blue); }
  .init-box button {
    background: var(--accent-blue);
    color: white;
    padding: 0 20px;
    border-radius: var(--radius-md);
    font-weight: 600;
    transition: background var(--transition);
  }
  .init-box button:hover:not(:disabled) { background: #1f6feb; }
  .init-box button:disabled { opacity: 0.5; cursor: not-allowed; }

  .init-error { margin-top: 16px; color: var(--accent-red); font-size: 13px; font-weight: 500; }
  .welcome-footer { margin-top: 24px; font-size: 12px; color: var(--text-muted); opacity: 0.6; }

  /* Recent workspaces */
  .recent-workspaces {
    width: 100%; margin-top: 24px;
    text-align: left;
  }
  .recent-title {
    font-size: 11px; font-weight: 600; text-transform: uppercase;
    letter-spacing: 0.6px; color: var(--text-muted); margin-bottom: 8px;
  }
  .recent-item {
    display: flex; align-items: center; gap: 10px;
    padding: 9px 12px; border-radius: var(--radius-md);
    border: 1px solid var(--border); background: var(--bg-secondary);
    cursor: pointer; margin-bottom: 6px;
    transition: background 0.15s, border-color 0.15s;
  }
  .recent-item:hover { background: var(--bg-hover); border-color: var(--accent-blue); }
  .recent-icon { font-size: 16px; flex-shrink: 0; }
  .recent-info { flex: 1; min-width: 0; }
  .recent-name {
    display: block; font-size: 13px; font-weight: 500;
    color: var(--text-primary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .recent-path {
    display: block; font-size: 11px; color: var(--text-muted);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    font-family: var(--font-mono);
  }
  .recent-delete {
    background: none; border: none; color: var(--text-muted);
    font-size: 12px; cursor: pointer; padding: 2px 4px;
    border-radius: var(--radius-sm); flex-shrink: 0;
    opacity: 0; transition: opacity 0.15s;
  }
  .recent-item:hover .recent-delete { opacity: 1; }
  .recent-delete:hover { color: var(--accent-red); }

  /* Main IDE content styles */
  .main-content { display: flex; flex: 1; overflow: hidden; }
  
  .activity-bar {
    width: 48px;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    align-items: center;
    padding-top: 10px;
    gap: 12px;
  }
  .activity-btn {
    background: none; border: none; font-size: 20px; cursor: pointer;
    opacity: 0.5; transition: opacity 0.2s; padding: 8px; border-radius: 4px;
    width: 36px; height: 36px; display: flex; align-items: center; justify-content: center;
  }
  .activity-btn:hover { opacity: 0.8; background: rgba(255,255,255,0.05); }
  .activity-btn.active { opacity: 1; border-left: 2px solid var(--accent-blue); border-radius: 0; }

  .signout-btn {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    border: 1px solid var(--border);
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 500;
    cursor: pointer;
    transition: all 0.15s;
  }
  .signout-btn:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
    border-color: var(--text-muted);
  }

  .sidebar { width: 260px; min-width: 200px; background: var(--bg-secondary); border-right: 1px solid var(--border); overflow-y: auto; }
  .editor-column { flex: 1; min-width: 200px; display: flex; flex-direction: column; overflow: hidden; }
  .editor-area { flex: 1; min-width: 0; overflow: hidden; background: var(--bg-primary); }
  .chat-panel { width: 480px; min-width: 360px; background: var(--bg-secondary); border-left: 1px solid var(--border); overflow: hidden; display: flex; flex-direction: column; }

  /* Terminal panel */
  .terminal-area {
    flex-shrink: 0;
    height: 260px;
    border-top: 1px solid var(--border);
    background: #0d1117;
    display: flex;
    flex-direction: column;
  }
  .terminal-area__header {
    display: flex;
    align-items: center;
    gap: 8px;
    height: 30px;
    padding: 0 12px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .terminal-area__title {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .terminal-area__cwd {
    flex: 1;
    font-size: 11px;
    color: var(--text-muted);
    font-family: var(--font-mono);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .terminal-area__close {
    background: none;
    border: none;
    color: var(--text-muted);
    font-size: 16px;
    cursor: pointer;
    padding: 2px 4px;
    line-height: 1;
    border-radius: 3px;
    transition: color 0.15s;
  }
  .terminal-area__close:hover { color: var(--text-primary); }
  .terminal-area__body { flex: 1; overflow: hidden; }

  /* Terminal toggle button */
  .terminal-toggle-btn {
    background: var(--bg-tertiary);
    color: var(--text-secondary);
    border: 1px solid var(--border);
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    font-family: var(--font-mono);
    cursor: pointer;
    transition: all 0.15s;
  }
  .terminal-toggle-btn:hover { background: var(--bg-hover); color: var(--text-primary); }
  .terminal-toggle-btn.active { background: rgba(88,166,255,0.15); color: var(--accent-blue); border-color: var(--accent-blue); }

  .home-btn {
    background: var(--bg-tertiary);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
    padding: 2px 6px;
    margin-right: 12px;
    font-size: 14px;
    line-height: 1;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    cursor: pointer;
    transition: all var(--transition);
  }
  .home-btn:hover {
    background: var(--bg-hover);
    border-color: var(--accent-blue);
    transform: translateY(-1px);
  }
  .home-btn:active {
    transform: translateY(0);
  }
</style>
