<script>
  import { readFile } from "../lib/api.js";

  let { filePath = "", workspacePath = "" } = $props();

  let fileContent = $state("");
  let isLoading = $state(false);
  let error = $state("");

  // Re-fetch when filePath changes
  $effect(() => {
    if (filePath) {
      loadContent(filePath);
    }
  });

  async function loadContent(path) {
    isLoading = true;
    error = "";
    try {
      const res = await readFile(path);
      fileContent = res.content;
    } catch (e) {
      error = "Failed to load file content: " + e.message;
      fileContent = "";
    } finally {
      isLoading = false;
    }
  }

  function getLineNumbers(content) {
    if (!content && !isLoading) return [1];
    return content.split("\n").map((_, i) => i + 1);
  }

  let lineNumbers = $derived(getLineNumbers(fileContent));
</script>

<div class="editor">
  <div class="editor__tab-bar">
    {#if filePath}
      <div class="editor__tab editor__tab--active">
        <span>{filePath.split(/[/\\]/).pop()}</span>
        <button class="editor__tab-close" onclick={() => filePath = ""}>×</button>
      </div>
    {:else}
      <div class="editor__tab editor__tab--active">
        <span>Welcome</span>
      </div>
    {/if}
  </div>
  
  <div class="editor__scroll-container">
    {#if isLoading}
      <div class="editor__status">Loading...</div>
    {:else if error}
      <div class="editor__status editor__status--error">{error}</div>
    {:else if !filePath}
      <div class="editor__status">Select a file from the explorer to view its contents.</div>
    {:else}
      <div class="editor__content">
        <div class="editor__gutter">
          {#each lineNumbers as num}
            <div class="editor__line-num">{num}</div>
          {/each}
        </div>
        <pre class="editor__code"><code>{fileContent}</code></pre>
      </div>
    {/if}
  </div>
</div>

<style>
  .editor {
    height: 100%;
    display: flex;
    flex-direction: column;
    background: var(--bg-primary);
  }
  .editor__tab-bar {
    display: flex;
    align-items: center;
    height: 36px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    padding: 0 4px;
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
  }
  .editor__tab--active {
    color: var(--text-primary);
    border-bottom-color: var(--accent-blue);
    background: var(--bg-primary);
  }
  .editor__tab-close {
    background: none;
    border: none;
    color: inherit;
    font-size: 16px;
    padding: 0 4px;
    cursor: pointer;
    opacity: 0.5;
  }
  .editor__tab-close:hover { opacity: 1; }

  .editor__scroll-container {
    flex: 1;
    overflow: auto;
    position: relative;
  }
  
  .editor__status {
    padding: 40px;
    text-align: center;
    color: var(--text-muted);
    font-size: 14px;
  }
  .editor__status--error { color: var(--accent-red); }

  .editor__content {
    display: flex;
    padding-top: 8px;
    min-height: 100%;
  }
  .editor__gutter {
    width: 50px;
    text-align: right;
    padding: 0 12px 0 0;
    user-select: none;
    flex-shrink: 0;
    border-right: 1px solid var(--border);
  }
  .editor__line-num {
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 22px;
    color: var(--text-muted);
  }
  .editor__code {
    flex: 1;
    margin: 0;
    padding: 0 16px 20px 16px;
    font-family: var(--font-mono);
    font-size: 13px;
    line-height: 22px;
    color: var(--text-primary);
    white-space: pre;
    tab-size: 4;
  }
</style>
