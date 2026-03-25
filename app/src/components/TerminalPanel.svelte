<script>
  import { onMount, onDestroy } from "svelte";
  import { Terminal } from "@xterm/xterm";
  import { FitAddon } from "@xterm/addon-fit";
  import "@xterm/xterm/css/xterm.css";

  let { workspacePath = "", isOpen = $bindable(false) } = $props();

  let containerEl;
  let terminal = null;
  let fitAddon = null;
  let ws = null;
  let resizeObserver = null;
  let hasSelection = $state(false);

  // Derive WebSocket base URL
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

  function connect() {
    if (ws && ws.readyState !== WebSocket.CLOSED) return;
    ws = new WebSocket(getWsUrl(workspacePath));
    ws.onopen = () => { terminal?.writeln("\x1b[32m[Terminal connected]\x1b[0m\r"); fitAddon?.fit(); };
    ws.onmessage = (e) => { terminal?.write(e.data); };
    ws.onclose = () => { terminal?.writeln("\r\n\x1b[31m[Terminal disconnected — press any key to reconnect]\x1b[0m"); };
    ws.onerror = () => { terminal?.writeln("\r\n\x1b[31m[Connection error]\x1b[0m"); };
  }

  function disconnect() {
    ws?.close();
    ws = null;
  }

  $effect(() => {
    if (isOpen && terminal) {
      connect();
      setTimeout(() => { fitAddon?.fit(); terminal?.focus(); }, 50);
    } else if (!isOpen) {
      disconnect();
    }
  });

  // ── Send terminal content to AI chat ────────────────────────────────────────
  function getLastLines(n = 50) {
    if (!terminal) return "";
    const buf = terminal.buffer.active;
    const end = buf.baseY + buf.cursorY;
    const start = Math.max(0, end - n);
    const lines = [];
    for (let i = start; i <= end; i++) {
      const line = buf.getLine(i);
      if (line) lines.push(line.translateToString(true));
    }
    // Strip trailing blank lines
    while (lines.length && !lines[lines.length - 1].trim()) lines.pop();
    return lines.join("\n");
  }

  function sendToAI() {
    if (!terminal) return;
    const selected = terminal.getSelection().trim();
    const content = selected || getLastLines(60);
    if (!content) return;

    const label = selected ? "selected terminal text" : "terminal output";
    const query = `Here is ${label}:\n\n\`\`\`\n${content}\n\`\`\`\n\nWhat does this mean? Is there an error I should fix?`;

    window.dispatchEvent(new CustomEvent("chat-prefill", {
      detail: { query, autoSend: false }
    }));
  }

  onMount(() => {
    terminal = new Terminal({
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

    fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(containerEl);
    fitAddon.fit();

    terminal.onData((data) => {
      if (ws?.readyState === WebSocket.OPEN) ws.send(data);
      else connect();
    });

    // Ctrl+V / Ctrl+Shift+V → paste from clipboard
    terminal.attachCustomKeyEventHandler((e) => {
      if (e.type === "keydown" && (e.ctrlKey || e.metaKey) && (e.key === "v" || e.key === "V")) {
        navigator.clipboard.readText().then((text) => {
          if (text && ws?.readyState === WebSocket.OPEN) ws.send(text);
        }).catch(() => {});
        return false; // prevent xterm default
      }
      return true;
    });

    // Also handle browser-level paste events (right-click paste, middle-click on Linux)
    containerEl.addEventListener("paste", (e) => {
      const text = e.clipboardData?.getData("text");
      if (text && ws?.readyState === WebSocket.OPEN) ws.send(text);
      e.preventDefault();
    });

    terminal.onResize(({ cols, rows }) => {
      if (ws?.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "resize", cols, rows }));
      }
    });

    // Track selection state to update button label
    terminal.onSelectionChange(() => {
      hasSelection = !!terminal.getSelection().trim();
    });

    resizeObserver = new ResizeObserver(() => { if (isOpen) fitAddon?.fit(); });
    resizeObserver.observe(containerEl);

    if (isOpen) connect();
  });

  onDestroy(() => {
    resizeObserver?.disconnect();
    disconnect();
    terminal?.dispose();
  });
</script>

<div class="terminal-wrap">
  <div class="terminal-toolbar">
    <button class="ask-ai-btn" onclick={sendToAI} title="Send to AI chat">
      ✦ {hasSelection ? "Ask AI about selection" : "Ask AI about output"}
    </button>
  </div>
  <div class="terminal-body" bind:this={containerEl}></div>
</div>

<style>
  .terminal-wrap {
    width: 100%;
    height: 100%;
    display: flex;
    flex-direction: column;
    background: #0d1117;
  }

  .terminal-toolbar {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 3px 8px;
    background: #0d1117;
    border-bottom: 1px solid #21262d;
    flex-shrink: 0;
  }

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
  }
  .ask-ai-btn:hover {
    background: rgba(88, 166, 255, 0.2);
    border-color: rgba(88, 166, 255, 0.6);
  }

  .terminal-body {
    flex: 1;
    overflow: hidden;
    padding: 4px 8px;
    box-sizing: border-box;
  }

  .terminal-wrap :global(.xterm) { height: 100%; }
  .terminal-wrap :global(.xterm-viewport) { background: transparent !important; }
</style>
