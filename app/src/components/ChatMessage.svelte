<script>
  import { renderMarkdown } from "../lib/markdown.js";
  import { agentRespond } from "../lib/api.js";
  import AgentProgress from "./AgentProgress.svelte";

  let {
    msg,
    agentPlan = null,
    agentRunning = false,
    agentAskPayload = $bindable(null),
    statusText = "Thinking...",
    lastQuery = "",
    onretry = null,
    oncommand = null,
  } = $props();
</script>

<div class="chat__msg chat__msg--{msg.role}">
  <div class="chat__msg-header">
    <span class="chat__msg-avatar">
      {msg.role === "user" ? "👤" : msg.role === "assistant" ? "🌊" : "⚠️"}
    </span>
    <span class="chat__msg-label">
      {msg.role === "user" ? "You" : msg.role === "assistant" ? "GitSurf" : "Error"}
    </span>
  </div>

  <!-- Activity Feed & Reasoning -->
  {#if msg.thoughts && msg.thoughts.length > 0}
    <div class="chat__activity" class:chat__activity--thinking={msg.thinking}>
      <div class="chat__activity-header">
        <span class="chat__activity-title">
          {msg.thinking ? "Processing..." : "Completed"}
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

  <!-- Tool Actions Timeline -->
  {#if msg.toolActions && msg.toolActions.length > 0}
    <div class="chat__tool-actions" class:chat__tool-actions--thinking={msg.thinking}>
      <details class="chat__tool-actions-details" open={msg.thinking || msg.toolActions.length <= 5}>
        <summary class="chat__tool-actions-header">
          <span class="chat__tool-actions-title">Agent Actions</span>
          <span class="chat__tool-actions-count">{msg.toolActions.length}</span>
        </summary>
        <div class="chat__tool-actions-list">
          {#each msg.toolActions as action}
            <div class="chat__tool-action chat__tool-action--{action.status}">
              <span class="chat__tool-action-icon">
                {#if action.action === 'final_answer'}
                  ✦
                {:else if action.status === 'running'}
                  ●
                {:else if action.status === 'error'}
                  ✗
                {:else}
                  ✓
                {/if}
              </span>
              <div class="chat__tool-action-body">
                <div class="chat__tool-action-main">
                  {#if action.action === 'final_answer'}
                    <span class="chat__tool-action-label">Generating answer</span>
                  {:else}
                    <span class="chat__tool-action-tool">{action.tool}</span>
                    <span class="chat__tool-action-sep">.</span>
                    <span class="chat__tool-action-method">{action.method}</span>
                    {#if action.args && Object.keys(action.args).length > 0}
                      <span class="chat__tool-action-args">
                        ({#each Object.entries(action.args).slice(0, 2) as [k, v], i}{#if i > 0}, {/if}{k}: {typeof v === 'string' ? v.length > 40 ? v.slice(0, 40) + '...' : v : JSON.stringify(v)}{/each}{#if Object.keys(action.args).length > 2}, ...{/if})
                      </span>
                    {/if}
                  {/if}
                </div>
                {#if action.thought && action.action !== 'final_answer'}
                  <div class="chat__tool-action-thought">{action.thought}</div>
                {/if}
                {#if action.observation}
                  <div class="chat__tool-action-obs">{action.observation}</div>
                {/if}
              </div>
              <span class="chat__tool-action-iter">#{action.iteration}</span>
            </div>
          {/each}
        </div>
      </details>
    </div>
  {/if}

  <!-- Agent Progress -->
  {#if msg.role === "assistant" && agentPlan && msg.thinking}
    <AgentProgress plan={agentPlan} isRunning={agentRunning} onCancel={() => {}} />
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

  <!-- Loading indicator -->
  {#if msg.thinking && !msg.content}
    <div class="chat__loading">
      <div class="chat__loading-dots">
        <span></span><span></span><span></span>
      </div>
      <span>{statusText}</span>
    </div>
  {/if}

  <!-- Message body -->
  {#if msg.content}
    {#if msg.role === "assistant"}
      <div class="chat__msg-body chat__msg-body--markdown">
        {#if msg.isStreaming}
          <span class="chat__streaming-text">{msg.content}<span class="chat__cursor"></span></span>
        {:else}
          {@html renderMarkdown(msg.content)}
        {/if}
      </div>
    {:else}
      <div class="chat__msg-body">{msg.content}</div>
    {/if}
    {#if msg.timedOut && onretry}
      <button class="chat__retry" onclick={() => onretry(lastQuery)}>Retry</button>
    {/if}
  {/if}
</div>

<style>
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

  /* Activity Feed */
  .chat__activity {
    margin: 4px 0 8px; background: rgba(48, 54, 61, 0.4);
    border: 1px solid var(--border); border-radius: var(--radius-md);
    overflow: hidden; transition: all 0.3s ease;
  }
  .chat__activity--thinking {
    border-color: rgba(56, 139, 253, 0.4); background: rgba(56, 139, 253, 0.05);
  }
  .chat__activity-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 10px; background: rgba(48, 54, 61, 0.2);
    border-bottom: 1px solid rgba(240, 246, 252, 0.05);
  }
  .chat__activity-title {
    font-size: 11px; font-weight: 600; color: var(--text-secondary);
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .chat__activity-steps {
    font-size: 10px; padding: 2px 6px; background: rgba(56, 139, 253, 0.15);
    color: var(--text-accent); border-radius: 10px;
  }
  .chat__activity-log {
    padding: 8px 10px; display: flex; flex-direction: column; gap: 4px;
  }
  .chat__activity-line {
    font-family: var(--font-mono); font-size: 11px; color: var(--text-secondary);
    white-space: pre-wrap; word-break: break-all; opacity: 0.8;
  }
  .chat__activity-line:last-child { opacity: 1; color: var(--text-primary); }
  .chat__activity-details {
    margin-top: 6px; border-top: 1px solid rgba(240, 246, 252, 0.05); padding-top: 6px;
  }
  .chat__activity-details summary {
    font-size: 10px; color: var(--text-muted); cursor: pointer; user-select: none; outline: none;
  }
  .chat__activity-details summary:hover { color: var(--text-secondary); }
  .chat__activity-full {
    margin-top: 8px; max-height: 150px; overflow-y: auto;
    display: flex; flex-direction: column; gap: 4px;
  }

  /* Tool Actions Timeline */
  .chat__tool-actions {
    margin: 4px 0 8px; background: rgba(48, 54, 61, 0.3);
    border: 1px solid var(--border); border-radius: var(--radius-md);
    overflow: hidden;
  }
  .chat__tool-actions--thinking {
    border-color: rgba(139, 92, 246, 0.3); background: rgba(139, 92, 246, 0.04);
  }
  .chat__tool-actions-details { }
  .chat__tool-actions-header {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 10px; cursor: pointer; user-select: none;
    background: rgba(48, 54, 61, 0.2);
    border-bottom: 1px solid rgba(240, 246, 252, 0.05);
  }
  .chat__tool-actions-header:hover { background: rgba(48, 54, 61, 0.4); }
  .chat__tool-actions-title {
    font-size: 11px; font-weight: 600; color: var(--text-secondary);
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .chat__tool-actions-count {
    font-size: 10px; padding: 2px 6px; background: rgba(139, 92, 246, 0.15);
    color: #a78bfa; border-radius: 10px;
  }
  .chat__tool-actions-list {
    padding: 4px 0; max-height: 300px; overflow-y: auto;
  }
  .chat__tool-action {
    display: flex; align-items: flex-start; gap: 8px;
    padding: 5px 10px; font-size: 11px;
    border-left: 2px solid transparent; transition: all 0.15s;
  }
  .chat__tool-action:hover { background: rgba(240, 246, 252, 0.03); }
  .chat__tool-action--done { border-left-color: #3fb950; }
  .chat__tool-action--running { border-left-color: #58a6ff; }
  .chat__tool-action--error { border-left-color: #f85149; }
  .chat__tool-action-icon {
    width: 14px; text-align: center; flex-shrink: 0; margin-top: 1px;
    font-size: 10px;
  }
  .chat__tool-action--done .chat__tool-action-icon { color: #3fb950; }
  .chat__tool-action--running .chat__tool-action-icon { color: #58a6ff; animation: pulse 1s ease-in-out infinite; }
  .chat__tool-action--error .chat__tool-action-icon { color: #f85149; }
  .chat__tool-action-body { flex: 1; min-width: 0; }
  .chat__tool-action-main {
    font-family: var(--font-mono); font-size: 11px; color: var(--text-primary);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chat__tool-action-tool { color: #d2a8ff; font-weight: 600; }
  .chat__tool-action-sep { color: var(--text-muted); }
  .chat__tool-action-method { color: #79c0ff; }
  .chat__tool-action-args { color: var(--text-secondary); font-size: 10px; }
  .chat__tool-action-label { color: #e3b341; font-weight: 600; }
  .chat__tool-action-thought {
    font-size: 10px; color: var(--text-muted); margin-top: 2px;
    font-style: italic; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .chat__tool-action-obs {
    font-size: 10px; color: var(--text-secondary); margin-top: 2px;
    font-family: var(--font-mono); overflow: hidden; text-overflow: ellipsis;
    white-space: nowrap; max-width: 100%;
  }
  .chat__tool-action-iter {
    font-size: 9px; color: var(--text-muted); flex-shrink: 0;
    font-variant-numeric: tabular-nums;
  }

  /* Agent Ask */
  .chat__agent-ask {
    background: var(--bg-tertiary, #313244); border: 1px solid var(--accent, #89b4fa);
    border-radius: 6px; padding: 10px 12px; margin: 6px 0;
  }
  .chat__agent-ask-question { font-size: 12px; color: var(--text-primary); margin-bottom: 8px; }
  .chat__agent-ask-options { display: flex; gap: 6px; flex-wrap: wrap; }
  .chat__agent-ask-btn {
    font-size: 11px; padding: 4px 12px; border-radius: 4px;
    border: 1px solid var(--accent, #89b4fa); background: transparent;
    color: var(--accent, #89b4fa); cursor: pointer;
  }
  .chat__agent-ask-btn:hover {
    background: var(--accent, #89b4fa); color: var(--bg-primary, #1e1e2e);
  }
  .chat__agent-ask-input input {
    width: 100%; padding: 6px 10px; font-size: 12px;
    background: var(--bg-primary); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text-primary); outline: none;
  }
  .chat__agent-ask-input input:focus { border-color: var(--accent, #89b4fa); }

  /* Loading dots */
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

  /* Streaming text */
  .chat__streaming-text { white-space: pre-wrap; word-break: break-word; font-size: 13px; line-height: 1.6; }
  .chat__cursor {
    display: inline-block; width: 2px; height: 1em;
    background: var(--accent-blue); margin-left: 1px;
    vertical-align: text-bottom; animation: blink 0.8s step-end infinite;
  }
  @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0; } }

  /* Retry button */
  .chat__retry {
    align-self: flex-start; margin-top: 4px; padding: 4px 12px;
    background: rgba(248, 81, 73, 0.1); border: 1px solid rgba(248, 81, 73, 0.3);
    border-radius: var(--radius-md); font-size: 12px; color: var(--accent-red); cursor: pointer;
  }
  .chat__retry:hover { background: rgba(248, 81, 73, 0.2); }

  /* Markdown rendered output */
  .chat__msg-body--markdown { white-space: normal; }
</style>
