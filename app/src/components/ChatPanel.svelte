<script>
  import { onMount } from "svelte";
  import { sendChat, checkHealth } from "../lib/api.js";

  let { workspacePath = "", engineOnline = $bindable(false), oncommand = null } = $props();

  const VISIBLE_LIMIT = 50; // max messages rendered in DOM at once

  let messages = $state([]);          // full history (unbounded)
  let visibleCount = $state(VISIBLE_LIMIT);
  let visibleMessages = $derived(messages.slice(-visibleCount));
  let hasHidden = $derived(messages.length > visibleCount);

  let inputText = $state("");
  let isLoading = $state(false);
  let statusText = $state("Thinking...");
  let lastQuery = $state("");         // for timeout retry
  let chatContainer;
  let abortController = null;

  onMount(() => {
    pingEngine();
    const interval = setInterval(pingEngine, 10000);
    return () => clearInterval(interval);
  });

  async function pingEngine() {
    engineOnline = await checkHealth();
  }

  async function handleSend(overrideQuery) {
    const query = (overrideQuery ?? inputText).trim();
    if (!query || isLoading) return;

    inputText = "";
    lastQuery = query;
    messages.push({ role: "user", content: query });
    isLoading = true;
    statusText = "🚀 Starting...";

    // Push a skeleton assistant message that will be filled in progressively
    const assistantMsg = { role: "assistant", content: "", thoughts: [], thinking: true, stepCount: 0 };
    messages.push(assistantMsg);
    const msgIndex = messages.length - 1;
    // Snap visible window to the latest messages
    visibleCount = Math.max(VISIBLE_LIMIT, messages.length);
    setTimeout(scrollToBottom, 50);

    abortController = new AbortController();

    // Build history from recent messages (exclude skeleton assistant msg just pushed)
    const history = messages
      .slice(0, -1)
      .filter(m => m.role === "user" || m.role === "assistant")
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }));

    try {
      let answer = "";
      await sendChat(
        query,
        workspacePath || ".",
        history,
        (logLine) => {
          // Push log to thoughts array
          messages[msgIndex].thoughts = [...messages[msgIndex].thoughts, logLine];

          // Increment step count
          if (logLine.includes("[Step")) {
            messages[msgIndex].stepCount++;
          }

          // Update concise status
          if (logLine.includes("[Step"))
            statusText = `Step ${messages[msgIndex].stepCount}: ` + logLine.split("] ").pop();
          else if (logLine.includes("Refined to:"))
            statusText = "🔍 " + logLine.trim();
          else if (logLine.includes("[Embeddings]") || logLine.includes("Encoding"))
            statusText = "🧮 Computing embeddings...";
          else if (logLine.includes("[Action Loop]") || logLine.includes("Iteration"))
            statusText = "🧠 Reasoning...";
          else if (logLine.includes("[Fast-Path]"))
            statusText = "⚡ Fast-Path executing...";
          else if (logLine.includes("[Smart-Route]"))
            statusText = "✅ Context ready, generating answer...";

          setTimeout(scrollToBottom, 50);
        },
        (answerContent) => { answer = answerContent; },
        oncommand,
        abortController.signal
      );
      messages[msgIndex].content = answer;
    } catch (error) {
      if (error.name === "AbortError") {
        messages[msgIndex].content = "_Stopped by user._";
      } else if (error.name === "TimeoutError") {
        messages[msgIndex] = {
          role: "error",
          content: `_Request timed out after 5 minutes._`,
          timedOut: true,
        };
      } else {
        messages[msgIndex] = {
          role: "error",
          content: `Failed to reach the engine.\n\nStart it with:\ncd engine && uvicorn server:app --port 8002\n\nError: ${error.message}`,
        };
      }
    }

    messages[msgIndex].thinking = false;
    isLoading = false;
    abortController = null;
    setTimeout(scrollToBottom, 50);
  }

  function handleStop() {
    if (abortController) {
      abortController.abort();
    }
  }

  function scrollToBottom() {
    if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
  }

  function handleKeydown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      handleSend();
    }
  }

  function clearChat() {
    messages = [];
    visibleCount = VISIBLE_LIMIT;
  }

  function loadEarlier() {
    visibleCount = Math.min(visibleCount + VISIBLE_LIMIT, messages.length);
  }
</script>

<div class="chat">
  <div class="chat__header">
    <div class="chat__header-left">
      <span class="chat__icon">🤖</span>
      <span class="chat__title">AI Assistant</span>
    </div>
    <button class="chat__clear" onclick={clearChat} title="Clear Chat">🗑️</button>
  </div>

  <div class="chat__messages" bind:this={chatContainer}>
    {#if messages.length === 0}
      <div class="chat__welcome">
        <div class="chat__welcome-icon">🌊</div>
        <h3>GitSurf AI</h3>
        <p>Ask anything about your codebase. The PRAR engine will search, reason, and answer.</p>
        <div class="chat__suggestions">
          <button class="chat__suggestion" onclick={() => handleSend("What does this project do?")}>
            What does this project do?
          </button>
          <button class="chat__suggestion" onclick={() => handleSend("Explain the main architecture")}>
            Explain the main architecture
          </button>
          <button class="chat__suggestion" onclick={() => handleSend("Find potential bugs")}>
            Find potential bugs
          </button>
        </div>
      </div>
    {/if}

    {#if hasHidden}
      <button class="chat__load-earlier" onclick={loadEarlier}>
        ↑ Load earlier messages ({messages.length - visibleCount} hidden)
      </button>
    {/if}

    {#each visibleMessages as msg}
      <div class="chat__msg chat__msg--{msg.role}">
        <div class="chat__msg-header">
          <span class="chat__msg-avatar">
            {msg.role === "user" ? "👤" : msg.role === "assistant" ? "🌊" : "⚠️"}
          </span>
          <span class="chat__msg-label">
            {msg.role === "user" ? "You" : msg.role === "assistant" ? "GitSurf" : "Error"}
          </span>
        </div>

        <!-- Enhanced Activity Feed & Reasoning -->
        {#if msg.thoughts && msg.thoughts.length > 0}
          <div class="chat__activity" class:chat__activity--thinking={msg.thinking}>
            <div class="chat__activity-header">
              <span class="chat__activity-title">
                {msg.thinking ? "⚙️ Processing..." : "✅ Completed"}
              </span>
              {#if msg.stepCount > 0}
                <span class="chat__activity-steps">
                  {msg.stepCount} {msg.stepCount === 1 ? 'step' : 'steps'}
                </span>
              {/if}
            </div>
            
            <div class="chat__activity-log">
              {#each msg.thoughts.slice(-3) as thought}
                <div class="chat__activity-line">{thought}</div>
              {/each}
              {#if msg.thoughts.length > 3}
                <details class="chat__activity-details">
                  <summary>View full log ({msg.thoughts.length} lines)</summary>
                  <div class="chat__activity-full">
                    {#each msg.thoughts as thought}
                      <div class="chat__activity-line">{thought}</div>
                    {/each}
                  </div>
                </details>
              {/if}
            </div>
          </div>
        {/if}

        <!-- Loading indicator (only while still thinking and no answer yet) -->
        {#if msg.thinking && !msg.content}
          <div class="chat__loading">
            <div class="chat__loading-dots">
              <span></span><span></span><span></span>
            </div>
            <span>{statusText}</span>
          </div>
        {/if}

        <!-- Message body (answer) -->
        {#if msg.content}
          <div class="chat__msg-body">{msg.content}</div>
          {#if msg.timedOut}
            <button class="chat__retry" onclick={() => handleSend(lastQuery)}>↺ Retry</button>
          {/if}
        {/if}
      </div>
    {/each}
  </div>

  <div class="chat__input-area">
    <textarea
      class="chat__input"
      bind:value={inputText}
      onkeydown={handleKeydown}
      placeholder="Ask about your codebase..."
      rows="2"
      disabled={isLoading}
    ></textarea>
    {#if isLoading}
      <button class="chat__stop" onclick={handleStop} title="Stop generation">
        ■
      </button>
    {:else}
      <button class="chat__send" onclick={handleSend} disabled={!inputText.trim()}>
        ➤
      </button>
    {/if}
  </div>
</div>

<style>
  .chat { display: flex; flex-direction: column; height: 100%; }

  .chat__header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 10px 14px; border-bottom: 1px solid var(--border);
  }
  .chat__header-left { display: flex; align-items: center; gap: 8px; }
  .chat__icon { font-size: 16px; }
  .chat__title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
  .chat__clear {
    background: none; font-size: 14px; padding: 2px 6px;
    border-radius: var(--radius-sm); opacity: 0.5;
  }
  .chat__clear:hover { opacity: 1; background: var(--bg-hover); }

  .chat__messages {
    flex: 1; overflow-y: auto; padding: 12px;
    display: flex; flex-direction: column; gap: 16px;
  }

  .chat__welcome {
    display: flex; flex-direction: column; align-items: center;
    text-align: center; padding: 40px 20px; gap: 8px; color: var(--text-secondary);
  }
  .chat__welcome-icon { font-size: 48px; margin-bottom: 8px; }
  .chat__welcome h3 { font-size: 16px; color: var(--text-primary); font-weight: 600; }
  .chat__welcome p { font-size: 13px; line-height: 1.5; }
  .chat__suggestions { display: flex; flex-direction: column; gap: 6px; margin-top: 12px; width: 100%; }
  .chat__suggestion {
    padding: 8px 12px; background: var(--bg-tertiary); color: var(--text-accent);
    border-radius: var(--radius-md); font-size: 12px; text-align: left;
  }
  .chat__suggestion:hover { background: var(--bg-hover); }

  .chat__msg { display: flex; flex-direction: column; gap: 6px; }
  .chat__msg-header { display: flex; align-items: center; gap: 6px; }
  .chat__msg-avatar { font-size: 14px; }
  .chat__msg-label {
    font-size: 11px; font-weight: 600; color: var(--text-secondary);
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .chat__msg-body {
    padding: 10px 12px; border-radius: var(--radius-md);
    font-size: 13px; line-height: 1.6; white-space: pre-wrap; word-break: break-word;
  }
  .chat__msg--user .chat__msg-body {
    background: rgba(88, 166, 255, 0.1); border: 1px solid rgba(88, 166, 255, 0.15);
  }
  .chat__msg--assistant .chat__msg-body {
    background: var(--bg-tertiary); border: 1px solid var(--border);
  }
  .chat__msg--error .chat__msg-body {
    background: rgba(248, 81, 73, 0.08); border: 1px solid rgba(248, 81, 73, 0.2);
    color: var(--accent-red);
  }

  /* ── Activity Feed (Enhanced Reasoning) ── */
  .chat__activity {
    margin: 4px 0 8px;
    background: rgba(48, 54, 61, 0.4);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    overflow: hidden;
    transition: all 0.3s ease;
  }
  .chat__activity--thinking {
    border-color: rgba(56, 139, 253, 0.4);
    background: rgba(56, 139, 253, 0.05);
  }
  .chat__activity-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 10px;
    background: rgba(48, 54, 61, 0.2);
    border-bottom: 1px solid rgba(240, 246, 252, 0.05);
  }
  .chat__activity-title {
    font-size: 11px;
    font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .chat__activity-steps {
    font-size: 10px;
    padding: 2px 6px;
    background: rgba(56, 139, 253, 0.15);
    color: var(--text-accent);
    border-radius: 10px;
  }
  .chat__activity-log {
    padding: 8px 10px;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .chat__activity-line {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--text-secondary);
    white-space: pre-wrap;
    word-break: break-all;
    opacity: 0.8;
  }
  .chat__activity-line:last-child {
    opacity: 1;
    color: var(--text-primary);
  }
  .chat__activity-details {
    margin-top: 6px;
    border-top: 1px solid rgba(240, 246, 252, 0.05);
    padding-top: 6px;
  }
  .chat__activity-details summary {
    font-size: 10px;
    color: var(--text-muted);
    cursor: pointer;
    user-select: none;
    outline: none;
  }
  .chat__activity-details summary:hover { color: var(--text-secondary); }
  .chat__activity-full {
    margin-top: 8px;
    max-height: 150px;
    overflow-y: auto;
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  /* ── Load earlier button ── */
  .chat__load-earlier {
    align-self: center; padding: 5px 14px;
    background: var(--bg-tertiary); border: 1px solid var(--border);
    border-radius: 12px; font-size: 11px; color: var(--text-secondary);
    cursor: pointer; margin-bottom: 4px;
  }
  .chat__load-earlier:hover { background: var(--bg-hover); color: var(--text-primary); }

  /* ── Timeout retry button ── */
  .chat__retry {
    align-self: flex-start; margin-top: 4px; padding: 4px 12px;
    background: rgba(248, 81, 73, 0.1); border: 1px solid rgba(248, 81, 73, 0.3);
    border-radius: var(--radius-md); font-size: 12px; color: var(--accent-red);
    cursor: pointer;
  }
  .chat__retry:hover { background: rgba(248, 81, 73, 0.2); }

  /* ── Loading dots ── */
  .chat__loading {
    display: flex; align-items: center; gap: 8px; padding: 8px 12px;
    color: var(--text-secondary); font-size: 12px;
  }
  .chat__loading-dots { display: flex; gap: 4px; }
  .chat__loading-dots span {
    width: 6px; height: 6px; border-radius: 50%; background: var(--accent-blue);
    animation: bounce 1.2s infinite ease-in-out;
  }
  .chat__loading-dots span:nth-child(2) { animation-delay: 0.2s; }
  .chat__loading-dots span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce {
    0%, 80%, 100% { opacity: 0.3; transform: scale(0.8); }
    40% { opacity: 1; transform: scale(1.2); }
  }

  /* ── Input area ── */
  .chat__input-area {
    display: flex; gap: 8px; padding: 12px;
    border-top: 1px solid var(--border); background: var(--bg-secondary);
  }
  .chat__input {
    flex: 1; resize: none; border: 1px solid var(--border);
    border-radius: var(--radius-md); background: var(--bg-primary);
    color: var(--text-primary); font-family: var(--font-ui); font-size: 13px;
    padding: 8px 12px; outline: none; transition: border-color var(--transition);
  }
  .chat__input:focus { border-color: var(--accent-blue); }
  .chat__input::placeholder { color: var(--text-muted); }
  .chat__send {
    width: 36px; height: 36px; align-self: flex-end;
    border-radius: var(--radius-md); background: var(--accent-blue);
    color: white; font-size: 16px; display: flex; align-items: center; justify-content: center;
  }
  .chat__send:hover:not(:disabled) { background: #79b8ff; }
  .chat__send:disabled { opacity: 0.4; cursor: not-allowed; }

  /* ── Stop button ── */
  .chat__stop {
    width: 36px; height: 36px; align-self: flex-end;
    border-radius: var(--radius-md); background: var(--accent-red, #f85149);
    color: white; font-size: 14px; font-weight: bold;
    display: flex; align-items: center; justify-content: center;
    cursor: pointer; border: none;
    animation: pulse-red 1.5s infinite ease-in-out;
  }
  .chat__stop:hover { background: #da3633; }
  @keyframes pulse-red {
    0%, 100% { box-shadow: 0 0 0 0 rgba(248, 81, 73, 0.4); }
    50% { box-shadow: 0 0 0 6px rgba(248, 81, 73, 0); }
  }
</style>
