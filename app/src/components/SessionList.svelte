<script>
  let {
    sessions = [],
    activeSessionId = null,
    onswitch = null,
    ondelete = null,
  } = $props();
</script>

{#if sessions.length > 0}
  <div class="chat__session-list">
    {#each sessions as s}
      <div
        class="chat__session-item"
        class:chat__session-item--active={s.id === activeSessionId}
        role="button"
        tabindex="0"
        onclick={() => onswitch?.(s.id)}
        onkeydown={(e) => e.key === 'Enter' && onswitch?.(s.id)}
      >
        <span class="chat__session-title">{s.title ?? "Untitled"}</span>
        <button
          class="chat__session-delete"
          onclick={(e) => { e.stopPropagation(); ondelete?.(s.id); }}
          title="Delete session"
        >✕</button>
      </div>
    {/each}
  </div>
{/if}

<style>
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
</style>
