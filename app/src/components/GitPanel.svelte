<script>
  import { onMount } from "svelte";
  import { gitStatus, gitStage, gitCommit, getBranches, checkoutBranch, gitStash, gitStashPop, gitDiscard } from "../lib/api";

  let { workspacePath = "" } = $props();

  let changes = $state([]);
  let commitMessage = $state("");
  let loading = $state(false);
  let error = $state("");
  let currentBranch = $state("");
  let branches = $state([]);
  let isSwitchingBranch = $state(false);

  async function refreshStatus() {
    if (!workspacePath) return;
    try {
      const branchData = await getBranches(workspacePath);
      currentBranch = branchData.current || "unknown";
      branches = branchData.branches || [];
      
      const result = await gitStatus(workspacePath);
      changes = result.status;
    } catch (e) {
      error = "Failed to load git status";
    }
  }

  async function handleBranchChange(e) {
    const newBranch = e.target.value;
    if (newBranch && newBranch !== currentBranch) {
      isSwitchingBranch = true;
      try {
        await checkoutBranch(workspacePath, newBranch);
        await refreshStatus();
        // Fire custom event so parent can refresh file tree
        window.dispatchEvent(new CustomEvent('branch-changed'));
      } catch (err) {
        error = err.message || `Failed to checkout ${newBranch}`;
        // Revert select back to current branch
        e.target.value = currentBranch;
      } finally {
        isSwitchingBranch = false;
      }
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

  async function handleStash() {
    loading = true;
    try {
      await gitStash(workspacePath);
      await refreshStatus();
    } catch (e) {
      error = "Stash failed: " + e.message;
    } finally {
      loading = false;
    }
  }

  async function handlePop() {
    loading = true;
    try {
      await gitStashPop(workspacePath);
      await refreshStatus();
    } catch (e) {
      error = "Pop failed: " + e.message;
    } finally {
      loading = false;
    }
  }

  async function handleDiscard(path) {
    if (!confirm(`Are you sure you want to discard changes to ${path}?`)) return;
    try {
      await gitDiscard(workspacePath, path);
      await refreshStatus();
    } catch (e) {
      error = "Failed to discard changes: " + e.message;
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
    <div class="header-left">
      <h3>Source Control</h3>
      {#if currentBranch}
        <select class="branch-select" value={currentBranch} onchange={handleBranchChange} disabled={isSwitchingBranch}>
          {#each branches as branch}
            <option value={branch}>{branch}</option>
          {/each}
        </select>
      {/if}
    </div>
    <div class="header-right">
      <button class="action-btn" onclick={handleStash} title="Stash All Changes">📥</button>
      <button class="action-btn" onclick={handlePop} title="Pop Latest Stash">📤</button>
      <button class="refresh-btn" onclick={refreshStatus} title="Refresh Status">🔄</button>
    </div>
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
          <div class="change-actions">
            <button class="action-item-btn discard" onclick={() => handleDiscard(change.path)} title="Discard Changes">↩</button>
            <button class="action-item-btn stage" onclick={() => handleStage(change.path)} title="Stage Change">+</button>
          </div>
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
  .header-left {
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .panel-header h3 {
    margin: 0;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: #8b949e;
  }
  .branch-select {
    background: #21262d;
    color: #c9d1d9;
    border: 1px solid #30363d;
    border-radius: 4px;
    padding: 2px 6px;
    font-size: 11px;
    cursor: pointer;
    outline: none;
    max-width: 120px;
    text-overflow: ellipsis;
  }
  .branch-select:hover:not(:disabled) {
    border-color: #8b949e;
  }
  .branch-select:disabled {
    opacity: 0.5;
    cursor: wait;
  }
  .refresh-btn, .action-btn {
    background: none; border: none; cursor: pointer; color: #8b949e; font-size: 14px;
    padding: 4px;
    border-radius: 4px;
    transition: background 0.2s;
  }
  .refresh-btn:hover, .action-btn:hover { background: #21262d; color: white; }

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
  .change-actions {
    display: flex;
    gap: 4px;
    opacity: 0;
  }
  .change-item:hover .change-actions {
    opacity: 1;
  }
  .action-item-btn {
    background: none; border: none; color: #8b949e; cursor: pointer; font-size: 14px;
    padding: 2px 4px;
    border-radius: 4px;
  }
  .action-item-btn:hover { background: #30363d; color: white; }
  .action-item-btn.discard:hover { color: #f85149; }
  .action-item-btn.stage:hover { color: #3fb950; }

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
