<script>
  import { onMount, onDestroy } from "svelte";
  import { readFile, writeFile } from "../lib/api.js";

  let { activeFile = $bindable(""), openFiles = $bindable([]), workspacePath = "" } = $props();

  // state per file: mapping path to file data
  let filesState = $state({});

  onMount(() => {
    const handleBranchChange = () => {
      console.log("Branch changed, clearing editor cache...");
      filesState = {}; // Clear all cache
      if (activeFile) {
        loadContent(activeFile);
      }
    };

    const handleNavigate = (e) => {
      const { path, line } = e.detail;
      if (path === activeFile) {
        // Find the textarea and scroll to the line
        const textarea = document.querySelector('.editor__textarea');
        if (textarea) {
          const lines = textarea.value.split('\n');
          let charOffset = 0;
          for (let i = 0; i < Math.min(line - 1, lines.length); i++) {
            charOffset += lines[i].length + 1;
          }
          textarea.focus();
          textarea.setSelectionRange(charOffset, charOffset);
          // Simple scroll approximation (since it's a textarea)
          const lineHeight = 20; // from CSS
          textarea.scrollTop = (line - 1) * lineHeight;
        }
      } else {
          // If the file is not active, Svelte's reactivity might take a moment to mount the textarea.
          // In App.svelte I already ensure it opens, but let's wait a bit and try to navigate.
          setTimeout(() => {
              if (activeFile === path) handleNavigate(e);
          }, 100);
      }
    };

    window.addEventListener('branch-changed', handleBranchChange);
    window.addEventListener('navigate-to-line', handleNavigate);

    return () => {
      window.removeEventListener('branch-changed', handleBranchChange);
      window.removeEventListener('navigate-to-line', handleNavigate);
    };
  });

  // Re-fetch when activeFile changes if not already loaded
  $effect(() => {
    if (activeFile && !filesState[activeFile]) {
      loadContent(activeFile);
    }
  });

  async function loadContent(path) {
    if (!filesState[path]) {
      filesState[path] = { content: "", original: "", isLoading: true, isSaving: false, error: "", saveStatus: "" };
    } else {
      filesState[path].isLoading = true;
    }
    
    filesState[path].error = "";
    filesState[path].saveStatus = "";
    
    try {
      const res = await readFile(path);
      filesState[path].content = res.content;
      filesState[path].original = res.content;
    } catch (e) {
      filesState[path].error = "Failed to load file content: " + e.message;
    } finally {
      filesState[path].isLoading = false;
    }
  }

  async function handleSave(path) {
    const f = filesState[path];
    if (!path || f.isSaving || f.content === f.original) return;

    f.isSaving = true;
    f.saveStatus = "saving";
    try {
      await writeFile(path, f.content);
      f.original = f.content;
      f.saveStatus = "success";
      setTimeout(() => { if (f.saveStatus === "success") f.saveStatus = ""; }, 2000);
    } catch (e) {
      f.error = "Failed to save: " + e.message;
      f.saveStatus = "error";
    } finally {
      f.isSaving = false;
    }
  }

  function handleKeyDown(e, path) {
    if ((e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault();
      handleSave(path);
    }
    
    if (e.key === 'Tab') {
      e.preventDefault();
      const start = e.target.selectionStart;
      const end = e.target.selectionEnd;
      const content = filesState[path].content;
      filesState[path].content = content.substring(0, start) + "    " + content.substring(end);
      setTimeout(() => {
        e.target.selectionStart = e.target.selectionEnd = start + 4;
      }, 0);
    }
  }


  function handleCloseTab(path, e) {
    e.stopPropagation();
    const index = openFiles.indexOf(path);
    if (index === -1) return;

    openFiles = openFiles.filter(p => p !== path);
    // Don't leak memory, but maybe keep state if they reopen? 
    // For now, let's delete it.
    delete filesState[path];

    if (activeFile === path) {
      if (openFiles.length > 0) {
        activeFile = openFiles[Math.min(index, openFiles.length - 1)];
      } else {
        activeFile = "";
      }
    }
  }

  function getLineNumbers(content, isLoading) {
    if (!content && !isLoading) return [1];
    const lines = content.split("\n");
    return lines.length > 0 ? lines.map((_, i) => i + 1) : [1];
  }
</script>

<div class="editor">
  <div class="editor__tab-bar">
    {#if openFiles.length > 0}
      {#each openFiles as path}
        {@const f = filesState[path] || {}}
        {@const isModified = f.content !== f.original}
        <button 
          class="editor__tab" 
          class:editor__tab--active={activeFile === path}
          onclick={() => activeFile = path}
        >
          <span>{path.split(/[/\\]/).pop()}</span>
          {#if isModified}
            <span class="editor__modified-dot"></span>
          {/if}
          <span class="editor__tab-close" onclick={(e) => handleCloseTab(path, e)}>×</span>
        </button>
      {/each}
      
      <div class="editor__actions">
        {#if activeFile && filesState[activeFile]}
          {@const f = filesState[activeFile]}
          {#if f.saveStatus === "success"}
            <span class="save-toast success">Saved!</span>
          {:else if f.saveStatus === "error"}
            <span class="save-toast error">Failed to save</span>
          {/if}
          <button 
            class="save-button" 
            onclick={() => handleSave(activeFile)} 
            disabled={f.content === f.original || f.isSaving}
            title="Save (Ctrl+S)"
          >
            {f.isSaving ? "Saving..." : "Save"}
          </button>
        {/if}
      </div>
    {:else}
      <div class="editor__tab editor__tab--active">
        <span>Welcome</span>
      </div>
    {/if}
  </div>
  
  <div class="editor__scroll-container">
    {#if !activeFile}
      <div class="editor__status welcome">
        <div class="welcome-icon">🌊</div>
        <h3>Ready to surf?</h3>
        <p>Select a file from the explorer to start editing.</p>
      </div>
    {:else if filesState[activeFile]}
      {@const f = filesState[activeFile]}
      {#if f.isLoading}
        <div class="editor__status">
          <div class="spinner"></div>
          Loading...
        </div>
      {:else if f.error}
        <div class="editor__status editor__status--error">
          <span class="error-icon">⚠️</span>
          {f.error}
          <button onclick={() => loadContent(activeFile)}>Retry</button>
        </div>
      {:else}
        <div class="editor__content">
          <div class="editor__gutter">
            {#each getLineNumbers(f.content, f.isLoading) as num}
              <div class="editor__line-num">{num}</div>
            {/each}
          </div>
          <div class="editor__textarea-container">
            <textarea
              bind:value={f.content}
              onkeydown={(e) => handleKeyDown(e, activeFile)}
              spellcheck="false"
              class="editor__textarea"
            ></textarea>
          </div>
        </div>
      {/if}
    {/if}
  </div>
</div>

<style>
  .editor {
    height: 100%;
    display: flex;
    flex-direction: column;
    background: var(--bg-primary);
    color: var(--text-primary);
  }
  .editor__tab-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 36px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 0 4px;
    user-select: none;
  }
  .editor__tab {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 16px;
    font-size: 12px;
    color: var(--text-secondary);
    border-bottom: 2px solid transparent;
    cursor: pointer;
    background: transparent;
    transition: all 0.2s ease;
  }
  .editor__tab--active {
    color: var(--text-primary);
    border-bottom-color: var(--accent-blue);
    background: var(--bg-primary);
  }
  .editor__modified-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent-blue);
  }
  .editor__tab-close {
    background: none;
    border: none;
    color: inherit;
    font-size: 16px;
    padding: 2px;
    cursor: pointer;
    opacity: 0.5;
    line-height: 1;
  }
  .editor__tab-close:hover { opacity: 1; color: var(--accent-red); }

  .editor__actions {
    display: flex;
    align-items: center;
    gap: 12px;
    padding-right: 8px;
  }
  
  .save-toast {
    font-size: 11px;
    font-weight: 500;
    animation: fadeIn 0.2s ease;
  }
  .save-toast.success { color: var(--accent-green); }
  .save-toast.error { color: var(--accent-red); }

  @keyframes fadeIn { from { opacity: 0; transform: translateX(5px); } to { opacity: 1; transform: translateX(0); } }

  .save-button {
    background: var(--accent-blue);
    color: white;
    border: none;
    padding: 4px 12px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.2s;
  }
  .save-button:hover:not(:disabled) { filter: brightness(1.1); }
  .save-button:disabled { opacity: 0.3; cursor: default; }

  .editor__scroll-container {
    flex: 1;
    overflow: hidden; /* Scroll handled by textarea */
    position: relative;
    display: flex;
  }
  
  .editor__status {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    color: var(--text-muted);
    font-size: 14px;
    text-align: center;
  }
  .editor__status.welcome { opacity: 0.7; }
  .welcome-icon { font-size: 48px; margin-bottom: 8px; }
  .welcome h3 { color: var(--text-primary); margin: 0; }
  .welcome p { margin: 0; font-size: 13px; }
  .editor__status--error { color: var(--accent-red); padding: 20px; }
  .error-icon { font-size: 24px; }

  .spinner {
    width: 20px;
    height: 20px;
    border: 2px solid rgba(255,255,255,0.1);
    border-top-color: var(--accent-blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }

  .editor__content {
    display: flex;
    flex: 1;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }
  
  .editor__gutter {
    width: 50px;
    text-align: right;
    padding: 16px 12px 0 0;
    user-select: none;
    flex-shrink: 0;
    background: var(--bg-secondary);
    border-right: 1px solid var(--border);
    overflow: hidden;
  }
  .editor__line-num {
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 20px;
    color: var(--text-muted);
    opacity: 0.5;
  }

  .editor__textarea-container {
    flex: 1;
    position: relative;
    background: var(--bg-primary);
  }

  .editor__textarea {
    width: 100%;
    height: 100%;
    border: none;
    background: transparent;
    color: var(--text-primary);
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 20px;
    padding: 16px;
    resize: none;
    outline: none;
    tab-size: 4;
    white-space: pre;
    overflow: auto;
  }
</style>
