<script>
  let {
    inputText = $bindable(""),
    isLoading = false,
    onSend = null,
    onStop = null,
    fileList = [],
  } = $props();

  let textareaEl;
  let atMention = $state(null);
  let mentionIndex = $state(0);
  let mentionResults = $derived(
    atMention
      ? fileList
          .filter(p => p.replace(/\\/g, "/").toLowerCase().includes(atMention.query.toLowerCase()))
          .slice(0, 8)
      : []
  );

  function displayMentionPath(path) {
    const parts = path.replace(/\\/g, "/").split("/");
    return parts.length <= 2 ? path : ".../" + parts.slice(-2).join("/");
  }

  function insertMention(path) {
    if (!atMention || !textareaEl) return;
    const { matchStart } = atMention;
    const cursor = textareaEl.selectionStart ?? inputText.length;
    const after = inputText.slice(cursor);
    inputText = inputText.slice(0, matchStart) + `@${path} ` + after;
    atMention = null;
    setTimeout(() => {
      if (!textareaEl) return;
      textareaEl.focus();
      const newCursor = matchStart + path.length + 2;
      textareaEl.setSelectionRange(newCursor, newCursor);
    }, 0);
  }

  function handleKeydown(event) {
    if (atMention && mentionResults.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        mentionIndex = (mentionIndex + 1) % mentionResults.length;
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        mentionIndex = (mentionIndex - 1 + mentionResults.length) % mentionResults.length;
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        if (mentionResults[mentionIndex]) insertMention(mentionResults[mentionIndex]);
        return;
      }
      if (event.key === "Escape") {
        atMention = null;
        return;
      }
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (onSend) onSend();
    }
  }

  function handleInput(e) {
    const cursor = e.target.selectionStart ?? inputText.length;
    const before = inputText.slice(0, cursor);
    const m = before.match(/@([\w./\\\-]*)$/);
    if (m) {
      atMention = { query: m[1], matchStart: cursor - m[0].length };
      mentionIndex = 0;
    } else {
      atMention = null;
    }
  }

  /** Focus the textarea (callable by parent via bind:this) */
  export function focus() {
    textareaEl?.focus();
  }

  /** Prefill the textarea */
  export function prefill(text) {
    inputText = text;
    setTimeout(() => textareaEl?.focus(), 0);
  }
</script>

<div class="chat__input-area">
  {#if atMention && mentionResults.length}
    <div class="mention-dropdown">
      {#each mentionResults as path, i}
        <button
          class="mention-item"
          class:mention-item--active={i === mentionIndex}
          onmousedown={(e) => {
            e.preventDefault();
            mentionIndex = i;
            insertMention(path);
          }}
        >
          <span class="mention-icon">📄</span>
          <span class="mention-path">{displayMentionPath(path)}</span>
        </button>
      {/each}
    </div>
  {/if}

  <textarea
    class="chat__input"
    bind:value={inputText}
    bind:this={textareaEl}
    onkeydown={handleKeydown}
    oninput={handleInput}
    placeholder="Ask about your codebase... type @ to reference a file"
    rows="1"
    disabled={isLoading}
  ></textarea>
  {#if isLoading}
    <button class="chat__stop" onclick={onStop} title="Stop generation">
      ■
    </button>
  {:else}
    <button class="chat__send" onclick={onSend} disabled={!inputText.trim()}>
      ➤
    </button>
  {/if}
</div>

<style>
  .chat__input-area {
    position: relative;
    display: flex; align-items: flex-end; gap: 8px; padding: 10px 12px;
    border-top: 1px solid var(--border); background: var(--bg-secondary);
  }
  .chat__input {
    flex: 1; resize: none; border: 1px solid var(--border);
    border-radius: var(--radius-md); background: var(--bg-primary);
    color: var(--text-primary); font-family: var(--font-ui); font-size: 13px;
    padding: 8px 12px; outline: none; transition: border-color var(--transition);
    line-height: 1.5; min-height: 36px; max-height: 120px;
  }
  .chat__input:focus { border-color: var(--accent-blue); }
  .chat__input::placeholder { color: var(--text-muted); }
  .chat__send {
    width: 34px; height: 34px; flex-shrink: 0;
    border-radius: var(--radius-md); background: var(--accent-blue);
    color: white; font-size: 15px; display: flex; align-items: center; justify-content: center;
  }
  .chat__send:hover:not(:disabled) { background: #79b8ff; }
  .chat__send:disabled { opacity: 0.4; cursor: not-allowed; }
  .chat__stop {
    width: 34px; height: 34px; flex-shrink: 0;
    border-radius: var(--radius-md); background: var(--accent-red, #f85149);
    color: white; font-size: 14px; font-weight: bold;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; border: none; animation: pulse-red 1.5s infinite ease-in-out;
  }
  .chat__stop:hover { background: #da3633; }
  @keyframes pulse-red {
    0%, 100% { box-shadow: 0 0 0 0 rgba(248, 81, 73, 0.4); }
    50% { box-shadow: 0 0 0 6px rgba(248, 81, 73, 0); }
  }

  .mention-dropdown {
    position: absolute; bottom: calc(100% + 4px); left: 12px; right: 12px;
    background: var(--bg-secondary); border: 1px solid var(--border);
    border-radius: var(--radius-md); overflow: hidden;
    box-shadow: 0 -4px 16px rgba(0,0,0,0.3); z-index: 100;
    max-height: 240px; overflow-y: auto;
  }
  .mention-item {
    display: flex; align-items: center; gap: 8px;
    width: 100%; padding: 7px 12px; text-align: left;
    background: transparent; color: var(--text-secondary);
    font-size: 12px; cursor: pointer;
    border-bottom: 1px solid rgba(255,255,255,0.04); transition: background 0.1s;
  }
  .mention-item:last-child { border-bottom: none; }
  .mention-item:hover, .mention-item--active {
    background: var(--bg-hover); color: var(--text-primary);
  }
  .mention-icon { font-size: 13px; flex-shrink: 0; }
  .mention-path {
    font-family: var(--font-mono); font-size: 11px;
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
</style>
