<script>
  import { onMount } from "svelte";
  import { gitStatus, gitStage, gitCommit } from "../lib/api";

  let { workspacePath = "" } = $props();

  let changes = $state([]);
  let commitMessage = $state("");
  let loading = $state(false);
  let error = $state("");

  async function refreshStatus() {
    if (!workspacePath) return;
    try {
      const result = await gitStatus(workspacePath);
      changes = result.status;
    } catch (e) {
      error = "Failed to load git status";
    }
  }

  async function handleStage(path) {
    try {
      await gitStage(workspacePath, [path]);
      await refreshStatus();
    } catch (e) {
      error = "Failed to stage file";
    }
  }

  async function handleCommit() {
    if (!commitMessage.trim()) return;
    loading = true;
    try {
      await gitCommit(workspacePath, commitMessage);
      commitMessage = "";
      await refreshStatus();
    } catch (e) {
      error = "Commit failed";
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    refreshStatus();
  });

  // Re-run status when workspace changes
  $effect(() => {
    if (workspacePath) refreshStatus();
  });
</script>

<div class="git-panel">
  <div class="panel-header">
    <h3>Source Control</h3>
    <button class="refresh-btn" onclick={refreshStatus} title="Refresh Status">🔄</button>
  </div>

  <div class="changes-list">
    {#if changes.length === 0}
      <div class="empty-state">No changes detected</div>
    {:else}
      <div class="section-title">Changes ({changes.length})</div>
      {#each changes as change}
        <div class="change-item">
          <span class="status-badge" class:modified={change.status.includes('M')} class:added={change.status.includes('?') || change.status.includes('A')}>
            {change.status.trim() || 'M'}
          </span>
          <span class="file-path" title={change.path}>{change.path.split('/').pop()}</span>
          <button class="stage-btn" onclick={() => handleStage(change.path)} title="Stage Change">+</button>
        </div>
      {/each}
    {/if}
  </div>

  {#if changes.length > 0}
    <div class="commit-section">
      <textarea 
        placeholder="Message (Ctrl+Enter to commit)" 
        bind:value={commitMessage}
        onkeydown={(e) => e.key === 'Enter' && (e.ctrlKey || e.metaKey) && handleCommit()}
      ></textarea>
      <button class="commit-btn" onclick={handleCommit} disabled={loading || !commitMessage.trim()}>
        {#if loading}Committing...{:else}Commit{/if}
      </button>
    </div>
  {/if}

  {#if error}
    <div class="panel-error">{error}</div>
  {/if}
</div>

<style>
  .git-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
    background: #0d1117;
    color: #c9d1d9;
    border-right: 1px solid #30363d;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  }
  .panel-header {
    padding: 12px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid #30363d;
  }
  .panel-header h3 {
    margin: 0;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #8b949e;
  }
  .refresh-btn {
    background: none; border: none; cursor: pointer; color: #8b949e; font-size: 14px;
  }
  .refresh-btn:hover { color: white; }

  .changes-list {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0;
  }
  .empty-state {
    padding: 20px;
    text-align: center;
    color: #8b949e;
    font-size: 13px;
  }
  .section-title {
    padding: 4px 12px;
    font-size: 11px;
    font-weight: 600;
    color: #8b949e;
  }
  .change-item {
    display: flex;
    align-items: center;
    padding: 4px 12px;
    gap: 8px;
    font-size: 13px;
  }
  .change-item:hover {
    background: #161b22;
  }
  .status-badge {
    width: 14px;
    font-size: 10px;
    font-weight: bold;
    text-align: center;
  }
  .status-badge.modified { color: #d29922; }
  .status-badge.added { color: #2ea043; }
  
  .file-path {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .stage-btn {
    opacity: 0;
    background: none; border: none; color: #8b949e; cursor: pointer; font-size: 16px;
  }
  .change-item:hover .stage-btn {
    opacity: 1;
  }
  .stage-btn:hover { color: white; }

  .commit-section {
    padding: 12px;
    border-top: 1px solid #30363d;
    background: #0d1117;
  }
  textarea {
    width: 100%;
    height: 60px;
    background: #161b22;
    border: 1px solid #30363d;
    border-radius: 6px;
    color: white;
    padding: 8px;
    font-size: 13px;
    resize: none;
    margin-bottom: 8px;
  }
  textarea:focus {
    outline: none;
    border-color: #58a6ff;
  }
  .commit-btn {
    width: 100%;
    background: #238636;
    color: white;
    border: none;
    padding: 6px;
    border-radius: 6px;
    font-weight: 600;
    cursor: pointer;
  }
  .commit-btn:hover:not(:disabled) { background: #2ea043; }
  .commit-btn:disabled { opacity: 0.5; cursor: default; }

  .panel-error {
    padding: 8px 12px;
    background: #482323;
    color: #ff7b72;
    font-size: 11px;
  }
</style>
