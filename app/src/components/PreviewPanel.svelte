<script>
  import { onDestroy } from "svelte";
  import { startPreview, stopPreview } from "../lib/api.js";

  let {
    workspacePath = "",
    isOpen = $bindable(false),
  } = $props();

  let previewUrl = $state("");
  let isStarting = $state(false);
  let error = $state("");
  let port = $state(3000);
  let customCommand = $state("");
  let showConfig = $state(false);
  let iframeEl;

  async function handleStart() {
    isStarting = true;
    error = "";
    try {
      const res = await startPreview(workspacePath, customCommand || undefined, port);
      previewUrl = res.url;
      port = res.port;
      showConfig = false;
    } catch (e) {
      error = e.message;
    } finally {
      isStarting = false;
    }
  }

  async function handleStop() {
    try {
      await stopPreview(workspacePath);
    } catch {
      // ignore
    }
    previewUrl = "";
  }

  function handleRefresh() {
    if (iframeEl && previewUrl) {
      iframeEl.src = previewUrl;
    }
  }

  // Auto-refresh on file changes
  function onFsChanged() {
    if (previewUrl && iframeEl) {
      // Debounce: the dev server's HMR usually handles this,
      // but for non-HMR servers we force-reload
      clearTimeout(onFsChanged._timer);
      onFsChanged._timer = setTimeout(() => {
        iframeEl.contentWindow?.location.reload();
      }, 1000);
    }
  }

  $effect(() => {
    if (isOpen && previewUrl) {
      window.addEventListener("fs-batch", onFsChanged);
      return () => window.removeEventListener("fs-batch", onFsChanged);
    }
  });

  onDestroy(() => {
    // Don't auto-stop the server on panel close — user may want it running
  });
</script>

<div class="preview">
  <div class="preview__header">
    <span class="preview__title">Preview</span>
    {#if previewUrl}
      <span class="preview__url">{previewUrl}</span>
      <button class="preview__btn" onclick={handleRefresh} title="Refresh">↻</button>
      <button class="preview__btn preview__btn--stop" onclick={handleStop} title="Stop server">■</button>
    {:else}
      <button
        class="preview__btn preview__btn--start"
        onclick={handleStart}
        disabled={isStarting}
      >
        {isStarting ? "Starting..." : "▶ Start"}
      </button>
      <button class="preview__btn" onclick={() => showConfig = !showConfig} title="Configure">⚙</button>
    {/if}
    <button class="preview__btn preview__btn--close" onclick={() => isOpen = false} title="Close">×</button>
  </div>

  {#if showConfig && !previewUrl}
    <div class="preview__config">
      <label>
        <span>Command</span>
        <input
          type="text"
          bind:value={customCommand}
          placeholder="Auto-detect (npm run dev, etc.)"
        />
      </label>
      <label>
        <span>Port</span>
        <input
          type="number"
          bind:value={port}
          min="1024"
          max="65535"
        />
      </label>
    </div>
  {/if}

  {#if error}
    <div class="preview__error">{error}</div>
  {/if}

  <div class="preview__body">
    {#if previewUrl}
      <iframe
        bind:this={iframeEl}
        src={previewUrl}
        title="Live Preview"
        class="preview__iframe"
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-modals"
      ></iframe>
    {:else if isStarting}
      <div class="preview__status">
        <div class="spinner"></div>
        <span>Starting dev server...</span>
      </div>
    {:else}
      <div class="preview__status">
        <div class="preview__status-icon">🌐</div>
        <p>Click <strong>Start</strong> to launch a live preview.</p>
        <p class="preview__hint">Auto-detects Vite, Next.js, Svelte, Django, Flask</p>
      </div>
    {/if}
  </div>
</div>

<style>
  .preview {
    height: 100%; display: flex; flex-direction: column;
    background: var(--bg-primary); color: var(--text-primary);
  }
  .preview__header {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 10px; flex-shrink: 0;
    background: var(--bg-secondary);
    border-bottom: 1px solid var(--border);
  }
  .preview__title {
    font-size: 11px; font-weight: 600; color: var(--text-secondary);
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .preview__url {
    flex: 1; font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted); overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap;
  }
  .preview__btn {
    background: none; border: 1px solid var(--border); border-radius: 4px;
    color: var(--text-secondary); font-size: 12px; padding: 3px 8px;
    cursor: pointer; flex-shrink: 0;
  }
  .preview__btn:hover { color: var(--text-primary); background: var(--bg-hover); }
  .preview__btn--start {
    color: #3fb950; border-color: rgba(63, 185, 80, 0.4);
    background: rgba(63, 185, 80, 0.08);
  }
  .preview__btn--start:hover { background: rgba(63, 185, 80, 0.2); }
  .preview__btn--start:disabled { opacity: 0.4; cursor: not-allowed; }
  .preview__btn--stop {
    color: #f85149; border-color: rgba(248, 81, 73, 0.4);
  }
  .preview__btn--stop:hover { background: rgba(248, 81, 73, 0.15); }
  .preview__btn--close {
    font-size: 16px; padding: 1px 6px; border: none;
  }

  .preview__config {
    display: flex; gap: 10px; padding: 8px 10px;
    background: var(--bg-tertiary, #21262d);
    border-bottom: 1px solid var(--border);
  }
  .preview__config label {
    display: flex; flex-direction: column; gap: 3px; flex: 1;
    font-size: 11px; color: var(--text-secondary);
  }
  .preview__config input {
    background: var(--bg-primary); border: 1px solid var(--border);
    border-radius: 4px; padding: 4px 8px; font-size: 12px;
    color: var(--text-primary); outline: none;
  }
  .preview__config input:focus { border-color: var(--accent-blue); }
  .preview__config input[type="number"] { width: 80px; flex: 0; }

  .preview__error {
    padding: 6px 10px; background: rgba(248, 81, 73, 0.08);
    border-bottom: 1px solid rgba(248, 81, 73, 0.2);
    color: var(--accent-red); font-size: 12px;
  }

  .preview__body {
    flex: 1; position: relative; overflow: hidden;
  }
  .preview__iframe {
    width: 100%; height: 100%; border: none;
    background: white;
  }
  .preview__status {
    position: absolute; inset: 0; display: flex; flex-direction: column;
    align-items: center; justify-content: center; gap: 8px;
    color: var(--text-muted); font-size: 13px; text-align: center;
  }
  .preview__status-icon { font-size: 36px; margin-bottom: 4px; }
  .preview__status p { margin: 0; }
  .preview__hint { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
  .spinner {
    width: 20px; height: 20px;
    border: 2px solid rgba(255,255,255,0.1);
    border-top-color: var(--accent-blue);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
