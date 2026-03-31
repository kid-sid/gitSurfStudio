<script>
  let {
    openFiles = [],
    activeFile = $bindable(""),
    filesState = {},
    pendingDiff = null,
    onclose = null,
    onsave = null,
  } = $props();

  function handleCloseClick(path, e) {
    e.stopPropagation();
    onclose?.(path);
  }
</script>

<div class="editor__tab-bar">
  <div class="editor__tabs">
    {#if openFiles.length > 0}
      {#each openFiles as path}
        {@const f = filesState[path] || {}}
        <button
          class="editor__tab"
          class:editor__tab--active={activeFile === path}
          class:editor__tab--diff={pendingDiff?.path === path}
          onclick={() => activeFile = path}
        >
          <span>{path.split(/[/\\]/).pop()}</span>
          {#if f.isDirty}
            <span class="editor__modified-dot"></span>
          {/if}
          <span
            class="editor__tab-close"
            onclick={(e) => handleCloseClick(path, e)}
            onkeydown={(e) => { if (e.key === "Enter" || e.key === " ") { handleCloseClick(path, e); } }}
            role="button"
            tabindex="0"
            title="Close tab"
            aria-label="Close tab"
          >
            &times;
          </span>
        </button>
      {/each}
    {:else}
      <div class="editor__tab editor__tab--active"><span>Welcome</span></div>
    {/if}
  </div>

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
        onclick={() => onsave?.(activeFile)}
        disabled={!f.isDirty || f.isSaving}
        title="Save (Ctrl+S)"
      >
        {f.isSaving ? "Saving..." : "Save"}
      </button>
    {/if}
  </div>
</div>

<style>
  .editor__tab-bar {
    display: flex;
    align-items: stretch;
    height: 36px;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
    user-select: none;
    overflow: hidden;
    flex-shrink: 0;
  }
  .editor__tabs {
    display: flex; align-items: stretch; flex: 1;
    overflow-x: auto; overflow-y: hidden; scrollbar-width: none;
  }
  .editor__tabs::-webkit-scrollbar { display: none; }
  .editor__tab {
    display: flex; align-items: center; gap: 6px; padding: 0 12px;
    font-size: 12px; color: var(--text-secondary);
    border-bottom: 2px solid transparent; border-right: 1px solid var(--border);
    cursor: pointer; background: transparent; white-space: nowrap;
    flex-shrink: 0; transition: background 0.15s, color 0.15s;
  }
  .editor__tab--active {
    color: var(--text-primary);
    border-bottom-color: var(--accent-blue);
    background: var(--bg-primary);
  }
  .editor__tab--diff {
    border-bottom-color: #3fb950 !important;
  }
  .editor__modified-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--accent-blue); flex-shrink: 0;
  }
  .editor__tab-close {
    background: none; border: none; color: inherit; font-size: 16px;
    padding: 2px; cursor: pointer; opacity: 0.5; line-height: 1;
  }
  .editor__tab-close:hover { opacity: 1; color: var(--accent-red); }

  .editor__actions {
    display: flex; align-items: center; gap: 8px; padding: 0 10px;
    flex-shrink: 0; border-left: 1px solid var(--border);
  }
  .save-toast { font-size: 11px; font-weight: 500; animation: fadeIn 0.2s ease; }
  .save-toast.success { color: var(--accent-green); }
  .save-toast.error   { color: var(--accent-red); }
  @keyframes fadeIn {
    from { opacity: 0; transform: translateX(5px); }
    to   { opacity: 1; transform: translateX(0); }
  }
  .save-button {
    background: var(--accent-blue); color: white; border: none;
    padding: 4px 12px; border-radius: 4px; font-size: 11px;
    font-weight: 600; cursor: pointer; transition: opacity 0.2s;
  }
  .save-button:hover:not(:disabled) { filter: brightness(1.1); }
  .save-button:disabled { opacity: 0.3; cursor: default; }
</style>
