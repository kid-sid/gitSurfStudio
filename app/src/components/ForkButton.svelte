<script>
  import { gitFork } from "../lib/api";

  let { workspacePath = "", isGitHub = false } = $props();
  
  let loading = $state(false);
  let forkUrl = $state("");
  let error = $state("");

  async function handleFork() {
    if (!workspacePath) return;
    
    // Extract repo name from workspace path or if it's a URL
    // For now, we assume the workspacePath might be the repo name if we just initialized
    // However, the engine usually syncs it to a local path.
    // Let's assume the user has a way to provide the repo name or we extract it.
    // In a real app, this would be part of the workspace metadata.
    
    const parts = workspacePath.split(/[/\\]/);
    const repoName = parts[parts.length - 1].replace("_", "/"); // Hacky reversal of sync_repo naming

    loading = true;
    error = "";
    try {
      const result = await gitFork(workspacePath, repoName);
      forkUrl = result.fork_url;
    } catch (e) {
      error = e.message;
    } finally {
      loading = false;
    }
  }
</script>

<div class="fork-container">
  {#if isGitHub}
    <button 
      class="fork-button" 
      onclick={handleFork} 
      disabled={loading || forkUrl}
    >
      {#if loading}
        <span class="spinner">🌀</span> Forking...
      {:else if forkUrl}
        ✅ Forked
      {:else}
        🍴 Fork Repository
      {/if}
    </button>
  {/if}

  {#if forkUrl}
    <a href={forkUrl} target="_blank" class="fork-link">View on GitHub</a>
  {/if}

  {#if error}
    <span class="error">{error}</span>
  {/if}
</div>

<style>
  .fork-container {
    display: flex;
    align-items: center;
    gap: 10px;
  }
  .fork-button {
    background: #238636;
    color: white;
    border: none;
    padding: 3px 10px;
    border-radius: 6px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    display: flex;
    align-items: center;
    gap: 5px;
  }
  .fork-button:hover:not(:disabled) {
    background: #2ea043;
  }
  .fork-button:disabled {
    opacity: 0.7;
    cursor: default;
  }
  .fork-link {
    color: #58a6ff;
    font-size: 12px;
    text-decoration: none;
  }
  .fork-link:hover {
    text-decoration: underline;
  }
  .error {
    color: #f85149;
    font-size: 11px;
  }
  .spinner {
    animation: spin 1s linear infinite;
    display: inline-block;
  }
  @keyframes spin {
    from { transform: rotate(0deg); }
    to { transform: rotate(360deg); }
  }
</style>
