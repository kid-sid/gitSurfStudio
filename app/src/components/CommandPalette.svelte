<script>
  import { getFileTree, getSymbols } from "../lib/api.js";

  let {
    workspacePath = "",
    activeFile = "",
    openFiles = [],
    onselect = null,
    oncommand = null,
  } = $props();

  let isOpen = $state(false);
  let query = $state("");
  let selectedIndex = $state(0);
  let inputEl;
  let mode = $state("files"); // "files" | "symbols" | "commands"

  // Cached data
  let fileList = $state([]);
  let symbolList = $state([]);
  let fileListLoaded = $state(false);

  const COMMANDS = [
    { id: "toggle-terminal", label: "Toggle Terminal", icon: ">_", shortcut: "Ctrl+`" },
    { id: "toggle-sidebar-explorer", label: "Show Explorer", icon: "📂" },
    { id: "toggle-sidebar-git", label: "Show Source Control", icon: "🌿" },
    { id: "toggle-sidebar-symbols", label: "Show Symbol Browser", icon: "🧩" },
    { id: "ask-ai", label: "Ask AI...", icon: "🌊" },
    { id: "toggle-preview", label: "Toggle Live Preview", icon: "🌐" },
    { id: "new-file", label: "New File", icon: "📄" },
    { id: "new-folder", label: "New Folder", icon: "📁" },
    { id: "go-home", label: "Go to Home", icon: "🏠" },
  ];

  let results = $derived.by(() => {
    const q = query.toLowerCase().trim();

    if (mode === "commands") {
      if (!q) return COMMANDS;
      return COMMANDS.filter(c => c.label.toLowerCase().includes(q));
    }

    if (mode === "symbols") {
      if (!q) return symbolList.slice(0, 50);
      return symbolList
        .filter(s => s.name.toLowerCase().includes(q) || (s.file || "").toLowerCase().includes(q))
        .slice(0, 50);
    }

    // Files mode
    if (!q) {
      // Show open files first, then recent
      const open = openFiles.map(p => ({ path: p, isOpen: true }));
      const rest = fileList
        .filter(p => !openFiles.includes(p))
        .slice(0, 30)
        .map(p => ({ path: p, isOpen: false }));
      return [...open, ...rest];
    }

    // Fuzzy-ish file search
    const parts = q.split(/[\s/\\]+/);
    return fileList
      .filter(p => {
        const lower = p.replace(/\\/g, "/").toLowerCase();
        return parts.every(part => lower.includes(part));
      })
      .slice(0, 50)
      .map(p => ({ path: p, isOpen: openFiles.includes(p) }));
  });

  // Reset selected index when results change
  $effect(() => {
    if (results) selectedIndex = 0;
  });

  export function open(startMode = "files") {
    mode = startMode;
    query = "";
    selectedIndex = 0;
    isOpen = true;

    if (!fileListLoaded && workspacePath) {
      loadFiles();
    }
    if (startMode === "symbols" && symbolList.length === 0 && workspacePath) {
      loadSymbols();
    }

    setTimeout(() => inputEl?.focus(), 0);
  }

  export function close() {
    isOpen = false;
    query = "";
  }

  async function loadFiles() {
    try {
      const tree = await getFileTree(workspacePath);
      fileList = flattenTree(tree.tree || []);
      fileListLoaded = true;
    } catch {
      fileList = [];
    }
  }

  async function loadSymbols() {
    try {
      const res = await getSymbols(workspacePath);
      symbolList = (res.symbols || []).map(s => ({
        name: s.name,
        kind: s.type || s.kind || "symbol",
        file: s.file || "",
        line: s.start_line || s.line || 1,
      }));
    } catch {
      symbolList = [];
    }
  }

  function flattenTree(nodes, prefix = "") {
    let paths = [];
    for (const node of nodes) {
      const full = prefix ? `${prefix}/${node.name}` : node.name;
      if (node.type === "file") {
        paths.push(full);
      }
      if (node.children) {
        paths = paths.concat(flattenTree(node.children, full));
      }
    }
    return paths;
  }

  function selectResult(item) {
    if (mode === "files") {
      const absPath = workspacePath
        ? workspacePath.replace(/\\/g, "/") + "/" + item.path.replace(/\\/g, "/")
        : item.path;
      onselect?.({ type: "file", path: absPath });
    } else if (mode === "symbols") {
      const absPath = workspacePath
        ? workspacePath.replace(/\\/g, "/") + "/" + item.file.replace(/\\/g, "/")
        : item.file;
      onselect?.({ type: "symbol", path: absPath, line: item.line });
    } else if (mode === "commands") {
      oncommand?.(item.id);
    }
    close();
  }

  function handleKeydown(e) {
    if (e.key === "Escape") {
      e.preventDefault();
      close();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      selectedIndex = Math.min(selectedIndex + 1, results.length - 1);
      scrollIntoView();
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      selectedIndex = Math.max(selectedIndex - 1, 0);
      scrollIntoView();
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (results[selectedIndex]) selectResult(results[selectedIndex]);
      return;
    }
  }

  function scrollIntoView() {
    setTimeout(() => {
      const el = document.querySelector(".palette__item--active");
      el?.scrollIntoView({ block: "nearest" });
    }, 0);
  }

  function handleInput() {
    // Detect mode prefix
    if (query.startsWith(">")) {
      mode = "commands";
      query = query.slice(1);
    } else if (query.startsWith("@")) {
      mode = "symbols";
      query = query.slice(1);
      if (symbolList.length === 0 && workspacePath) loadSymbols();
    }
  }

  function displayPath(path) {
    const parts = path.replace(/\\/g, "/").split("/");
    return parts.length <= 3 ? path : ".../" + parts.slice(-3).join("/");
  }

  function symbolIcon(kind) {
    const icons = {
      function: "ƒ", method: "ƒ", class: "C",
      variable: "V", constant: "K", interface: "I",
      module: "M", property: "P", enum: "E",
    };
    return icons[kind] || "S";
  }
</script>

{#if isOpen}
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="palette__backdrop" onclick={close} onkeydown={() => {}}>
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div class="palette" onclick={(e) => e.stopPropagation()} onkeydown={() => {}}>
      <div class="palette__input-row">
        <span class="palette__mode-badge">
          {mode === "files" ? "📄" : mode === "symbols" ? "🧩" : "⌘"}
        </span>
        <input
          bind:this={inputEl}
          bind:value={query}
          oninput={handleInput}
          onkeydown={handleKeydown}
          class="palette__input"
          placeholder={mode === "files"
            ? "Search files... (> for commands, @ for symbols)"
            : mode === "symbols"
              ? "Search symbols..."
              : "Search commands..."}
          spellcheck="false"
        />
        <div class="palette__mode-tabs">
          <button
            class="palette__tab"
            class:palette__tab--active={mode === "files"}
            onclick={() => { mode = "files"; query = ""; inputEl?.focus(); }}
          >Files</button>
          <button
            class="palette__tab"
            class:palette__tab--active={mode === "symbols"}
            onclick={() => { mode = "symbols"; query = ""; if (symbolList.length === 0 && workspacePath) loadSymbols(); inputEl?.focus(); }}
          >Symbols</button>
          <button
            class="palette__tab"
            class:palette__tab--active={mode === "commands"}
            onclick={() => { mode = "commands"; query = ""; inputEl?.focus(); }}
          >Commands</button>
        </div>
      </div>

      <div class="palette__results">
        {#if results.length === 0}
          <div class="palette__empty">No results found</div>
        {:else}
          {#each results as item, i}
            {#if mode === "files"}
              <button
                class="palette__item"
                class:palette__item--active={i === selectedIndex}
                onclick={() => selectResult(item)}
                onmouseenter={() => selectedIndex = i}
              >
                <span class="palette__item-icon">{item.isOpen ? "📝" : "📄"}</span>
                <span class="palette__item-name">{item.path.split(/[/\\]/).pop()}</span>
                <span class="palette__item-path">{displayPath(item.path)}</span>
              </button>
            {:else if mode === "symbols"}
              <button
                class="palette__item"
                class:palette__item--active={i === selectedIndex}
                onclick={() => selectResult(item)}
                onmouseenter={() => selectedIndex = i}
              >
                <span class="palette__item-icon palette__symbol-icon">{symbolIcon(item.kind)}</span>
                <span class="palette__item-name">{item.name}</span>
                <span class="palette__item-kind">{item.kind}</span>
                <span class="palette__item-path">{displayPath(item.file)}:{item.line}</span>
              </button>
            {:else}
              <button
                class="palette__item"
                class:palette__item--active={i === selectedIndex}
                onclick={() => selectResult(item)}
                onmouseenter={() => selectedIndex = i}
              >
                <span class="palette__item-icon">{item.icon}</span>
                <span class="palette__item-name">{item.label}</span>
                {#if item.shortcut}
                  <span class="palette__item-shortcut">{item.shortcut}</span>
                {/if}
              </button>
            {/if}
          {/each}
        {/if}
      </div>

      <div class="palette__footer">
        <span>↑↓ navigate</span>
        <span>↵ select</span>
        <span>esc close</span>
      </div>
    </div>
  </div>
{/if}

<style>
  .palette__backdrop {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0, 0, 0, 0.5);
    display: flex; justify-content: center; padding-top: 80px;
  }
  .palette {
    width: 560px; max-height: 420px;
    background: var(--bg-secondary, #161b22);
    border: 1px solid var(--border, #30363d);
    border-radius: 8px;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.5);
    display: flex; flex-direction: column;
    overflow: hidden;
    align-self: flex-start;
  }
  .palette__input-row {
    display: flex; align-items: center; gap: 8px;
    padding: 10px 12px;
    border-bottom: 1px solid var(--border, #30363d);
    flex-wrap: wrap;
  }
  .palette__mode-badge {
    font-size: 14px; flex-shrink: 0;
  }
  .palette__input {
    flex: 1; min-width: 200px;
    background: transparent; border: none; outline: none;
    color: var(--text-primary, #c9d1d9);
    font-size: 14px; font-family: var(--font-ui);
  }
  .palette__input::placeholder { color: var(--text-muted, #484f58); }
  .palette__mode-tabs {
    display: flex; gap: 2px; flex-shrink: 0;
  }
  .palette__tab {
    font-size: 10px; padding: 3px 8px; border-radius: 4px;
    background: transparent; color: var(--text-muted, #484f58);
    cursor: pointer; border: 1px solid transparent;
    text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600;
  }
  .palette__tab:hover { color: var(--text-secondary); }
  .palette__tab--active {
    color: var(--text-accent, #58a6ff);
    border-color: var(--text-accent, #58a6ff);
    background: rgba(88, 166, 255, 0.08);
  }

  .palette__results {
    flex: 1; overflow-y: auto; max-height: 320px;
  }
  .palette__empty {
    padding: 24px; text-align: center;
    color: var(--text-muted, #484f58); font-size: 13px;
  }
  .palette__item {
    display: flex; align-items: center; gap: 8px;
    width: 100%; padding: 7px 14px; text-align: left;
    background: transparent; color: var(--text-secondary, #8b949e);
    font-size: 13px; cursor: pointer; border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 0.03);
    transition: background 0.08s;
  }
  .palette__item:hover, .palette__item--active {
    background: var(--bg-hover, #21262d);
    color: var(--text-primary, #c9d1d9);
  }
  .palette__item-icon {
    font-size: 13px; flex-shrink: 0; width: 18px; text-align: center;
  }
  .palette__symbol-icon {
    font-family: var(--font-mono); font-weight: 700;
    color: var(--accent-blue, #58a6ff); font-size: 12px;
  }
  .palette__item-name {
    font-weight: 500; flex-shrink: 0;
    color: var(--text-primary, #c9d1d9);
  }
  .palette__item-kind {
    font-size: 10px; color: var(--text-muted); text-transform: uppercase;
    padding: 1px 5px; background: rgba(255, 255, 255, 0.05); border-radius: 3px;
    flex-shrink: 0;
  }
  .palette__item-path {
    flex: 1; text-align: right;
    font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted, #484f58);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .palette__item-shortcut {
    margin-left: auto;
    font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted); padding: 1px 6px;
    background: rgba(255, 255, 255, 0.06); border-radius: 3px;
  }

  .palette__footer {
    display: flex; gap: 16px; justify-content: center;
    padding: 6px 12px;
    border-top: 1px solid var(--border, #30363d);
    font-size: 10px; color: var(--text-muted, #484f58);
  }
</style>
