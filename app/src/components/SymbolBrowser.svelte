<script>
  import { getSymbols } from "../lib/api.js";
  import { onMount } from "svelte";

  let { workspacePath, activeFile = $bindable("") } = $props();

  let symbols = $state([]);
  let isLoading = $state(false);
  let error = $state("");

  // Re-fetch symbols when the active file or workspace changes
  $effect(() => {
    if (workspacePath) {
      fetchSymbols();
    }
  });

  async function fetchSymbols() {
    isLoading = true;
    error = "";
    try {
      // If we have an active file, show its symbols. Otherwise, show all in workspace.
      const target = activeFile || workspacePath;
      const res = await getSymbols(target, workspacePath);
      symbols = res.symbols || [];
    } catch (e) {
      error = "Failed to load symbols: " + e.message;
    } finally {
      isLoading = false;
    }
  }

  function handleSymbolClick(sym) {
    let targetFile = sym.file || activeFile;
    
    // Ensure we use an absolute path for activeFile
    if (sym.file && !sym.file.includes(':') && !sym.file.startsWith('/') && !sym.file.startsWith('\\')) {
        // Simple absolute path resolution for Windows/Unix
        const separator = workspacePath.includes('\\') ? '\\' : '/';
        const normalizedFile = sym.file.replace(/[/\\]/g, separator);
        targetFile = workspacePath + (workspacePath.endsWith(separator) ? '' : separator) + normalizedFile;
    }

    if (targetFile !== activeFile) {
        activeFile = targetFile;
    }

    window.dispatchEvent(new CustomEvent("navigate-to-line", {
        detail: { path: targetFile, line: sym.start_line || sym.line }
    }));
  }
</script>

<div class="symbol-browser">
  <div class="panel-header">
    <h3>Symbols</h3>
    <button class="refresh-btn" onclick={fetchSymbols} title="Refresh symbols">🔄</button>
  </div>

  {#if isLoading}
    <div class="status">
      <div class="spinner"></div>
      Parsing symbols...
    </div>
  {:else if error}
    <div class="status error">
      ⚠️ {error}
      <button onclick={fetchSymbols}>Retry</button>
    </div>
  {:else if symbols.length === 0}
    <div class="status empty">
      No symbols found in this {activeFile ? 'file' : 'workspace'}.
    </div>
  {:else}
    <div class="symbol-list">
      {#each symbols as sym}
        <button class="symbol-item" onclick={() => handleSymbolClick(sym)}>
          <span class="symbol-icon" class:class={sym.type === 'class'} class:func={sym.type === 'function' || sym.type === 'method'}>
            {sym.type === 'class' ? 'C' : sym.type === 'import' ? 'I' : 'F'}
          </span>
          <div class="symbol-info">
            <span class="symbol-name">{sym.name}</span>
            <span class="symbol-meta">
                {#if sym.type === 'method' && sym.parent}
                  {sym.parent}.
                {/if}
                Line {sym.start_line || sym.line}
            </span>
          </div>
        </button>
        
        {#if sym.methods && sym.methods.length > 0}
            <div class="symbol-children">
                {#each sym.methods as method}
                    <button class="symbol-item child" onclick={() => handleSymbolClick(method)}>
                        <span class="symbol-icon func">M</span>
                        <div class="symbol-info">
                            <span class="symbol-name">{method.name}</span>
                            <span class="symbol-meta">Line {method.start_line}</span>
                        </div>
                    </button>
                {/each}
            </div>
        {/if}
      {/each}
    </div>
  {/if}
</div>

<style>
  .symbol-browser {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: var(--bg-secondary);
    color: var(--text-primary);
  }

  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
  }
  .panel-header h3 {
    margin: 0;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: var(--text-secondary);
  }

  .refresh-btn {
    background: none;
    border: none;
    cursor: pointer;
    font-size: 12px;
    opacity: 0.6;
    transition: opacity 0.2s;
  }
  .refresh-btn:hover { opacity: 1; }

  .status {
    padding: 32px 16px;
    text-align: center;
    font-size: 13px;
    color: var(--text-muted);
  }
  .status.error { color: var(--accent-red); }
  .status.empty { opacity: 0.6; }

  .spinner {
    width: 16px;
    height: 16px;
    border: 2px solid rgba(255,255,255,0.1);
    border-top-color: var(--accent-blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    margin: 0 auto 12px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .symbol-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }

  .symbol-item {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 6px 16px;
    background: none;
    border: none;
    cursor: pointer;
    text-align: left;
    transition: background 0.1s;
  }
  .symbol-item:hover { background: var(--bg-hover); }
  .symbol-item.child { padding-left: 32px; }

  .symbol-icon {
    width: 18px;
    height: 18px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    font-weight: 800;
    border-radius: 3px;
    flex-shrink: 0;
    background: #444;
    color: #eee;
  }
  .symbol-icon.class { background: #dcb139; color: #111; }
  .symbol-icon.func { background: #b180d7; color: #eee; }

  .symbol-info {
    display: flex;
    flex-direction: column;
    min-width: 0;
  }
  .symbol-name {
    font-size: 13px;
    font-weight: 500;
    color: var(--text-primary);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .symbol-meta {
    font-size: 11px;
    color: var(--text-muted);
    opacity: 0.7;
  }

  .symbol-children {
      border-left: 1px solid var(--border);
      margin-left: 24px;
  }
</style>
