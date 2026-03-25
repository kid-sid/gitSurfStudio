<script>
  import { supabase } from "../lib/supabase.js";

  let { onauthsuccess = null } = $props();

  let mode = $state("login"); // "login" | "signup"
  let email = $state("");
  let password = $state("");
  let loading = $state(false);
  let error = $state("");
  let info = $state("");

  async function handleSubmit() {
    error = "";
    info = "";
    if (!email.trim() || !password) {
      error = "Email and password are required.";
      return;
    }
    loading = true;
    try {
      if (mode === "login") {
        const { error: e } = await supabase.auth.signInWithPassword({ email, password });
        if (e) throw e;
      } else {
        const { error: e } = await supabase.auth.signUp({ email, password });
        if (e) throw e;
        info = "Check your inbox to confirm your email, then log in.";
        mode = "login";
        password = "";
        loading = false;
        return;
      }
      if (onauthsuccess) onauthsuccess();
    } catch (e) {
      error = e.message ?? "Authentication failed.";
    } finally {
      loading = false;
    }
  }

  async function handleGitHub() {
    error = "";
    loading = true;
    try {
      const { error: e } = await supabase.auth.signInWithOAuth({
        provider: "github",
        options: { redirectTo: window.location.origin },
      });
      if (e) throw e;
    } catch (e) {
      error = e.message ?? "GitHub login failed.";
      loading = false;
    }
  }

  function handleKeydown(event) {
    if (event.key === "Enter") handleSubmit();
  }
</script>

<div class="auth-page">
  <div class="auth-card">
    <div class="auth-logo">🌊</div>
    <h1 class="auth-title">GitSurf Studio</h1>
    <p class="auth-subtitle">The AI-native IDE for understanding codebases.</p>

    <!-- Tab switcher -->
    <div class="auth-tabs">
      <button
        class="auth-tab"
        class:active={mode === "login"}
        onclick={() => { mode = "login"; error = ""; info = ""; }}
      >Log In</button>
      <button
        class="auth-tab"
        class:active={mode === "signup"}
        onclick={() => { mode = "signup"; error = ""; info = ""; }}
      >Sign Up</button>
    </div>

    {#if info}
      <div class="auth-info">{info}</div>
    {/if}
    {#if error}
      <div class="auth-error">⚠️ {error}</div>
    {/if}

    <!-- Email / password form -->
    <div class="auth-form">
      <label class="auth-label" for="auth-email">Email</label>
      <input
        id="auth-email"
        class="auth-input"
        type="email"
        placeholder="you@example.com"
        bind:value={email}
        onkeydown={handleKeydown}
        disabled={loading}
        autocomplete="email"
      />

      <label class="auth-label" for="auth-password">Password</label>
      <input
        id="auth-password"
        class="auth-input"
        type="password"
        placeholder={mode === "signup" ? "Min. 8 characters" : "Your password"}
        bind:value={password}
        onkeydown={handleKeydown}
        disabled={loading}
        autocomplete={mode === "signup" ? "new-password" : "current-password"}
      />

      <button class="auth-btn-primary" onclick={handleSubmit} disabled={loading}>
        {loading ? "Please wait…" : mode === "login" ? "Log In" : "Create Account"}
      </button>
    </div>

    <div class="auth-divider"><span>or</span></div>

    <!-- OAuth -->
    <button class="auth-btn-github" onclick={handleGitHub} disabled={loading}>
      <svg class="github-icon" viewBox="0 0 24 24" fill="currentColor" width="18" height="18">
        <path d="M12 0C5.37 0 0 5.37 0 12c0 5.31 3.435 9.795 8.205 11.385.6.105.825-.255.825-.57 0-.285-.015-1.23-.015-2.235-3.015.555-3.795-.735-4.035-1.41-.135-.345-.72-1.41-1.23-1.695-.42-.225-1.02-.78-.015-.795.945-.015 1.62.87 1.845 1.23 1.08 1.815 2.805 1.305 3.495.99.105-.78.42-1.305.765-1.605-2.67-.3-5.46-1.335-5.46-5.925 0-1.305.465-2.385 1.23-3.225-.12-.3-.54-1.53.12-3.18 0 0 1.005-.315 3.3 1.23.96-.27 1.98-.405 3-.405s2.04.135 3 .405c2.295-1.56 3.3-1.23 3.3-1.23.66 1.65.24 2.88.12 3.18.765.84 1.23 1.905 1.23 3.225 0 4.605-2.805 5.625-5.475 5.925.435.375.81 1.095.81 2.22 0 1.605-.015 2.895-.015 3.3 0 .315.225.69.825.57A12.02 12.02 0 0024 12c0-6.63-5.37-12-12-12z"/>
      </svg>
      Continue with GitHub
    </button>
  </div>
</div>

<style>
  .auth-page {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 100%;
    background: var(--bg-primary);
    padding: 24px;
  }

  .auth-card {
    width: 100%;
    max-width: 400px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 40px 36px;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 0;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.4);
    animation: slideUp 0.3s ease-out;
  }

  @keyframes slideUp {
    from { opacity: 0; transform: translateY(16px); }
    to   { opacity: 1; transform: translateY(0); }
  }

  .auth-logo { font-size: 48px; margin-bottom: 12px; }

  .auth-title {
    font-size: 22px;
    font-weight: 700;
    color: var(--text-primary);
    margin: 0 0 6px;
  }

  .auth-subtitle {
    font-size: 13px;
    color: var(--text-secondary);
    margin: 0 0 28px;
    text-align: center;
  }

  /* Tabs */
  .auth-tabs {
    display: flex;
    width: 100%;
    background: var(--bg-tertiary);
    border-radius: var(--radius-md);
    padding: 3px;
    margin-bottom: 20px;
  }

  .auth-tab {
    flex: 1;
    padding: 7px;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-secondary);
    background: transparent;
    border: none;
    border-radius: calc(var(--radius-md) - 2px);
    cursor: pointer;
    transition: all 0.15s;
  }

  .auth-tab.active {
    background: var(--bg-secondary);
    color: var(--text-primary);
    box-shadow: 0 1px 4px rgba(0, 0, 0, 0.3);
  }

  /* Info / error banners */
  .auth-info {
    width: 100%;
    padding: 10px 14px;
    background: rgba(63, 185, 80, 0.1);
    border: 1px solid rgba(63, 185, 80, 0.25);
    border-radius: var(--radius-md);
    color: var(--accent-green);
    font-size: 13px;
    margin-bottom: 14px;
  }

  .auth-error {
    width: 100%;
    padding: 10px 14px;
    background: rgba(248, 81, 73, 0.08);
    border: 1px solid rgba(248, 81, 73, 0.2);
    border-radius: var(--radius-md);
    color: var(--accent-red);
    font-size: 13px;
    margin-bottom: 14px;
  }

  /* Form */
  .auth-form {
    width: 100%;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .auth-label {
    font-size: 12px;
    font-weight: 500;
    color: var(--text-secondary);
    margin-bottom: -4px;
  }

  .auth-input {
    width: 100%;
    padding: 10px 14px;
    background: var(--bg-primary);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    color: var(--text-primary);
    font-size: 14px;
    outline: none;
    transition: border-color 0.15s;
    box-sizing: border-box;
  }

  .auth-input:focus { border-color: var(--accent-blue); }
  .auth-input::placeholder { color: var(--text-muted); }
  .auth-input:disabled { opacity: 0.5; cursor: not-allowed; }

  .auth-btn-primary {
    width: 100%;
    padding: 11px;
    margin-top: 4px;
    background: var(--accent-blue);
    color: white;
    border: none;
    border-radius: var(--radius-md);
    font-size: 14px;
    font-weight: 600;
    cursor: pointer;
    transition: background 0.15s, opacity 0.15s;
  }

  .auth-btn-primary:hover:not(:disabled) { background: #1f6feb; }
  .auth-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }

  /* Divider */
  .auth-divider {
    width: 100%;
    display: flex;
    align-items: center;
    gap: 12px;
    margin: 20px 0;
    color: var(--text-muted);
    font-size: 12px;
  }

  .auth-divider::before,
  .auth-divider::after {
    content: "";
    flex: 1;
    height: 1px;
    background: var(--border);
  }

  /* GitHub button */
  .auth-btn-github {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 11px;
    background: var(--bg-tertiary);
    color: var(--text-primary);
    border: 1px solid var(--border);
    border-radius: var(--radius-md);
    font-size: 14px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }

  .auth-btn-github:hover:not(:disabled) {
    background: var(--bg-hover);
    border-color: var(--text-muted);
  }

  .auth-btn-github:disabled { opacity: 0.5; cursor: not-allowed; }

  .github-icon { flex-shrink: 0; }
</style>
