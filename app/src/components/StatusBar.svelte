<script>
  let { currentFile = "", engineOnline = false, workspacePath = "" } = $props();

  function getLanguage(path) {
    if (!path) return "Plain Text";
    const ext = path.split(".").pop().toLowerCase();
    const langs = {
      py: "Python", js: "JavaScript", ts: "TypeScript",
      json: "JSON", md: "Markdown", html: "HTML",
      css: "CSS", rs: "Rust", svelte: "Svelte", toml: "TOML",
    };
    return langs[ext] || "Plain Text";
  }
</script>

<footer class="status-bar">
  <div class="status-bar__left">
    <span class="status-bar__item status-bar__engine" class:online={engineOnline}>
      ● {engineOnline ? "Engine Connected" : "Engine Disconnected"}
    </span>
    {#if workspacePath}
      <span class="status-bar__item">📂 {workspacePath.split(/[/\\]/).pop()}</span>
    {/if}
  </div>
  <div class="status-bar__right">
    <span class="status-bar__item">{getLanguage(currentFile)}</span>
    <span class="status-bar__item">UTF-8</span>
    <span class="status-bar__item">GitSurf Studio v0.1.0</span>
  </div>
</footer>

<style>
  .status-bar {
    display: flex; justify-content: space-between; align-items: center;
    height: 24px; padding: 0 10px; background: #1f6feb;
    color: white; font-size: 11px; user-select: none;
  }
  .status-bar__left, .status-bar__right {
    display: flex; gap: 14px; align-items: center;
  }
  .status-bar__item { opacity: 0.9; }
  .status-bar__engine { color: #ffa3a3; }
  .status-bar__engine.online { color: #a3ffba; }
</style>
