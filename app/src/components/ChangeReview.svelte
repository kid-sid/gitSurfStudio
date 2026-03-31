<script>
  import { agentRollback, agentAccept } from '../lib/api.js';

  let { changeset = null, onAccepted = null, onRolledBack = null } = $props();

  let loading = $state('');  // '' | 'accepting' | 'rolling_back' | 'rolling_back_file'
  let expanded = $state(true);

  async function handleAcceptAll() {
    if (!changeset?.id) return;
    loading = 'accepting';
    try {
      await agentAccept(changeset.id);
      if (onAccepted) onAccepted(changeset.id);
    } catch (e) {
      console.error('Failed to accept changeset:', e);
    } finally {
      loading = '';
    }
  }

  async function handleRollbackAll() {
    if (!changeset?.id) return;
    loading = 'rolling_back';
    try {
      await agentRollback(changeset.id);
      if (onRolledBack) onRolledBack(changeset.id);
    } catch (e) {
      console.error('Failed to rollback changeset:', e);
    } finally {
      loading = '';
    }
  }

  async function handleRollbackFile(filePath) {
    if (!changeset?.id) return;
    loading = 'rolling_back_file';
    try {
      await agentRollback(changeset.id, filePath);
      // Remove the file from the local changeset view
      if (changeset.files) {
        changeset.files = changeset.files.filter(f => f.path !== filePath);
      }
    } catch (e) {
      console.error('Failed to rollback file:', e);
    } finally {
      loading = '';
    }
  }

  const actionIcon = (action) => {
    switch (action) {
      case 'created': return '+';
      case 'deleted': return '−';
      case 'modified': return '~';
      default: return '?';
    }
  };

  const actionClass = (action) => {
    switch (action) {
      case 'created': return 'file-created';
      case 'deleted': return 'file-deleted';
      case 'modified': return 'file-modified';
      default: return '';
    }
  };
</script>

{#if changeset && changeset.files?.length > 0}
  <div class="change-review">
    <div class="review-header" role="button" tabindex="0" onclick={() => expanded = !expanded} onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') expanded = !expanded; }}>
      <span class="toggle">{expanded ? '▾' : '▸'}</span>
      <span class="title">Changes ({changeset.files.length} files)</span>
      <div class="actions">
        <button class="accept-btn" onclick={(e) => { e.stopPropagation(); handleAcceptAll(); }}
          disabled={loading !== ''}>
          {loading === 'accepting' ? 'Accepting...' : 'Accept All'}
        </button>
        <button class="rollback-btn" onclick={(e) => { e.stopPropagation(); handleRollbackAll(); }}
          disabled={loading !== ''}>
          {loading === 'rolling_back' ? 'Rolling back...' : 'Rollback All'}
        </button>
      </div>
    </div>

    {#if expanded}
      <div class="file-list">
        {#each changeset.files as file}
          <div class="file-row {actionClass(file.action)}">
            <span class="file-icon">{actionIcon(file.action)}</span>
            <span class="file-path">{file.path}</span>
            <span class="file-summary">{file.diff_summary}</span>
            <button class="file-rollback" onclick={() => handleRollbackFile(file.path)}
              disabled={loading !== ''}
              title="Rollback this file">
              ↩
            </button>
          </div>
        {/each}
      </div>
      {#if changeset.commands_run > 0}
        <div class="commands-note">
          {changeset.commands_run} command(s) were executed during this task
        </div>
      {/if}
    {/if}
  </div>
{/if}

<style>
  .change-review {
    background: var(--bg-secondary, #1e1e2e);
    border: 1px solid var(--border-color, #313244);
    border-radius: 6px;
    margin: 8px 0;
    overflow: hidden;
  }

  .review-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 12px;
    cursor: pointer;
    font-size: 12px;
    color: var(--text-primary, #cdd6f4);
  }

  .review-header:hover {
    background: var(--bg-hover, #262637);
  }

  .toggle {
    color: var(--text-secondary, #a6adc8);
    width: 12px;
  }

  .title {
    flex: 1;
    font-weight: 600;
  }

  .actions {
    display: flex;
    gap: 6px;
  }

  .accept-btn, .rollback-btn {
    font-size: 11px;
    padding: 3px 10px;
    border-radius: 3px;
    cursor: pointer;
    border: 1px solid;
  }

  .accept-btn {
    border-color: var(--success-color, #a6e3a1);
    background: transparent;
    color: var(--success-color, #a6e3a1);
  }

  .accept-btn:hover:not(:disabled) {
    background: var(--success-color, #a6e3a1);
    color: var(--bg-primary, #1e1e2e);
  }

  .rollback-btn {
    border-color: var(--warning-color, #fab387);
    background: transparent;
    color: var(--warning-color, #fab387);
  }

  .rollback-btn:hover:not(:disabled) {
    background: var(--warning-color, #fab387);
    color: var(--bg-primary, #1e1e2e);
  }

  .accept-btn:disabled, .rollback-btn:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .file-list {
    padding: 2px 0;
  }

  .file-row {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 12px 3px 20px;
    font-size: 11px;
    font-family: var(--font-mono, 'Consolas', monospace);
  }

  .file-row:hover {
    background: var(--bg-hover, #262637);
  }

  .file-icon {
    width: 14px;
    text-align: center;
    font-weight: bold;
    flex-shrink: 0;
  }

  .file-path {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .file-summary {
    color: var(--text-tertiary, #6c7086);
    font-size: 10px;
    white-space: nowrap;
  }

  .file-rollback {
    background: none;
    border: none;
    color: var(--text-secondary, #a6adc8);
    cursor: pointer;
    font-size: 13px;
    padding: 0 4px;
    opacity: 0;
    transition: opacity 0.15s;
  }

  .file-row:hover .file-rollback {
    opacity: 1;
  }

  .file-rollback:hover {
    color: var(--warning-color, #fab387);
  }

  .file-created .file-icon { color: var(--success-color, #a6e3a1); }
  .file-deleted .file-icon { color: var(--error-color, #f38ba8); }
  .file-modified .file-icon { color: var(--accent-color, #89b4fa); }

  .file-created .file-path { color: var(--success-color, #a6e3a1); }
  .file-deleted .file-path { color: var(--error-color, #f38ba8); text-decoration: line-through; }
  .file-modified .file-path { color: var(--text-primary, #cdd6f4); }

  .commands-note {
    font-size: 10px;
    color: var(--text-tertiary, #6c7086);
    padding: 4px 12px 6px 20px;
    font-style: italic;
  }
</style>
