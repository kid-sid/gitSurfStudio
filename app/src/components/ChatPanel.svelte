<script>
  import { onMount, onDestroy } from "svelte";
  import { sendChat, checkHealth, readFile, getFileTree, getChatSessions, createChatSession, loadSessionMessages, deleteChatSession, agentCancel, agentRespond } from "../lib/api.js";
  import AgentProgress from "./AgentProgress.svelte";
  import ChangeReview from "./ChangeReview.svelte";
  import { supabase } from "../lib/supabase.js";
  import { renderMarkdown } from "../lib/markdown.js";

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
  let textareaEl;                     // bound textarea element
  let abortController = null;

  // ── Agent mode state ──────────────────────────────────────────────────────
  const agentMode = true;
  let agentPlan = $state(null);        // current plan from agent
  let agentChangeset = $state(null);   // changeset from completed agent task
  let agentAskPayload = $state(null);  // {question, options} when agent pauses
  let agentRunning = $state(false);

  // ── Session management ───────────────────────────────────────────────────────
  let sessions = $state([]);          // list of sessions for current repo
  let activeSessionId = $state(null); // current session ID
  let showSessionList = $state(false);

  // ── @mention state ──────────────────────────────────────────────────────────
  let fileList = $state([]);          // flat list of absolute file paths
  let atMention = $state(null);       // { query, matchStart } | null
  let mentionIndex = $state(0);
  let mentionResults = $derived(
    atMention
      ? fileList
          .filter(p => p.replace(/\\/g, "/").toLowerCase().includes(atMention.query.toLowerCase()))
          .slice(0, 8)
      : []
  );

  // Load sessions when workspace changes
  $effect(() => {
    if (workspacePath) loadSessions();
  });

  async function loadSessions() {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user || !workspacePath) return;
    const repoId = makeRepoIdentifier(workspacePath);
    const { sessions: list } = await getChatSessions(user.id, repoId);
    sessions = list ?? [];
    // Auto-activate the most recent session (or create one)
    if (sessions.length > 0) {
      await switchSession(sessions[0].id);
    } else {
      const { session_id } = await createChatSession(user.id, repoId);
      if (session_id) {
        activeSessionId = session_id;
        await loadSessions(); // refresh list
      }
    }
  }

  function makeRepoIdentifier(path) {
    // Mirror the Python logic: "local:<sha8>:<folder>"
    // We use a simple hash of the path for the frontend side
    // (backend uses the same algorithm; this is just for API calls)
    const folder = path.replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? path;
    return `local:${simpleHash(path)}:${folder}`;
  }

  function simpleHash(str) {
    let hash = 0;
    for (let i = 0; i < str.length; i++) {
      hash = ((hash << 5) - hash) + str.charCodeAt(i);
      hash |= 0;
    }
    return Math.abs(hash).toString(16).slice(0, 8);
  }

  async function switchSession(sessionId) {
    activeSessionId = sessionId;
    showSessionList = false;
    const { messages: storedMsgs } = await loadSessionMessages(sessionId);
    messages = (storedMsgs ?? []).map(m => ({
      role: m.role,
      content: m.content,
      thoughts: [],
      thinking: false,
      stepCount: 0,
      isStreaming: false,
    }));
    visibleCount = Math.max(VISIBLE_LIMIT, messages.length);
    setTimeout(scrollToBottom, 50);
  }

  async function handleNewChat() {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user || !workspacePath) {
      clearChat();
      return;
    }
    const repoId = makeRepoIdentifier(workspacePath);
    const { session_id } = await createChatSession(user.id, repoId);
    if (session_id) {
      clearChat();
      activeSessionId = session_id;
      await loadSessions();
    } else {
      clearChat();
    }
  }

  async function handleDeleteSession(sessionId, e) {
    e.stopPropagation();
    await deleteChatSession(sessionId);
    if (activeSessionId === sessionId) {
      await handleNewChat();
    } else {
      await loadSessions();
    }
  }

  onMount(() => {
    pingEngine();
    const interval = setInterval(pingEngine, 10000);
    window.__gsOpenFile = (path) => oncommand && oncommand("open_file", path);

    const handleChatPrefill = (e) => {
      const { query, autoSend } = e.detail;
      if (autoSend) {
        handleSend(query);
      } else {
        inputText = query;
        setTimeout(() => textareaEl?.focus(), 0);
      }
    };
    window.addEventListener("chat-prefill", handleChatPrefill);

    return () => {
      clearInterval(interval);
      window.removeEventListener("chat-prefill", handleChatPrefill);
    };
  });

  onDestroy(() => {
    delete window.__gsOpenFile;
  });

  // ── File list for @mentions ─────────────────────────────────────────────────
  function flattenTree(node) {
    if (!node) return [];
    if (node.type === "file") return [node.path];
    return (node.children || []).flatMap(flattenTree);
  }

  async function ensureFileList() {
    if (fileList.length || !workspacePath) return;
    try {
      const tree = await getFileTree(workspacePath);
      fileList = flattenTree(tree);
    } catch {}
  }

  function displayMentionPath(path) {
    const parts = path.replace(/\\/g, "/").split("/");
    return parts.length <= 2 ? path : "…/" + parts.slice(-2).join("/");
  }

  function insertMention(path) {
    if (!atMention || !textareaEl) return;
    const { matchStart } = atMention;
    const cursor = textareaEl.selectionStart ?? inputText.length;
    const after = inputText.slice(cursor);
    inputText = inputText.slice(0, matchStart) + `@${path} ` + after;
    atMention = null;
    // Restore focus and move cursor after the inserted mention
    setTimeout(() => {
      if (!textareaEl) return;
      textareaEl.focus();
      const newCursor = matchStart + path.length + 2; // "@" + path + " "
      textareaEl.setSelectionRange(newCursor, newCursor);
    }, 0);
  }

  // ── Resolve @mentions before sending ───────────────────────────────────────
  async function resolveAtMentions(query) {
    const mentionRe = /@([\w./\\:\-]+)/g;
    const mentions = [...query.matchAll(mentionRe)].map(m => m[1]);
    if (!mentions.length) return query;

    const blocks = [];
    for (const mention of mentions) {
      try {
        const res = await readFile(mention);
        const ext = mention.split(".").pop() ?? "";
        blocks.push(
          `**Context from \`${mention}\`:**\n\`\`\`${ext}\n${res.content.slice(0, 4000)}\n\`\`\``
        );
      } catch {}
    }

    if (!blocks.length) return query;
    return `${blocks.join("\n\n")}\n\n---\n\n${query}`;
  }

  async function pingEngine() {
    engineOnline = await checkHealth();
  }

  async function handleSend(overrideQuery) {
    const rawQuery = (overrideQuery ?? inputText).trim();
    if (!rawQuery || isLoading) return;

    inputText = "";
    atMention = null;
    lastQuery = rawQuery;
    messages.push({ role: "user", content: rawQuery });
    isLoading = true;
    statusText = agentMode ? "🤖 Agent planning..." : "🚀 Starting...";
    agentPlan = null;
    agentChangeset = null;
    agentAskPayload = null;
    agentRunning = agentMode;

    // Resolve @mentions — inject file content into query sent to AI
    const resolvedQuery = await resolveAtMentions(rawQuery);

    // Push a skeleton assistant message that will be filled in progressively
    const assistantMsg = { role: "assistant", content: "", thoughts: [], thinking: true, stepCount: 0, isStreaming: false };
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
      const { data: { user } } = await supabase.auth.getUser();
      // Wrap oncommand to intercept agent-specific UI commands
      const handleCommand = (cmd, args) => {
        if (cmd === 'agent_plan') {
          try { agentPlan = JSON.parse(args); } catch {}
          statusText = "🤖 Executing plan...";
          return;
        }
        if (cmd === 'agent_step') {
          try {
            const stepData = JSON.parse(args);
            if (agentPlan?.steps) {
              const step = agentPlan.steps.find(s => s.id === stepData.step_id);
              if (step) {
                step.status = stepData.status;
                if (stepData.error) step.error = stepData.error;
                agentPlan = { ...agentPlan }; // trigger reactivity
              }
            }
            if (stepData.status === 'running') {
              statusText = `🤖 Step ${stepData.step_id}: ${stepData.description || 'Executing...'}`;
            }
          } catch {}
          return;
        }
        if (cmd === 'agent_changeset') {
          try { agentChangeset = JSON.parse(args); } catch {}
          return;
        }
        if (cmd === 'agent_ask') {
          try { agentAskPayload = JSON.parse(args); } catch {}
          return;
        }
        if (cmd === 'agent_terminal_output') {
          messages[msgIndex].thoughts = [...messages[msgIndex].thoughts, `$ ${args}`];
          return;
        }
        // Pass through to parent handler for file_changed, open_file, etc.
        if (oncommand) oncommand(cmd, args);
      };

      await sendChat(
        resolvedQuery,
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
          else if (logLine.includes("[Agent Pipeline]") || logLine.includes("[Agent"))
            statusText = "🤖 " + logLine.split("] ").pop();
          else if (logLine.includes("[Fast-Path]"))
            statusText = "⚡ Fast-Path executing...";
          else if (logLine.includes("[Smart-Route]"))
            statusText = "✅ Context ready, generating answer...";

          setTimeout(scrollToBottom, 50);
        },
        (answerContent) => {
          // Update message content progressively as tokens stream in
          answer = answerContent;
          messages[msgIndex].content = answerContent;
          messages[msgIndex].isStreaming = true;
          setTimeout(scrollToBottom, 20);
        },
        handleCommand,
        abortController.signal,
        user?.id ?? null,
        agentMode
      );
      messages[msgIndex].content = answer;
      messages[msgIndex].isStreaming = false;
    } catch (error) {
      if (error.name === "AbortError") {
        messages[msgIndex].content = "_Stopped by user._";
      } else if (error.name === "TimeoutError") {
        messages[msgIndex] = {
          role: "error",
          content: `_Request timed out after 5 minutes._`,
          timedOut: true,
        };
      } else if (error.message && !error.message.startsWith("Failed to reach")) {
        // Policy / validation refusal — show as an assistant message, not an error
        messages[msgIndex] = {
          role: "assistant",
          content: error.message,
          thoughts: [],
          thinking: false,
          stepCount: 0,
        };
      } else {
        messages[msgIndex] = {
          role: "error",
          content: `Failed to reach the engine.\n\nStart it with:\ncd engine && uvicorn server:app --port 8002\n\nError: ${error.message}`,
        };
      }
    }

    // Guard: messages[] may have been cleared while this request was in-flight
    if (messages[msgIndex]) {
      messages[msgIndex].thinking = false;
      messages[msgIndex].isStreaming = false;
    }
    isLoading = false;
    agentRunning = false;
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
    // @mention dropdown navigation
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
      handleSend();
    }
  }

  function handleInput(e) {
    const cursor = e.target.selectionStart ?? inputText.length;
    const before = inputText.slice(0, cursor);
    const m = before.match(/@([\w./\\\-]*)$/);
    if (m) {
      atMention = { query: m[1], matchStart: cursor - m[0].length };
      mentionIndex = 0;
      ensureFileList();
    } else {
      atMention = null;
    }
  }

  function clearChat() {
    if (abortController) abortController.abort();
    messages = [];
    visibleCount = VISIBLE_LIMIT;
    isLoading = false;
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
      {#if sessions.length > 0}
        <button
          class="chat__session-toggle"
          onclick={() => { showSessionList = !showSessionList; }}
          title="Switch session"
        >
          {sessions.find(s => s.id === activeSessionId)?.title ?? "Chat"} ▾
        </button>
      {/if}
    </div>
    <div class="chat__header-right">
      <button class="chat__new" onclick={handleNewChat} title="New Chat">＋</button>
      <button class="chat__clear" onclick={clearChat} title="Clear view (doesn't delete history)">🗑️</button>
    </div>
  </div>

  {#if showSessionList && sessions.length > 0}
    <div class="chat__session-list">
      {#each sessions as s}
        <div
          class="chat__session-item"
          class:chat__session-item--active={s.id === activeSessionId}
          role="button"
          tabindex="0"
          onclick={() => switchSession(s.id)}
          onkeydown={(e) => e.key === 'Enter' && switchSession(s.id)}
        >
          <span class="chat__session-title">{s.title ?? "Untitled"}</span>
          <button
            class="chat__session-delete"
            onclick={(e) => handleDeleteSession(s.id, e)}
            title="Delete session"
          >✕</button>
        </div>
      {/each}
    </div>
  {/if}

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

        <!-- Agent Progress (shown in agent mode during execution) -->
        {#if msg.role === "assistant" && agentPlan}
          <AgentProgress plan={agentPlan} isRunning={agentRunning} onCancel={() => { agentRunning = false; }} />
        {/if}

        <!-- Agent Ask (human-in-the-loop) -->
        {#if msg.role === "assistant" && agentAskPayload && msg.thinking}
          <div class="chat__agent-ask">
            <div class="chat__agent-ask-question">{agentAskPayload.question}</div>
            {#if agentAskPayload.options}
              <div class="chat__agent-ask-options">
                {#each agentAskPayload.options as option}
                  <button class="chat__agent-ask-btn" onclick={async () => {
                    await agentRespond(option);
                    agentAskPayload = null;
                  }}>{option}</button>
                {/each}
              </div>
            {:else}
              <div class="chat__agent-ask-input">
                <input type="text" placeholder="Type your response..."
                  onkeydown={async (e) => {
                    if (e.key === 'Enter') {
                      await agentRespond(e.target.value);
                      agentAskPayload = null;
                    }
                  }} />
              </div>
            {/if}
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
          {#if msg.role === "assistant"}
            <div class="chat__msg-body chat__msg-body--markdown">
              {#if msg.isStreaming}
                <!-- Raw text during streaming for performance; markdown rendered after done -->
                <span class="chat__streaming-text">{msg.content}<span class="chat__cursor"></span></span>
              {:else}
                {@html renderMarkdown(msg.content)}
              {/if}
            </div>
          {:else}
            <div class="chat__msg-body">{msg.content}</div>
          {/if}
          {#if msg.timedOut}
            <button class="chat__retry" onclick={() => handleSend(lastQuery)}>↺ Retry</button>
          {/if}
        {/if}
      </div>
    {/each}
  </div>

  <!-- Agent Change Review (shown after agent task completes with changes) -->
  {#if agentChangeset}
    <ChangeReview changeset={agentChangeset}
      onAccepted={() => { agentChangeset = null; }}
      onRolledBack={() => { agentChangeset = null; }}
    />
  {/if}

  <div class="chat__input-area">
    <!-- @mention dropdown -->
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
      placeholder="Ask about your codebase… type @ to reference a file"
      rows="1"
      disabled={isLoading}
    ></textarea>
    {#if isLoading}
      <button class="chat__stop" onclick={handleStop} title="Stop generation">
        ■
      </button>
    {:else}
      <button class="chat__send" onclick={() => handleSend()} disabled={!inputText.trim()}>
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
  .chat__header-left { display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0; }
  .chat__header-right { display: flex; align-items: center; gap: 4px; flex-shrink: 0; }
  .chat__icon { font-size: 16px; }
  .chat__title { font-size: 12px; font-weight: 600; color: var(--text-primary); }
  .chat__session-toggle {
    background: none; border: 1px solid var(--border); border-radius: var(--radius-sm);
    color: var(--text-secondary); font-size: 10px; padding: 2px 6px;
    max-width: 120px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
    cursor: pointer;
  }
  .chat__session-toggle:hover { background: var(--bg-hover); color: var(--text-primary); }
  .chat__new {
    background: none; font-size: 16px; font-weight: 400; padding: 2px 6px;
    border-radius: var(--radius-sm); color: var(--text-accent); opacity: 0.7;
  }
  .chat__new:hover { opacity: 1; background: var(--bg-hover); }
  .chat__clear {
    background: none; font-size: 14px; padding: 2px 6px;
    border-radius: var(--radius-sm); opacity: 0.5;
  }
  .chat__clear:hover { opacity: 1; background: var(--bg-hover); }

  .chat__agent-ask {
    background: var(--bg-tertiary, #313244);
    border: 1px solid var(--accent, #89b4fa);
    border-radius: 6px; padding: 10px 12px;
    margin: 6px 0;
  }
  .chat__agent-ask-question {
    font-size: 12px; color: var(--text-primary);
    margin-bottom: 8px;
  }
  .chat__agent-ask-options {
    display: flex; gap: 6px; flex-wrap: wrap;
  }
  .chat__agent-ask-btn {
    font-size: 11px; padding: 4px 12px;
    border-radius: 4px;
    border: 1px solid var(--accent, #89b4fa);
    background: transparent;
    color: var(--accent, #89b4fa);
    cursor: pointer;
  }
  .chat__agent-ask-btn:hover {
    background: var(--accent, #89b4fa);
    color: var(--bg-primary, #1e1e2e);
  }
  .chat__agent-ask-input input {
    width: 100%; padding: 6px 10px;
    font-size: 12px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text-primary);
    outline: none;
  }
  .chat__agent-ask-input input:focus {
    border-color: var(--accent, #89b4fa);
  }

  .chat__session-list {
    border-bottom: 1px solid var(--border); background: var(--bg-secondary);
    max-height: 200px; overflow-y: auto;
  }
  .chat__session-item {
    display: flex; align-items: center; width: 100%; padding: 7px 14px;
    background: none; border-bottom: 1px solid var(--border-subtle);
    font-size: 12px; color: var(--text-secondary); cursor: pointer; gap: 6px;
    text-align: left;
  }
  .chat__session-item:hover { background: var(--bg-hover); color: var(--text-primary); }
  .chat__session-item--active { color: var(--text-accent); background: rgba(88,166,255,0.06); }
  .chat__session-title { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chat__session-delete {
    background: none; font-size: 10px; padding: 1px 4px; border-radius: 3px;
    color: var(--text-secondary); opacity: 0; flex-shrink: 0;
  }
  .chat__session-item:hover .chat__session-delete { opacity: 0.6; }
  .chat__session-delete:hover { opacity: 1 !important; color: var(--accent-red); }

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

  /* ── Streaming text ── */
  .chat__streaming-text {
    white-space: pre-wrap; word-break: break-word;
    font-size: 13px; line-height: 1.6;
  }
  .chat__cursor {
    display: inline-block; width: 2px; height: 1em;
    background: var(--accent-blue); margin-left: 1px;
    vertical-align: text-bottom;
    animation: blink 0.8s step-end infinite;
  }
  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }

  /* ── @mention dropdown ── */
  .mention-dropdown {
    position: absolute; bottom: calc(100% + 4px); left: 12px; right: 12px;
    background: var(--bg-secondary); border: 1px solid var(--border);
    border-radius: var(--radius-md); overflow: hidden;
    box-shadow: 0 -4px 16px rgba(0,0,0,0.3);
    z-index: 100;
    max-height: 240px; overflow-y: auto;
  }
  .mention-item {
    display: flex; align-items: center; gap: 8px;
    width: 100%; padding: 7px 12px; text-align: left;
    background: transparent; color: var(--text-secondary);
    font-size: 12px; cursor: pointer;
    border-bottom: 1px solid rgba(255,255,255,0.04);
    transition: background 0.1s;
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

  /* ── Input area ── */
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

  /* ── Stop button ── */
  .chat__stop {
    width: 34px; height: 34px; flex-shrink: 0;
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

  /* ── Markdown rendered output ── */
  .chat__msg-body--markdown {
    white-space: normal; /* override pre-wrap — marked handles line breaks */
  }

  /* Prose elements */
  :global(.chat__msg-body--markdown p) {
    margin: 0 0 8px; line-height: 1.65;
  }
  :global(.chat__msg-body--markdown p:last-child) { margin-bottom: 0; }
  :global(.chat__msg-body--markdown h1),
  :global(.chat__msg-body--markdown h2),
  :global(.chat__msg-body--markdown h3),
  :global(.chat__msg-body--markdown h4) {
    font-weight: 600; color: var(--text-primary);
    margin: 14px 0 6px; line-height: 1.3;
  }
  :global(.chat__msg-body--markdown h1) { font-size: 16px; }
  :global(.chat__msg-body--markdown h2) { font-size: 14px; }
  :global(.chat__msg-body--markdown h3) { font-size: 13px; }
  :global(.chat__msg-body--markdown h4) { font-size: 12px; }
  :global(.chat__msg-body--markdown ul),
  :global(.chat__msg-body--markdown ol) {
    margin: 6px 0 8px; padding-left: 20px;
  }
  :global(.chat__msg-body--markdown li) { margin: 3px 0; line-height: 1.55; }
  :global(.chat__msg-body--markdown a) {
    color: var(--accent-blue); text-decoration: underline; text-underline-offset: 2px;
  }
  :global(.chat__msg-body--markdown a:hover) { color: #79b8ff; }
  :global(.chat__msg-body--markdown strong) { font-weight: 600; color: var(--text-primary); }
  :global(.chat__msg-body--markdown em) { font-style: italic; color: var(--text-secondary); }
  :global(.chat__msg-body--markdown hr) {
    border: none; border-top: 1px solid var(--border); margin: 12px 0;
  }
  :global(.chat__msg-body--markdown table) {
    width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0;
  }
  :global(.chat__msg-body--markdown th),
  :global(.chat__msg-body--markdown td) {
    padding: 5px 10px; border: 1px solid var(--border); text-align: left;
  }
  :global(.chat__msg-body--markdown th) {
    background: var(--bg-tertiary); font-weight: 600; color: var(--text-primary);
  }
  :global(.chat__msg-body--markdown tr:nth-child(even) td) {
    background: rgba(48, 54, 61, 0.3);
  }

  /* Blockquote */
  :global(.md-blockquote) {
    margin: 8px 0; padding: 6px 12px;
    border-left: 3px solid var(--accent-blue);
    background: rgba(56, 139, 253, 0.06);
    color: var(--text-secondary); font-style: italic;
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  }

  /* Clickable file paths */
  :global(.md-file-link) {
    display: inline;
    font-family: var(--font-mono); font-size: 12px;
    padding: 1px 5px; border-radius: var(--radius-sm);
    background: rgba(88, 166, 255, 0.12);
    color: #58a6ff;
    border: 1px solid rgba(88, 166, 255, 0.3);
    cursor: pointer; text-decoration: none;
    transition: background 0.15s, border-color 0.15s;
    vertical-align: baseline; line-height: inherit;
  }
  :global(.md-file-link:hover) {
    background: rgba(88, 166, 255, 0.22);
    border-color: rgba(88, 166, 255, 0.55);
    text-decoration: underline;
  }

  /* Inline code */
  :global(.md-inline-code) {
    font-family: var(--font-mono); font-size: 12px;
    padding: 1px 5px; border-radius: var(--radius-sm);
    background: rgba(110, 118, 129, 0.2);
    color: #e3b341; /* amber — stands out against dark bg */
    border: 1px solid rgba(110, 118, 129, 0.3);
  }

  /* Fenced code blocks */
  :global(.md-code-block) {
    margin: 10px 0; border-radius: var(--radius-md);
    border: 1px solid var(--border); overflow: hidden;
    background: #0d1117; /* GitHub dark */
  }
  :global(.md-code-header) {
    display: flex; justify-content: space-between; align-items: center;
    padding: 5px 12px;
    background: rgba(48, 54, 61, 0.8);
    border-bottom: 1px solid var(--border);
  }
  :global(.md-code-lang) {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted); text-transform: lowercase;
  }
  :global(.md-copy-btn) {
    font-size: 11px; padding: 2px 8px;
    background: rgba(110, 118, 129, 0.2); border: 1px solid var(--border);
    border-radius: var(--radius-sm); color: var(--text-secondary);
    cursor: pointer; transition: all 0.15s;
  }
  :global(.md-copy-btn:hover) {
    background: rgba(110, 118, 129, 0.4); color: var(--text-primary);
  }
  :global(.md-code-block pre) {
    margin: 0; padding: 12px 14px; overflow-x: auto;
    font-family: var(--font-mono); font-size: 12px; line-height: 1.6;
  }
  :global(.md-code-block code) {
    font-family: inherit; font-size: inherit; background: none; border: none; padding: 0;
  }

  /* highlight.js — GitHub Dark theme tokens */
  :global(.hljs) { color: #c9d1d9; background: none; }
  :global(.hljs-keyword, .hljs-selector-tag, .hljs-literal, .hljs-section, .hljs-link) { color: #ff7b72; }
  :global(.hljs-string, .hljs-attr, .hljs-symbol, .hljs-bullet, .hljs-addition) { color: #a5d6ff; }
  :global(.hljs-title, .hljs-name, .hljs-type, .hljs-selector-id, .hljs-selector-class, .hljs-built_in) { color: #d2a8ff; }
  :global(.hljs-comment, .hljs-quote, .hljs-deletion) { color: #8b949e; font-style: italic; }
  :global(.hljs-number, .hljs-regexp, .hljs-params) { color: #79c0ff; }
  :global(.hljs-meta) { color: #e3b341; }
  :global(.hljs-emphasis) { font-style: italic; }
  :global(.hljs-strong) { font-weight: bold; }
  :global(.hljs-variable, .hljs-template-variable) { color: #ffa657; }
  :global(.hljs-subst, .hljs-selector-pseudo, .hljs-selector-attr, .hljs-attribute) { color: #c9d1d9; }
  :global(.hljs-doctag) { color: #ff7b72; }
  :global(.hljs-property) { color: #79c0ff; }
  :global(.hljs-operator) { color: #ff7b72; }
  :global(.hljs-punctuation) { color: #c9d1d9; }
</style>
