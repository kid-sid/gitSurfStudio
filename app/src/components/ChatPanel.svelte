<script>
  import { onMount, onDestroy } from "svelte";
  import { sendChat, checkHealth, readFile, getFileTree, getChatSessions, createChatSession, loadSessionMessages, deleteChatSession } from "../lib/api.js";
  import ChangeReview from "./ChangeReview.svelte";
  import ChatMessage from "./ChatMessage.svelte";
  import ChatInput from "./ChatInput.svelte";
  import SessionList from "./SessionList.svelte";
  import { supabase } from "../lib/supabase.js";

  let { workspacePath = "", engineOnline = $bindable(false), oncommand = null } = $props();

  const VISIBLE_LIMIT = 50;

  let messages = $state([]);
  let visibleCount = $state(VISIBLE_LIMIT);
  let visibleMessages = $derived(messages.slice(-visibleCount));
  let hasHidden = $derived(messages.length > visibleCount);

  let inputText = $state("");
  let isLoading = $state(false);
  let statusText = $state("Thinking...");
  let lastQuery = $state("");
  let chatContainer;
  let chatInputEl;
  let abortController = null;

  // ── Agent state (always agentic — no toggle) ────────────────────────────
  let agentPlan = $state(null);
  let agentChangeset = $state(null);
  let agentAskPayload = $state(null);
  let agentRunning = $state(false);

  // ── Session management ───────────────────────────────────────────────────────
  let sessions = $state([]);
  let activeSessionId = $state(null);
  let showSessionList = $state(false);

  // ── File list for @mentions ──────────────────────────────────────────────────
  let fileList = $state([]);

  $effect(() => {
    if (workspacePath) loadSessions();
  });

  async function loadSessions() {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user || !workspacePath) return;
    const repoId = makeRepoIdentifier(workspacePath);
    const { sessions: list } = await getChatSessions(user.id, repoId);
    sessions = list ?? [];
    if (sessions.length > 0) {
      await switchSession(sessions[0].id);
    } else {
      const { session_id } = await createChatSession(user.id, repoId);
      if (session_id) {
        activeSessionId = session_id;
        await loadSessions();
      }
    }
  }

  function makeRepoIdentifier(path) {
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
      role: m.role, content: m.content,
      thoughts: [], thinking: false, stepCount: 0, isStreaming: false,
    }));
    visibleCount = Math.max(VISIBLE_LIMIT, messages.length);
    setTimeout(scrollToBottom, 50);
  }

  async function handleNewChat() {
    const { data: { user } } = await supabase.auth.getUser();
    if (!user || !workspacePath) { clearChat(); return; }
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

  async function handleDeleteSession(sessionId) {
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
        chatInputEl?.focus();
      }
    };
    window.addEventListener("chat-prefill", handleChatPrefill);

    return () => {
      clearInterval(interval);
      window.removeEventListener("chat-prefill", handleChatPrefill);
    };
  });

  onDestroy(() => { delete window.__gsOpenFile; });

  function flattenTree(node) {
    if (!node) return [];
    if (node.type === "file") return [node.path];
    return (node.children || []).flatMap(flattenTree);
  }

  $effect(() => {
    if (workspacePath && !fileList.length) {
      getFileTree(workspacePath).then(tree => {
        fileList = flattenTree(tree);
      }).catch(() => {});
    }
  });

  async function resolveAtMentions(query) {
    const mentionRe = /@([\w./\\:\-]+)/g;
    const mentions = [...query.matchAll(mentionRe)].map(m => m[1]);
    if (!mentions.length) return query;

    const blocks = [];
    for (const mention of mentions) {
      try {
        const res = await readFile(mention);
        const ext = mention.split(".").pop() ?? "";
        blocks.push(`**Context from \`${mention}\`:**\n\`\`\`${ext}\n${res.content.slice(0, 4000)}\n\`\`\``);
      } catch {}
    }
    if (!blocks.length) return query;
    return `${blocks.join("\n\n")}\n\n---\n\n${query}`;
  }

  async function pingEngine() { engineOnline = await checkHealth(); }

  async function handleSend(overrideQuery) {
    const rawQuery = (overrideQuery ?? inputText).trim();
    if (!rawQuery || isLoading) return;

    inputText = "";
    lastQuery = rawQuery;
    messages.push({ role: "user", content: rawQuery });
    isLoading = true;
    statusText = "Starting...";
    agentPlan = null;
    agentChangeset = null;
    agentAskPayload = null;
    agentRunning = false;

    const resolvedQuery = await resolveAtMentions(rawQuery);

    const assistantMsg = { role: "assistant", content: "", thoughts: [], thinking: true, stepCount: 0, isStreaming: false, toolActions: [] };
    messages.push(assistantMsg);
    const msgIndex = messages.length - 1;
    visibleCount = Math.max(VISIBLE_LIMIT, messages.length);
    setTimeout(scrollToBottom, 50);

    abortController = new AbortController();

    const history = messages
      .slice(0, -1)
      .filter(m => m.role === "user" || m.role === "assistant")
      .slice(-10)
      .map(m => ({ role: m.role, content: m.content }));

    try {
      let answer = "";
      const { data: { user } } = await supabase.auth.getUser();

      const handleCommand = (cmd, args) => {
        if (cmd === 'agent_plan') {
          try { agentPlan = JSON.parse(args); } catch {}
          statusText = "Executing plan...";
          return;
        }
        if (cmd === 'agent_step') {
          try {
            const stepData = JSON.parse(args);
            if (!agentPlan) agentPlan = { goal: lastQuery, steps: [] };
            let step = agentPlan.steps.find(s => s.id === stepData.step_id);
            if (step) {
              step.status = stepData.status;
              if (stepData.error) step.error = stepData.error;
            } else {
              agentPlan.steps.push({
                id: stepData.step_id,
                description: stepData.description || `Step ${stepData.step_id}`,
                status: stepData.status, error: stepData.error
              });
            }
            agentPlan = { ...agentPlan };
            if (stepData.status === 'running') statusText = stepData.description || 'Executing...';
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
        if (cmd === 'agent_action') {
          try {
            const actionData = JSON.parse(args);
            const actions = messages[msgIndex].toolActions ?? [];
            if (actionData.status === 'running') {
              actions.push(actionData);
            } else {
              // Update existing running action for this iteration
              const idx = actions.findIndex(a => a.iteration === actionData.iteration && a.status === 'running');
              if (idx !== -1) {
                actions[idx] = { ...actions[idx], ...actionData };
              } else {
                actions.push(actionData);
              }
            }
            messages[msgIndex].toolActions = [...actions];
            if (actionData.thought) statusText = actionData.thought;
          } catch {}
          return;
        }
        if (cmd === 'agent_terminal_output') {
          messages[msgIndex].thoughts = [...messages[msgIndex].thoughts, `$ ${args}`];
          return;
        }
        if (oncommand) oncommand(cmd, args);
      };

      await sendChat(
        resolvedQuery, workspacePath || ".", history,
        (logLine) => {
          messages[msgIndex].thoughts = [...messages[msgIndex].thoughts, logLine];
          if (logLine.includes("[Step")) messages[msgIndex].stepCount++;
          if (logLine.includes("[Step"))
            statusText = `Step ${messages[msgIndex].stepCount}: ` + logLine.split("] ").pop();
          else if (logLine.includes("Refined to:"))
            statusText = logLine.trim();
          else if (logLine.includes("[Embeddings]") || logLine.includes("Encoding"))
            statusText = "Computing embeddings...";
          else if (logLine.includes("[Action Loop]") || logLine.includes("Iteration"))
            statusText = "Reasoning...";
          else if (logLine.includes("[Agent Pipeline]") || logLine.includes("[Agent"))
            statusText = logLine.split("] ").pop();
          else if (logLine.includes("[Fast-Path]"))
            statusText = "Fast-Path executing...";
          else if (logLine.includes("[Smart-Route]"))
            statusText = "Context ready, generating answer...";
          setTimeout(scrollToBottom, 50);
        },
        (answerContent) => {
          answer = answerContent;
          messages[msgIndex].content = answerContent;
          messages[msgIndex].isStreaming = true;
          setTimeout(scrollToBottom, 20);
        },
        handleCommand,
        abortController.signal,
        user?.id ?? null
      );
      messages[msgIndex].content = answer;
      messages[msgIndex].isStreaming = false;
    } catch (error) {
      if (error.name === "AbortError") {
        messages[msgIndex].content = "_Stopped by user._";
      } else if (error.name === "TimeoutError") {
        messages[msgIndex] = { role: "error", content: `_Request timed out after 5 minutes._`, timedOut: true };
      } else if (error.message && !error.message.startsWith("Failed to reach")) {
        messages[msgIndex] = { role: "assistant", content: error.message, thoughts: [], thinking: false, stepCount: 0 };
      } else {
        messages[msgIndex] = {
          role: "error",
          content: `Failed to reach the engine.\n\nStart it with:\ncd engine && uvicorn server:app --port 8002\n\nError: ${error.message}`,
        };
      }
    }

    if (messages[msgIndex]) {
      messages[msgIndex].thinking = false;
      messages[msgIndex].isStreaming = false;
    }
    isLoading = false;
    agentRunning = false;
    abortController = null;
    setTimeout(scrollToBottom, 50);
  }

  function handleStop() { abortController?.abort(); }

  function scrollToBottom() {
    if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
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
      <button class="chat__new" onclick={handleNewChat} title="New Chat">+</button>
      <button class="chat__clear" onclick={clearChat} title="Clear view">🗑️</button>
    </div>
  </div>

  {#if showSessionList}
    <SessionList
      {sessions}
      {activeSessionId}
      onswitch={switchSession}
      ondelete={handleDeleteSession}
    />
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
        Load earlier messages ({messages.length - visibleCount} hidden)
      </button>
    {/if}

    {#each visibleMessages as msg}
      <ChatMessage
        {msg}
        {agentPlan}
        {agentRunning}
        bind:agentAskPayload={agentAskPayload}
        {statusText}
        {lastQuery}
        onretry={handleSend}
        {oncommand}
      />
    {/each}
  </div>

  {#if agentChangeset}
    <ChangeReview changeset={agentChangeset}
      onAccepted={() => { agentChangeset = null; }}
      onRolledBack={() => { agentChangeset = null; }}
    />
  {/if}

  <ChatInput
    bind:inputText={inputText}
    {isLoading}
    onSend={() => handleSend()}
    onStop={handleStop}
    {fileList}
    bind:this={chatInputEl}
  />
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

  .chat__load-earlier {
    align-self: center; padding: 5px 14px;
    background: var(--bg-tertiary); border: 1px solid var(--border);
    border-radius: 12px; font-size: 11px; color: var(--text-secondary);
    cursor: pointer; margin-bottom: 4px;
  }
  .chat__load-earlier:hover { background: var(--bg-hover); color: var(--text-primary); }

  /* Markdown global styles remain here since they target :global selectors */
  :global(.chat__msg-body--markdown p) { margin: 0 0 8px; line-height: 1.65; }
  :global(.chat__msg-body--markdown p:last-child) { margin-bottom: 0; }
  :global(.chat__msg-body--markdown h1),
  :global(.chat__msg-body--markdown h2),
  :global(.chat__msg-body--markdown h3),
  :global(.chat__msg-body--markdown h4) {
    font-weight: 600; color: var(--text-primary); margin: 14px 0 6px; line-height: 1.3;
  }
  :global(.chat__msg-body--markdown h1) { font-size: 16px; }
  :global(.chat__msg-body--markdown h2) { font-size: 14px; }
  :global(.chat__msg-body--markdown h3) { font-size: 13px; }
  :global(.chat__msg-body--markdown h4) { font-size: 12px; }
  :global(.chat__msg-body--markdown ul),
  :global(.chat__msg-body--markdown ol) { margin: 6px 0 8px; padding-left: 20px; }
  :global(.chat__msg-body--markdown li) { margin: 3px 0; line-height: 1.55; }
  :global(.chat__msg-body--markdown a) {
    color: var(--accent-blue); text-decoration: underline; text-underline-offset: 2px;
  }
  :global(.chat__msg-body--markdown a:hover) { color: #79b8ff; }
  :global(.chat__msg-body--markdown strong) { font-weight: 600; color: var(--text-primary); }
  :global(.chat__msg-body--markdown em) { font-style: italic; color: var(--text-secondary); }
  :global(.chat__msg-body--markdown hr) { border: none; border-top: 1px solid var(--border); margin: 12px 0; }
  :global(.chat__msg-body--markdown table) { width: 100%; border-collapse: collapse; font-size: 12px; margin: 8px 0; }
  :global(.chat__msg-body--markdown th),
  :global(.chat__msg-body--markdown td) { padding: 5px 10px; border: 1px solid var(--border); text-align: left; }
  :global(.chat__msg-body--markdown th) {
    background: var(--bg-tertiary); font-weight: 600; color: var(--text-primary);
  }
  :global(.chat__msg-body--markdown tr:nth-child(even) td) { background: rgba(48, 54, 61, 0.3); }

  :global(.md-blockquote) {
    margin: 8px 0; padding: 6px 12px; border-left: 3px solid var(--accent-blue);
    background: rgba(56, 139, 253, 0.06); color: var(--text-secondary);
    font-style: italic; border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
  }
  :global(.md-file-link) {
    display: inline; font-family: var(--font-mono); font-size: 12px;
    padding: 1px 5px; border-radius: var(--radius-sm);
    background: rgba(88, 166, 255, 0.12); color: #58a6ff;
    border: 1px solid rgba(88, 166, 255, 0.3);
    cursor: pointer; text-decoration: none; transition: background 0.15s, border-color 0.15s;
    vertical-align: baseline; line-height: inherit;
  }
  :global(.md-file-link:hover) {
    background: rgba(88, 166, 255, 0.22); border-color: rgba(88, 166, 255, 0.55);
    text-decoration: underline;
  }
  :global(.md-inline-code) {
    font-family: var(--font-mono); font-size: 12px; padding: 1px 5px;
    border-radius: var(--radius-sm); background: rgba(110, 118, 129, 0.2);
    color: #e3b341; border: 1px solid rgba(110, 118, 129, 0.3);
  }
  :global(.md-code-block) {
    margin: 10px 0; border-radius: var(--radius-md);
    border: 1px solid var(--border); overflow: hidden; background: #0d1117;
  }
  :global(.md-code-header) {
    display: flex; justify-content: space-between; align-items: center;
    padding: 5px 12px; background: rgba(48, 54, 61, 0.8);
    border-bottom: 1px solid var(--border);
  }
  :global(.md-code-lang) {
    font-family: var(--font-mono); font-size: 11px; color: var(--text-muted); text-transform: lowercase;
  }
  :global(.md-copy-btn) {
    font-size: 11px; padding: 2px 8px; background: rgba(110, 118, 129, 0.2);
    border: 1px solid var(--border); border-radius: var(--radius-sm);
    color: var(--text-secondary); cursor: pointer; transition: all 0.15s;
  }
  :global(.md-copy-btn:hover) { background: rgba(110, 118, 129, 0.4); color: var(--text-primary); }
  :global(.md-code-block pre) {
    margin: 0; padding: 12px 14px; overflow-x: auto;
    font-family: var(--font-mono); font-size: 12px; line-height: 1.6;
  }
  :global(.md-code-block code) {
    font-family: inherit; font-size: inherit; background: none; border: none; padding: 0;
  }

  /* highlight.js tokens */
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
