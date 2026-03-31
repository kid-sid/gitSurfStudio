<script>
  let {
    pendingDiff = null,
    activeFile = "",
    onaccept = null,
    onreject = null,
  } = $props();
</script>

{#if pendingDiff && pendingDiff.path === activeFile}
  {@const s = pendingDiff.stats}
  {@const isNew = pendingDiff.isNewFile}
  <div class="editor__diff-bar">
    <span class="editor__diff-icon">{isNew ? "✨" : "⚡"}</span>
    <span class="editor__diff-label">{isNew ? "AI created this file" : "AI edited this file"}</span>
    <div class="editor__diff-stats">
      {#if s.added > 0}
        <span class="stat stat--added">+{s.added}</span>
      {/if}
      {#if s.changed > 0}
        <span class="stat stat--changed">~{s.changed}</span>
      {/if}
      {#if s.deleted > 0}
        <span class="stat stat--deleted">-{s.deleted}</span>
      {/if}
    </div>
    <div class="editor__diff-actions">
      <button class="diff-btn diff-btn--accept" onclick={onaccept} title={isNew ? "Keep this file" : "Keep AI changes"}>
        ✓ {isNew ? "Keep" : "Accept"}
      </button>
      <button class="diff-btn diff-btn--reject" onclick={onreject} title={isNew ? "Delete this file" : "Restore original"}>
        ✗ {isNew ? "Delete" : "Reject"}
      </button>
    </div>
  </div>
{/if}

<style>
  .editor__diff-bar {
    display: flex; align-items: center; gap: 10px;
    padding: 5px 12px; flex-shrink: 0;
    background: rgba(27, 67, 50, 0.5);
    border-bottom: 1px solid rgba(63, 185, 80, 0.3);
    animation: slideDown 0.2s ease;
  }
  @keyframes slideDown {
    from { opacity: 0; transform: translateY(-4px); }
    to   { opacity: 1; transform: translateY(0); }
  }
  .editor__diff-icon { font-size: 13px; }
  .editor__diff-label { font-size: 12px; color: #3fb950; font-weight: 500; flex-shrink: 0; }
  .editor__diff-stats { display: flex; gap: 6px; flex: 1; }
  .stat {
    font-size: 11px; font-weight: 600; font-family: var(--font-mono);
    padding: 1px 5px; border-radius: 3px;
  }
  .stat--added   { color: #3fb950; background: rgba(63, 185, 80, 0.15); }
  .stat--changed { color: #e3b341; background: rgba(227, 179, 65, 0.15); }
  .stat--deleted { color: #f85149; background: rgba(248, 81, 73, 0.15); }
  .editor__diff-actions { display: flex; gap: 6px; }
  .diff-btn {
    font-size: 11px; font-weight: 600; padding: 3px 10px;
    border-radius: 4px; cursor: pointer; border: 1px solid;
    transition: all 0.15s;
  }
  .diff-btn--accept {
    color: #3fb950; border-color: rgba(63, 185, 80, 0.4);
    background: rgba(63, 185, 80, 0.1);
  }
  .diff-btn--accept:hover { background: rgba(63, 185, 80, 0.25); }
  .diff-btn--reject {
    color: #f85149; border-color: rgba(248, 81, 73, 0.4);
    background: rgba(248, 81, 73, 0.1);
  }
  .diff-btn--reject:hover { background: rgba(248, 81, 73, 0.25); }
</style>
