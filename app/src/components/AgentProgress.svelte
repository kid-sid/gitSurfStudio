<script>
  import { agentCancel } from '../lib/api.js';

  let { plan = null, isRunning = false, onCancel = null } = $props();

  let expanded = $state(true);

  const statusIcon = (status) => {
    switch (status) {
      case 'pending': return '○';
      case 'running': return '●';
      case 'done': return '✓';
      case 'failed': return '✗';
      case 'skipped': return '–';
      default: return '?';
    }
  };

  const statusClass = (status) => {
    switch (status) {
      case 'done': return 'step-done';
      case 'failed': return 'step-failed';
      case 'running': return 'step-running';
      case 'skipped': return 'step-skipped';
      default: return 'step-pending';
    }
  };

  let completedCount = $derived(plan?.steps?.filter(s => s.status === 'done').length ?? 0);
  let totalCount = $derived(plan?.steps?.length ?? 0);
  let progressPercent = $derived(totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0);

  async function handleCancel() {
    try {
      await agentCancel();
      if (onCancel) onCancel();
    } catch (e) {
      console.error('Failed to cancel agent:', e);
    }
  }
</script>

{#if plan}
  <div class="agent-progress">
    <div class="progress-header" role="button" tabindex="0" onclick={() => expanded = !expanded} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') expanded = !expanded; }}>
      <span class="toggle">{expanded ? '▾' : '▸'}</span>
      <span class="goal">{plan.goal}</span>
      <span class="badge">{plan.complexity}</span>
      <span class="count">{completedCount}/{totalCount}</span>
      {#if isRunning}
        <button class="cancel-btn" onclick={(e) => { e.stopPropagation(); handleCancel(); }}>Cancel</button>
      {/if}
    </div>

    <div class="progress-bar-container">
      <div class="progress-bar" style="width: {progressPercent}%"></div>
    </div>

    {#if expanded}
      <div class="steps-list">
        {#each plan.steps as step}
          <div class="step {statusClass(step.status)}">
            <span class="step-icon">{statusIcon(step.status)}</span>
            <span class="step-desc">
              <span class="step-num">Step {step.id}</span>
              {step.description}
            </span>
            {#if step.status === 'failed' && step.error}
              <div class="step-error">{step.error}</div>
            {/if}
          </div>
        {/each}
      </div>
    {/if}
  </div>
{/if}

<style>
  .agent-progress {
    background: var(--bg-secondary, #1e1e2e);
    border: 1px solid var(--border-color, #313244);
    border-radius: 6px;
    margin: 8px 0;
    overflow: hidden;
  }

  .progress-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    cursor: pointer;
    font-size: 12px;
    color: var(--text-primary, #cdd6f4);
  }

  .progress-header:hover {
    background: var(--bg-hover, #262637);
  }

  .toggle {
    color: var(--text-secondary, #a6adc8);
    width: 12px;
  }

  .goal {
    flex: 1;
    font-weight: 600;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 3px;
    background: var(--accent-dim, #31324488);
    color: var(--text-secondary, #a6adc8);
    text-transform: uppercase;
  }

  .count {
    font-size: 11px;
    color: var(--text-secondary, #a6adc8);
    font-variant-numeric: tabular-nums;
  }

  .cancel-btn {
    font-size: 11px;
    padding: 2px 8px;
    border: 1px solid var(--error-color, #f38ba8);
    border-radius: 3px;
    background: transparent;
    color: var(--error-color, #f38ba8);
    cursor: pointer;
  }

  .cancel-btn:hover {
    background: var(--error-color, #f38ba8);
    color: var(--bg-primary, #1e1e2e);
  }

  .progress-bar-container {
    height: 2px;
    background: var(--border-color, #313244);
  }

  .progress-bar {
    height: 100%;
    background: var(--accent-color, #89b4fa);
    transition: width 0.3s ease;
  }

  .steps-list {
    padding: 4px 0;
    max-height: 200px;
    overflow-y: auto;
  }

  .step {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    padding: 4px 12px 4px 20px;
    font-size: 11px;
    color: var(--text-secondary, #a6adc8);
  }

  .step-icon {
    width: 14px;
    text-align: center;
    flex-shrink: 0;
    margin-top: 1px;
  }

  .step-num {
    color: var(--text-tertiary, #6c7086);
    margin-right: 4px;
  }

  .step-desc {
    flex: 1;
  }

  .step-done {
    color: var(--success-color, #a6e3a1);
  }

  .step-done .step-icon {
    color: var(--success-color, #a6e3a1);
  }

  .step-failed {
    color: var(--error-color, #f38ba8);
  }

  .step-failed .step-icon {
    color: var(--error-color, #f38ba8);
  }

  .step-running {
    color: var(--accent-color, #89b4fa);
  }

  .step-running .step-icon {
    color: var(--accent-color, #89b4fa);
    animation: pulse 1s ease-in-out infinite;
  }

  .step-skipped {
    opacity: 0.5;
    text-decoration: line-through;
  }

  .step-error {
    font-size: 10px;
    color: var(--error-color, #f38ba8);
    padding: 2px 0 2px 22px;
    word-break: break-word;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }
</style>
