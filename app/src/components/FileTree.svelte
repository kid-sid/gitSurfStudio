<script>
  import { onMount, tick } from "svelte";
  import { getFileTree, writeFile, createDirectory, deleteFile, deleteDirectory, renameEntry } from "../lib/api.js";

  let { workspacePath = "", onfileselect, onworkspaceopen } = $props();

  let files = $state([]);
  let expandedDirs = $state(new Set());
  let isLoading = $state(false);
  let showNewFileInput = $state(false);
  let newFileName = $state('');
  let newFileDir = $state('');
  let isCreating = $state(false);
  let createMode = $state('file'); // 'file' or 'folder'

  // Context menu state
  let contextMenu = $state({ show: false, x: 0, y: 0, item: null });

  // Rename state
  let renaming = $state({ active: false, path: '', name: '', name_orig: '', isDir: false });

  // Re-fetch when workspace changes
  let lastWorkspacePath = "";
  $effect(() => {
    if (workspacePath && workspacePath !== lastWorkspacePath) {
      lastWorkspacePath = workspacePath;
      loadFiles();
    }
  });

  onMount(() => {
    const handleBranchChange = () => {
      if (workspacePath) loadFiles();
    };
    const handleClickOutside = () => {
      contextMenu = { show: false, x: 0, y: 0, item: null };
    };
    window.addEventListener('branch-changed', handleBranchChange);
    window.addEventListener('click', handleClickOutside);

    return () => {
      window.removeEventListener('branch-changed', handleBranchChange);
      window.removeEventListener('click', handleClickOutside);
    };
  });

  async function loadFiles() {
    if (isLoading) return;
    isLoading = true;
    try {
      const tree = await getFileTree(workspacePath);
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

  function collapseAll() {
    expandedDirs = new Set();
  }

  // ── Create new file/folder ──────────────────────────────────────────────

  function startCreate(mode, dir = '') {
    createMode = mode;
    newFileDir = dir;
    newFileName = '';
    showNewFileInput = true;
    
    if (dir) {
      const next = new Set(expandedDirs);
      next.add(dir);
      expandedDirs = next;
    }
  }

  async function createNewEntry() {
    if (isCreating || !showNewFileInput) return;
    const name = newFileName.trim();
    if (!name) { 
      showNewFileInput = false; 
      return; 
    }

    isCreating = true;
    let targetPath = name;
    if (newFileDir) {
        targetPath = `${newFileDir}/${name}`.replace(/\\/g, '/');
    }

    try {
      if (createMode === 'folder') {
        await createDirectory(targetPath);
      } else {
        await writeFile(targetPath, '');
      }
      showNewFileInput = false;
      await loadFiles();
      
      if (createMode === 'file') {
        const fullPath = targetPath.includes(workspacePath) ? targetPath : `${workspacePath}/${targetPath}`;
        selectFile(fullPath);
      }
    } catch (e) {
      console.error(`Failed to create ${createMode}:`, e);
    } finally {
      isCreating = false;
      showNewFileInput = false;
    }
  }

  function handleNewEntryKeydown(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      createNewEntry();
    }
    if (e.key === 'Escape') {
      showNewFileInput = false;
    }
  }

  function selectName(node) {
    const dotIndex = renaming.name.lastIndexOf('.');
    const end = (dotIndex > 0 && !renaming.isDir) ? dotIndex : renaming.name.length;
    tick().then(() => {
      node.setSelectionRange(0, end);
      node.focus();
    });
  }

  // ── Rename ──────────────────────────────────────────────────────────────

  function startRename(item) {
    renaming = { 
      active: true, 
      path: item.path, 
      name: item.name, 
      name_orig: item.name, 
      isDir: item.type === 'dir' 
    };
  }

  async function commitRename() {
    if (!renaming.active) return;
    const newName = renaming.name.trim();
    if (!newName || newName === renaming.name_orig) {
      renaming = { active: false, path: '', name: '', name_orig: '', isDir: false };
      return;
    }

    const parent = getParentPath(renaming.path);
    const newPath = `${parent}/${newName}`;

    try {
      await renameEntry(renaming.path, newPath);
      await loadFiles();
    } catch (e) {
      console.error("Failed to rename:", e);
    } finally {
      renaming = { active: false, path: '', name: '', name_orig: '', isDir: false };
    }
  }

  function handleRenameKeydown(e) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commitRename();
    }
    if (e.key === 'Escape') {
      renaming = { active: false, path: '', name: '', name_orig: '', isDir: false };
    }
  }

  function getParentPath(filePath) {
    const parts = filePath.replace(/\\/g, '/').split('/');
    parts.pop();
    return parts.join('/');
  }

  // ── Delete ──────────────────────────────────────────────────────────────

  async function confirmDelete(item) {
    const label = item.type === 'dir' ? `folder "${item.name}"` : `file "${item.name}"`;
    if (!confirm(`Are you sure you want to delete ${label}?`)) return;

    try {
      if (item.type === 'dir') {
        await deleteDirectory(item.path);
      } else {
        await deleteFile(item.path);
      }
      await loadFiles();
    } catch (e) {
      console.error("Failed to delete:", e);
    }
  }

  // ── Context Menu Actions ────────────────────────────────────────────────
  
  function openContextMenu(e, item) {
    e.preventDefault();
    e.stopPropagation();
    const menuHeight = item?.type === 'dir' ? 200 : 150;
    let y = e.clientY;
    if (y + menuHeight > window.innerHeight) {
      y = Math.max(0, y - menuHeight);
    }
    contextMenu = { show: true, x: e.clientX, y, item };
  }

  function handleContextAction(action) {
    const item = contextMenu.item;
    contextMenu.show = false;
    if (!item) return;

    switch (action) {
      case 'newFile':   startCreate('file', item.type === 'dir' ? item.path : getParentPath(item.path)); break;
      case 'newFolder': startCreate('folder', item.type === 'dir' ? item.path : getParentPath(item.path)); break;
      case 'rename':    startRename(item); break;
      case 'delete':    confirmDelete(item); break;
      case 'copyPath':  navigator.clipboard.writeText(item.path); break;
      case 'copyRel':
        const rel = item.path.replace(workspacePath, '').replace(/^[\\\/]/, '').replace(/\\/g, '/');
        navigator.clipboard.writeText(rel);
        break;
    }
  }

  // ── Icons ───────────────────────────────────────────────────────────────

  function getFileIcon(name, isDir = false) {
    if (isDir) return "📁";
    const ext = name.split(".").pop().toLowerCase();
    const icons = {
      py: "🐍", js: "📜", ts: "📘", svelte: "🔥",
      md: "📝", json: "📋", toml: "⚙️", rs: "🦀",
      html: "🌐", css: "🎨", txt: "📄",
      png: "🖼️", jpg: "🖼️", svg: "📐",
      yml: "⚙️", yaml: "⚙️", dockerfile: "🐳",
      lock: "🔒", gitignore: "🐙", env: "🔑",
      sh: "🐚", bash: "🐚", sql: "🛢️",
      go: "🐹", rb: "💎", php: "🐘",
      java: "☕", cpp: "💠", c: "💠"
    };
    if (name.toLowerCase() === 'dockerfile') return "🐳";
    if (name.toLowerCase() === 'makefile') return "🛠️";
    return icons[ext] || "📄";
  }
</script>

{#snippet treeNode(item, depth = 0)}
  {#if renaming.active && renaming.path === item.path}
     <div class="new-input-container" style="padding-left: {depth * 12 + 20}px">
       <span class="item-icon">{getFileIcon(item.name, item.type === 'dir')}</span>
       <!-- svelte-ignore a11y_autofocus -->
       <input
         type="text"
         use:selectName
         bind:value={renaming.name}
         onkeydown={handleRenameKeydown}
         onblur={commitRename}
         autofocus
       />
     </div>
  {:else if item.type === "dir"}
    <button
      class="tree-item tree-item--dir"
      class:expanded={expandedDirs.has(item.path)}
      style="padding-left: {depth * 12 + 10}px"
      onclick={() => toggleDir(item.path)}
      oncontextmenu={(e) => openContextMenu(e, item)}
    >
      <span class="chevron">{expandedDirs.has(item.path) ? "▾" : "▸"}</span>
      <span class="item-icon">📁</span>
      <span class="item-name" title={item.path}>{item.name}</span>
    </button>
    
    {#if expandedDirs.has(item.path)}
      {#if showNewFileInput && newFileDir === item.path}
        <div class="new-input-container" style="padding-left: {(depth + 1) * 12 + 20}px">
          <span class="item-icon">{createMode === 'folder' ? '📁' : '📄'}</span>
          <!-- svelte-ignore a11y_autofocus -->
          <input
            type="text"
            bind:value={newFileName}
            onkeydown={handleNewEntryKeydown}
            onblur={createNewEntry}
            autofocus
          />
        </div>
      {/if}
      {#if item.children}
        {#each item.children as child}
          {@render treeNode(child, depth + 1)}
        {/each}
      {/if}
    {/if}
  {:else}
    <button
      class="tree-item tree-item--file"
      style="padding-left: {depth * 12 + 24}px"
      onclick={() => selectFile(item.path)}
      oncontextmenu={(e) => openContextMenu(e, item)}
    >
      <span class="item-icon">{getFileIcon(item.name)}</span>
      <span class="item-name" title={item.path}>{item.name}</span>
    </button>
  {/if}
{/snippet}

<div class="file-tree" role="none" oncontextmenu={(e) => {
    if (e.target.classList.contains('file-tree__list')) {
        e.preventDefault();
        contextMenu = { show: true, x: e.clientX, y: e.clientY, item: { type: 'dir', path: '', name: 'Root' } };
    }
}}>
  <div class="file-tree__header">
    <span class="file-tree__title">EXPLORER</span>
    <div class="file-tree__actions">
      <button class="file-tree__action" onclick={() => startCreate('file')} title="New File" aria-label="New File">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M14 4.5V14a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V2a2 2 0 0 1 2-2h4.5L14 4.5zM10 5h3L8.5 1.5V4a1 1 0 0 0 1 1z"/></svg>
      </button>
      <button class="file-tree__action" onclick={() => startCreate('folder')} title="New Folder" aria-label="New Folder">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M1 2.5A1.5 1.5 0 0 1 2.5 1h3.22a1.5 1.5 0 0 1 1.06.44l1.28 1.28a.5.5 0 0 0 .35.15h4.09A1.5 1.5 0 0 1 14 4.37V5h1v1h-1v7.5a1.5 1.5 0 0 1-1.5 1.5H2.5A1.5 1.5 0 0 1 1 13.5v-11zM2 4v9.5a.5.5 0 0 0 .5.5h10a.5.5 0 0 0 .5-.5V4H2z"/></svg>
      </button>
      <button class="file-tree__action" onclick={loadFiles} title="Refresh" aria-label="Refresh">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M8 3a5 5 0 1 0 5 5h-1a4 4 0 1 1-4-4V3z"/><path d="M11.5 2L14 4.5 11.5 7V2z"/></svg>
      </button>
      <button class="file-tree__action" onclick={collapseAll} title="Collapse All" aria-label="Collapse All">
        <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor"><path d="M1 8a.5.5 0 0 1 .5-.5h13a.5.5 0 0 1 0 1h-13A.5.5 0 0 1 1 8z"/></svg>
      </button>
    </div>
  </div>

  {#if workspacePath}
    <div class="file-tree__workspace">
      <span class="icon">📦</span>
      <span class="name">{workspacePath.split(/[/\\]/).pop() || workspacePath}</span>
    </div>
  {/if}

  <div class="file-tree__list">
    {#if isLoading && files.length === 0}
      <div class="file-tree__status">Scanning...</div>
    {:else if files.length === 0}
      <div class="file-tree__status">No files in workspace.</div>
    {:else}
      {#if showNewFileInput && !newFileDir}
        <div class="new-input-container" style="padding-left: 20px">
          <span class="item-icon">{createMode === 'folder' ? '📁' : '📄'}</span>
          <!-- svelte-ignore a11y_autofocus -->
          <input
            type="text"
            bind:value={newFileName}
            placeholder={createMode === 'folder' ? 'Folder name' : 'file.ext'}
            onkeydown={handleNewEntryKeydown}
            onblur={createNewEntry}
            autofocus
          />
        </div>
      {/if}

      {#each files as item}
        {@render treeNode(item)}
      {/each}
    {/if}
  </div>
</div>

{#if contextMenu.show}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="context-overlay" role="presentation" onclick={() => contextMenu.show = false}>
    <div class="context-menu" style="left: {contextMenu.x}px; top: {contextMenu.y}px">
      {#if contextMenu.item?.type === 'dir'}
        <button onclick={() => handleContextAction('newFile')}>New File</button>
        <button onclick={() => handleContextAction('newFolder')}>New Folder</button>
        <div class="sep"></div>
      {/if}
      <button onclick={() => handleContextAction('rename')}>Rename...</button>
      <button class="danger" onclick={() => handleContextAction('delete')}>Delete</button>
      <div class="sep"></div>
      <button onclick={() => handleContextAction('copyPath')}>Copy Path</button>
      <button onclick={() => handleContextAction('copyRel')}>Copy Relative Path</button>
    </div>
  </div>
{/if}

<style>
  .file-tree {
    height: 100%;
    display: flex;
    flex-direction: column;
    background: var(--bg-secondary);
    user-select: none;
    color: var(--text-primary);
    font-family: var(--font-sans, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif);
  }

  .file-tree__header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }

  .file-tree__title {
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-secondary);
    opacity: 0.7;
  }

  .file-tree__actions {
    display: flex;
    gap: 2px;
  }

  .file-tree__action {
    background: none;
    border: none;
    color: var(--text-muted);
    padding: 4px;
    border-radius: 4px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: all 0.1s ease;
  }
  .file-tree__action:hover {
    background: var(--bg-hover);
    color: var(--text-primary);
  }

  .file-tree__workspace {
    padding: 10px 14px;
    font-size: 12px;
    font-weight: 600;
    background: rgba(88, 166, 255, 0.05);
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--accent-blue);
  }

  .file-tree__list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }

  .file-tree__status {
    padding: 24px;
    text-align: center;
    font-size: 12px;
    color: var(--text-muted);
  }

  .tree-item {
    display: flex;
    align-items: center;
    width: 100%;
    padding: 2px 8px;
    border: none;
    background: none;
    color: inherit;
    font-size: 13px;
    text-align: left;
    cursor: pointer;
    white-space: nowrap;
    border-radius: 0;
    transition: background 0.05s;
    outline: none;
  }
  .tree-item:hover { background: var(--bg-hover); }
  .tree-item:focus-visible { background: rgba(88, 166, 255, 0.1); }

  .chevron {
    width: 14px;
    height: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 10px;
    color: var(--text-muted);
    transition: transform 0.1s;
  }
  .tree-item--dir.expanded .chevron { color: var(--text-primary); }

  .item-icon {
    margin-right: 6px;
    font-size: 14px;
    width: 16px;
    text-align: center;
    flex-shrink: 0;
  }

  .item-name {
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }

  .new-input-container {
    display: flex;
    align-items: center;
    padding: 2px 8px;
    background: var(--bg-hover);
  }
  .new-input-container input {
    flex: 1;
    background: var(--bg-primary);
    border: 1px solid var(--accent-blue);
    color: var(--text-primary);
    font-size: 13px;
    padding: 1px 6px;
    border-radius: 2px;
    outline: none;
    font-family: inherit;
  }

  .context-overlay {
    position: fixed;
    top: 0; left: 0; right: 0; bottom: 0;
    z-index: 1000;
  }

  .context-menu {
    position: absolute;
    min-width: 180px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 4px 0;
    box-shadow: 0 4px 20px rgba(0,0,0,0.5);
    animation: menuFadeIn 0.1s ease-out;
  }

  @keyframes menuFadeIn {
    from { opacity: 0; transform: scale(0.95); }
    to { opacity: 1; transform: scale(1); }
  }

  .context-menu button {
    display: block;
    width: 100%;
    padding: 6px 14px;
    text-align: left;
    background: none;
    border: none;
    color: var(--text-primary);
    font-size: 13px;
    cursor: pointer;
  }
  .context-menu button:hover {
    background: var(--bg-hover);
    color: var(--accent);
  }
  .context-menu button.danger { color: var(--accent-red); }
  .context-menu button.danger:hover {
    background: rgba(255, 71, 71, 0.1);
    color: #ff4747;
  }

  .sep {
    height: 1px;
    background: var(--border);
    margin: 4px 0;
  }

  /* Custom scrollbar */
  .file-tree__list::-webkit-scrollbar { width: 10px; }
  .file-tree__list::-webkit-scrollbar-track { background: transparent; }
  .file-tree__list::-webkit-scrollbar-thumb { background: #333; border: 3px solid var(--bg-secondary); border-radius: 10px; }
  .file-tree__list::-webkit-scrollbar-thumb:hover { background: #444; }
</style>
