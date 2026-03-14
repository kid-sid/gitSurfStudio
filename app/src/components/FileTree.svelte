<script>
  import { onMount } from "svelte";
  import { getFileTree } from "../lib/api.js";

  let { workspacePath = "", onfileselect, onworkspaceopen } = $props();

  let files = $state([]);
  let expandedDirs = $state(new Set());
  let isLoading = $state(false);

  // Re-fetch when workspace changes
  $effect(() => {
    if (workspacePath) {
      loadFiles();
    }
  });

  async function loadFiles() {
    isLoading = true;
    try {
      const tree = await getFileTree(workspacePath);
      // Backend returns build_tree(path) which is the root node
      files = tree.children || [];
    } catch (e) {
      console.error("Failed to load files:", e);
    } finally {
      isLoading = false;
    }
  }

  function toggleDir(path) {
    const next = new Set(expandedDirs);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
    }
    expandedDirs = next;
  }

  function selectFile(path) {
    if (onfileselect) onfileselect({ detail: { path } });
  }

  function openWorkspace() {
    if (onworkspaceopen) {
      onworkspaceopen({ detail: { path: "" } });
    }
  }

  function getFileIcon(name) {
    const ext = name.split(".").pop().toLowerCase();
    const icons = {
      py: "🐍", js: "📜", ts: "📘", svelte: "🔥",
      md: "📝", json: "📋", toml: "⚙️", rs: "🦀",
      html: "🌐", css: "🎨", txt: "📄",
    };
    return icons[ext] || "📄";
  }

  // Recursive component snippet (Svelte 5)
</script>

{#snippet treeNode(item, depth = 0)}
  {#if item.type === "dir"}
    <button 
      class="tree-item tree-item--dir" 
      style="padding-left: {depth * 12 + 14}px"
      onclick={() => toggleDir(item.path)}
    >
      <span class="tree-item__icon">{expandedDirs.has(item.path) ? "▾" : "▸"}</span>
      <span class="tree-item__icon">📁</span>
      <span class="tree-item__name">{item.name}</span>
    </button>
    {#if expandedDirs.has(item.path) && item.children}
      {#each item.children as child}
        {@render treeNode(child, depth + 1)}
      {/each}
    {/if}
  {:else}
    <button 
      class="tree-item tree-item--file" 
      style="padding-left: {depth * 12 + 26}px"
      onclick={() => selectFile(item.path)}
    >
      <span class="tree-item__icon">{getFileIcon(item.name)}</span>
      <span class="tree-item__name">{item.name}</span>
    </button>
  {/if}
{/snippet}

<div class="file-tree">
  <div class="file-tree__header">
    <span class="file-tree__title">EXPLORER</span>
    <div class="file-tree__actions">
      <button class="file-tree__action" onclick={loadFiles} title="Refresh">🔄</button>
      <button class="file-tree__action" onclick={openWorkspace} title="Switch Project">🚪</button>
    </div>
  </div>

  {#if workspacePath}
    <div class="file-tree__workspace">
      {workspacePath.split(/[/\\]/).pop() || workspacePath}
    </div>
  {/if}

  <div class="file-tree__list">
    {#if isLoading}
      <div class="file-tree__loading">Scanning...</div>
    {:else if files.length === 0}
      <div class="file-tree__empty">No files found or path empty.</div>
    {:else}
      {#each files as item}
        {@render treeNode(item)}
      {/each}
    {/if}
  </div>
</div>

<style>
  .file-tree { height: 100%; display: flex; flex-direction: column; background: var(--bg-secondary); }
  .file-tree__header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 14px; border-bottom: 1px solid var(--border);
  }
  .file-tree__title {
    font-size: 11px; font-weight: 600; letter-spacing: 1px; color: var(--text-secondary);
  }
  .file-tree__actions { display: flex; gap: 4px; }
  .file-tree__action {
    background: none; font-size: 14px; padding: 2px 4px; border-radius: var(--radius-sm);
  }
  .file-tree__action:hover { background: var(--bg-hover); }
  
  .file-tree__workspace {
    padding: 8px 14px; font-size: 12px; font-weight: 600;
    color: var(--text-accent); background: rgba(88, 166, 255, 0.05);
    border-bottom: 1px solid var(--border);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .file-tree__list { flex: 1; overflow-y: auto; padding: 4px 0; }
  .file-tree__loading, .file-tree__empty {
    padding: 20px; font-size: 12px; color: var(--text-muted); text-align: center;
  }

  .tree-item {
    display: flex; align-items: center; gap: 4px; width: 100%;
    padding: 4px 14px; background: none; color: var(--text-primary);
    font-size: 13px; text-align: left; border-radius: 0;
    white-space: nowrap; border: none; outline: none;
    cursor: pointer;
  }
  .tree-item:hover { background: var(--bg-hover); }
  .tree-item__icon { font-size: 12px; width: 16px; text-align: center; flex-shrink: 0; }
  .tree-item__name { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
</style>
