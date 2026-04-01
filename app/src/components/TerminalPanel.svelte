<script>
  import { onDestroy } from "svelte";
  import { Terminal } from "@xterm/xterm";
  import { FitAddon } from "@xterm/addon-fit";
  import "@xterm/xterm/css/xterm.css";

  let { workspacePath = "", isOpen = $bindable(false) } = $props();

  // ── Tab state ──────────────────────────────────────────────────────────────
  let tabs = $state([{ id: 1, label: "Terminal 1" }]);
  let activeTabId = $state(1);
  let nextId = 2;
  let hasSelection = $state(false);
  let pendingCommand = null;

  /** @type {Record<number, { terminal: Terminal, fitAddon: FitAddon, ws: WebSocket|null }>} */
  const instances = {};

  // ── Helpers ────────────────────────────────────────────────────────────────
  function getWsUrl(path) {
    if (typeof window === "undefined") return "ws://127.0.0.1:8002/terminal";
    const isRemote =
      window.location.hostname !== "localhost" &&
      window.location.hostname !== "127.0.0.1";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const base = isRemote
      ? `${proto}//${window.location.hostname}:8002/terminal`
      : "ws://127.0.0.1:8002/terminal";
    return path ? `${base}?cwd=${encodeURIComponent(path)}` : base;
  }

  function getLastLines(terminal, n = 50) {
    if (!terminal) return "";
    const buf = terminal.buffer.active;
    const end = buf.baseY + buf.cursorY;
    const start = Math.max(0, end - n);
    const lines = [];
    for (let i = start; i <= end; i++) {
      const line = buf.getLine(i);
      if (line) lines.push(line.translateToString(true));
    }
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    return lines.join("\n");
  }

  // ── Connection management ──────────────────────────────────────────────────
  function connectTab(id) {
    const inst = instances[id];
    if (!inst || (inst.ws && inst.ws.readyState !== WebSocket.CLOSED)) return;
    inst.ws = new WebSocket(getWsUrl(workspacePath));
    inst.ws.onopen = () => {
      inst.terminal?.writeln("\x1b[32m[Terminal connected]\x1b[0m\r");
      inst.fitAddon?.fit();
      // FitAddon.fit() is a no-op when dimensions are unchanged — explicitly sync PTY size
      const t = inst.terminal;
      if (t) inst.ws.send(JSON.stringify({ type: "resize", cols: t.cols, rows: t.rows }));
      // Flush any queued run command
      if (pendingCommand && id === activeTabId) {
        const cmd = pendingCommand;
        pendingCommand = null;
        setTimeout(() => inst.ws?.send(cmd + "\r"), 80);
      }
    };
    inst.ws.onmessage = (e) => {
      // Fix cls: \x1b[2J clears visible area but leaves scrollback, causing the
      // cursor to appear in the wrong place. Clearing scrollback first fixes it.
      if (e.data.includes("\x1b[2J")) {
        inst.terminal?.clear();
      }
      inst.terminal?.write(e.data);
    };
    inst.ws.onclose = () => {
      inst.terminal?.writeln(
        "\r\n\x1b[31m[Terminal disconnected — press any key to reconnect]\x1b[0m"
      );
      inst.ws = null;
    };
    inst.ws.onerror = () => {
      inst.terminal?.writeln("\r\n\x1b[31m[Connection error]\x1b[0m");
    };
  }

  function disconnectTab(id) {
    const inst = instances[id];
    if (!inst) return;
    inst.ws?.close();
    inst.ws = null;
  }

  // ── Effects ────────────────────────────────────────────────────────────────
  $effect(() => {
    if (isOpen) {
      for (const id of Object.keys(instances).map(Number)) connectTab(id);
      setTimeout(() => {
        instances[activeTabId]?.fitAddon?.fit();
        instances[activeTabId]?.terminal?.focus();
      }, 50);
    } else {
      for (const id of Object.keys(instances).map(Number)) disconnectTab(id);
    }
  });

  $effect(() => {
    const id = activeTabId; // reactive dependency
    hasSelection = !!(instances[id]?.terminal?.getSelection().trim());
    // Wait for DOM to show the newly active terminal before fitting
    setTimeout(() => {
      instances[id]?.fitAddon?.fit();
      instances[id]?.terminal?.focus();
    }, 20);
  });

  // ── Tab actions ────────────────────────────────────────────────────────────
  function addTab() {
    const id = nextId++;
    tabs.push({ id, label: `Terminal ${id}` });
    activeTabId = id;
  }

  function closeTab(id, e) {
    e.stopPropagation();
    const inst = instances[id];
    if (inst) {
      inst.ws?.close();
      inst.terminal?.dispose();
      delete instances[id];
    }
    const idx = tabs.findIndex((t) => t.id === id);
    tabs.splice(idx, 1);
    if (activeTabId === id) {
      activeTabId = tabs[Math.min(idx, tabs.length - 1)]?.id ?? -1;
    }
  }

  // ── Run command from outside ───────────────────────────────────────────────
  function sendCommand(cmd) {
    const inst = instances[activeTabId];
    if (inst?.ws?.readyState === WebSocket.OPEN) {
      inst.ws.send(cmd + "\r");
    } else {
      pendingCommand = cmd;
      connectTab(activeTabId);
    }
  }

  function handleRunCommand(e) {
    sendCommand(e.detail.command);
  }

  $effect(() => {
    window.addEventListener("terminal-send-command", handleRunCommand);
    return () => window.removeEventListener("terminal-send-command", handleRunCommand);
  });

  // ── Ask AI ─────────────────────────────────────────────────────────────────
  function sendToAI() {
    const inst = instances[activeTabId];
    if (!inst?.terminal) return;
    const selected = inst.terminal.getSelection().trim();
    const content = selected || getLastLines(inst.terminal, 60);
    if (!content) return;
    const label = selected ? "selected terminal text" : "terminal output";
    const query = `Here is ${label}:\n\n\`\`\`\n${content}\n\`\`\`\n\nWhat does this mean? Is there an error I should fix?`;
    window.dispatchEvent(
      new CustomEvent("chat-prefill", { detail: { query, autoSend: false } })
    );
  }

  // ── Svelte action: initialise xterm for each tab div ──────────────────────
  function terminalAction(node, tabId) {
    const term = new Terminal({
      theme: {
        background:          "#0d1117",
        foreground:          "#c9d1d9",
        cursor:              "#58a6ff",
        cursorAccent:        "#0d1117",
        selectionBackground: "#264f7880",
        black:   "#484f58", brightBlack:   "#6e7681",
        red:     "#ff7b72", brightRed:     "#ffa198",
        green:   "#3fb950", brightGreen:   "#56d364",
        yellow:  "#e3b341", brightYellow:  "#f0c955",
        blue:    "#58a6ff", brightBlue:    "#79c0ff",
        magenta: "#d2a8ff", brightMagenta: "#e6b8ff",
        cyan:    "#76e3ea", brightCyan:    "#b3f0ff",
        white:   "#b1bac4", brightWhite:   "#ffffff",
      },
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace",
      fontSize: 13,
      lineHeight: 1.4,
      cursorBlink: true,
      cursorStyle: "block",
      scrollback: 2000,
      allowTransparency: false,
      convertEol: true,
    });

    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.open(node);
    fitAddon.fit();

    instances[tabId] = { terminal: term, fitAddon, ws: null };

    term.onData((data) => {
      const inst = instances[tabId];
      if (inst?.ws?.readyState === WebSocket.OPEN) inst.ws.send(data);
      else connectTab(tabId);
    });

    // Ctrl+V / Cmd+V → paste from clipboard
    term.attachCustomKeyEventHandler((e) => {
      if (
        e.type === "keydown" &&
        (e.ctrlKey || e.metaKey) &&
        (e.key === "v" || e.key === "V")
      ) {
        navigator.clipboard
          .readText()
          .then((text) => {
            const inst = instances[tabId];
            if (text && inst?.ws?.readyState === WebSocket.OPEN) inst.ws.send(text);
          })
          .catch(() => {});
        return false;
      }
      return true;
    });

    // Right-click / middle-click paste
    node.addEventListener("paste", (e) => {
      const text = e.clipboardData?.getData("text");
      const inst = instances[tabId];
      if (text && inst?.ws?.readyState === WebSocket.OPEN) inst.ws.send(text);
      e.preventDefault();
    });

    term.onResize(({ cols, rows }) => {
      const inst = instances[tabId];
      if (inst?.ws?.readyState === WebSocket.OPEN) {
        inst.ws.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    term.onSelectionChange(() => {
      if (activeTabId === tabId) {
        hasSelection = !!term.getSelection().trim();
      }
    });

    const ro = new ResizeObserver(() => {
      if (isOpen && activeTabId === tabId) fitAddon?.fit();
    });
    ro.observe(node);

    if (isOpen) connectTab(tabId);

    return {
      destroy() {
        ro.disconnect();
        const inst = instances[tabId];
        if (inst) {
          inst.ws?.close();
          inst.terminal?.dispose();
          delete instances[tabId];
        }
      },
    };
  }

  onDestroy(() => {
    for (const id of Object.keys(instances).map(Number)) {
      const inst = instances[id];
      inst?.ws?.close();
      inst?.terminal?.dispose();
    }
  });
</script>

<div class="terminal-wrap">
  <!-- Tab bar -->
  <div class="terminal-tabs">
    {#each tabs as tab (tab.id)}
      <button
        class="tab-btn"
        class:active={activeTabId === tab.id}
        onclick={() => (activeTabId = tab.id)}
      >
        {tab.label}
        {#if tabs.length > 1}
          <span
            class="tab-close"
            onclick={(e) => closeTab(tab.id, e)}
            role="button"
            tabindex="-1"
          >×</span>
        {/if}
      </button>
    {/each}

    <button class="tab-add" onclick={addTab} title="New terminal">+</button>

    <div class="tab-spacer"></div>

    <button class="ask-ai-btn" onclick={sendToAI} title="Send to AI chat">
      ✦ {hasSelection ? "Ask AI about selection" : "Ask AI about output"}
    </button>
  </div>

  <!-- One terminal body per tab; inactive ones are hidden via display:none -->
  {#each tabs as tab (tab.id)}
    <div
      class="terminal-body"
      class:hidden={activeTabId !== tab.id}
      use:terminalAction={tab.id}
    ></div>
  {/each}
</div>

<style>
  .terminal-wrap {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: #0d1117;
  }

  /* ── Tab bar ──────────────────────────────────────────────────────────────── */
  .terminal-tabs {
    display: flex;
    align-items: center;
    padding: 0 4px;
    background: #0d1117;
    border-bottom: 1px solid #21262d;
    flex-shrink: 0;
    gap: 2px;
    min-height: 30px;
    overflow-x: auto;
    scrollbar-width: none;
  }
  .terminal-tabs::-webkit-scrollbar { display: none; }

  .tab-btn {
    background: transparent;
    color: #8b949e;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 5px 10px;
    font-size: 11px;
    cursor: pointer;
    white-space: nowrap;
    display: flex;
    align-items: center;
    gap: 5px;
    transition: color 0.15s;
    flex-shrink: 0;
  }
  .tab-btn:hover { color: #c9d1d9; }
  .tab-btn.active {
    color: #e6edf3;
    border-bottom-color: #58a6ff;
  }

  .tab-close {
    font-size: 14px;
    line-height: 1;
    opacity: 0.45;
    padding: 0 1px;
    transition: opacity 0.15s;
  }
  .tab-close:hover { opacity: 1; }

  .tab-add {
    background: transparent;
    color: #8b949e;
    border: none;
    padding: 0 8px;
    font-size: 18px;
    line-height: 1;
    cursor: pointer;
    flex-shrink: 0;
    transition: color 0.15s;
  }
  .tab-add:hover { color: #c9d1d9; }

  .tab-spacer { flex: 1; }

  .ask-ai-btn {
    background: rgba(88, 166, 255, 0.1);
    color: #58a6ff;
    border: 1px solid rgba(88, 166, 255, 0.3);
    border-radius: 4px;
    padding: 2px 10px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .ask-ai-btn:hover {
    background: rgba(88, 166, 255, 0.2);
    border-color: rgba(88, 166, 255, 0.6);
  }

  /* ── Terminal bodies ──────────────────────────────────────────────────────── */
  .terminal-body {
    flex: 1;
    overflow: hidden;
    box-sizing: border-box;
  }
  .terminal-body.hidden { display: none; }

  .terminal-wrap :global(.xterm) { height: 100%; }
  .terminal-wrap :global(.xterm-viewport) { background: transparent !important; }
</style>
