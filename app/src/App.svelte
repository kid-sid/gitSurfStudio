<script>
  import "./styles.css";
  import { onMount } from "svelte";
  import FileTree from "./components/FileTree.svelte";
  import CodeEditor from "./components/CodeEditor.svelte";
  import ChatPanel from "./components/ChatPanel.svelte";
  import StatusBar from "./components/StatusBar.svelte";
import ForkButton from "./components/ForkButton.svelte";
import GitPanel from "./components/GitPanel.svelte";
  import { initWorkspace, checkHealth, checkAuthStatus, loginWithGitHub } from "./lib/api.js";

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

  onMount(async () => {
    // Initial health and auth check
    engineOnline = await checkHealth();
    const auth = await checkAuthStatus();
    isAuthenticated = auth.authenticated;
    
    // Auto-ping engine
    setInterval(async () => {
      engineOnline = await checkHealth();
      const auth = await checkAuthStatus();
      isAuthenticated = auth.authenticated;
    }, 10000);
  });

  async function handleInit() {
    if (!initInput.trim() || isInitializing) return;
    
    isInitializing = true;
    initError = "";
    
    try {
      const res = await initWorkspace(initInput.trim());
      workspacePath = res.workspace_path;
      isGitHubRepo = res.is_github;
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
      <span class="status-bar__item status-bar__engine" class:online={engineOnline}>
        ● {engineOnline ? "Engine Connected" : "Engine Disconnected"}
      </span>
      {#if workspacePath}
        {#if !isAuthenticated}
          <button class="login-btn" onclick={loginWithGitHub}>🔑 Login with GitHub</button>
        {:else}
          <ForkButton {workspacePath} isGitHub={isGitHubRepo} />
        {/if}
      {/if}
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
      </nav>

      <aside class="sidebar">
        {#if activeSidebarView === 'explorer'}
          <FileTree {workspacePath} onfileselect={handleFileSelect} onworkspaceopen={handleWorkspaceOpen} />
        {:else if activeSidebarView === 'git'}
          <GitPanel {workspacePath} />
        {/if}
      </aside>

      <main class="editor-area">
        <CodeEditor 
          bind:activeFile={activeFile} 
          bind:openFiles={openFiles} 
          {workspacePath} 
        />
      </main>

      <aside class="chat-panel">
        <ChatPanel {workspacePath} bind:engineOnline oncommand={handleUICommand} />
      </aside>
    </div>
  {/if}

  <StatusBar currentFile={activeFile} {engineOnline} {workspacePath} />
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
  .title-bar__left { display: flex; align-items: center; gap: 8px; }
  .title-bar__logo { font-size: 18px; }
  .title-bar__name { font-weight: 600; font-size: 12px; color: var(--text-secondary); letter-spacing: 0.5px; text-transform: uppercase; }
  .title-bar__center { font-size: 12px; color: var(--text-secondary); }
  .title-bar__status { font-size: 11px; padding: 3px 10px; border-radius: var(--radius-sm); background: rgba(248, 81, 73, 0.15); color: var(--accent-red); }
  .title-bar__status.online { background: rgba(63, 185, 80, 0.15); color: var(--accent-green); }

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
  .welcome-footer { margin-top: 40px; font-size: 12px; color: var(--text-muted); opacity: 0.6; }

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

  .login-btn {
    background: #238636;
    color: white;
    border: none;
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.2s;
  }
  .login-btn:hover { background: #2ea043; }

  .sidebar { width: 260px; min-width: 200px; background: var(--bg-secondary); border-right: 1px solid var(--border); overflow-y: auto; }
  .editor-area { flex: 1; min-width: 200px; overflow: hidden; background: var(--bg-primary); }
  .chat-panel { width: 380px; min-width: 300px; background: var(--bg-secondary); border-left: 1px solid var(--border); overflow: hidden; display: flex; flex-direction: column; }

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
