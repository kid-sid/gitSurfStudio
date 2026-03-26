/**
 * GitSurf Studio — API Client
 * Bridge between the Svelte frontend and the Python AI Engine.
 */

const ENGINE_URL = (typeof window !== "undefined" && window.location.hostname !== "localhost" && window.location.hostname !== "127.0.0.1")
  ? `${window.location.protocol}//${window.location.hostname}:8002`
  : "http://127.0.0.1:8002";

/**
 * Initializes a workspace (local or GitHub)
 * @param {string} input - Local path or GitHub URL
 * @param {string|null} userId - Supabase user ID for persistent memory
 */
export async function initWorkspace(input, userId = null) {
  const response = await fetch(`${ENGINE_URL}/init`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, user_id: userId }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to initialize workspace");
  }
  return await response.json();
}

/**
 * Fetches the file tree for a given path
 * @param {string} path - Absolute path to scan
 */
export async function getFileTree(path) {
  const response = await fetch(`${ENGINE_URL}/files?path=${encodeURIComponent(path)}`);
  if (!response.ok) throw new Error("Failed to fetch file tree");
  return await response.json();
}

/**
 * Reads a file's content
 * @param {string} path - Absolute path to the file
 */
export async function readFile(path) {
  const response = await fetch(`${ENGINE_URL}/read?path=${encodeURIComponent(path)}`);
  if (!response.ok) throw new Error("Failed to read file");
  return await response.json();
}

/**
 * Writes content to a file
 * @param {string} path - Absolute path to the file
 * @param {string} content - New file content
 */
export async function writeFile(path, content) {
  const response = await fetch(`${ENGINE_URL}/write`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, content }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to write file");
  }
  return await response.json();
}

/**
 * Creates a directory
 * @param {string} path - Relative or absolute path
 */
export async function createDirectory(path) {
  const response = await fetch(`${ENGINE_URL}/mkdir`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to create directory");
  }
  return await response.json();
}

/**
 * Renames (moves) a file or directory
 * @param {string} oldPath - Current relative or absolute path
 * @param {string} newPath - Desired relative or absolute path
 */
export async function renameEntry(oldPath, newPath) {
  const response = await fetch(`${ENGINE_URL}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to rename");
  }
  return await response.json();
}

/**
 * Recursively deletes a directory
 * @param {string} path - Directory path
 */
export async function deleteDirectory(path) {
  const response = await fetch(`${ENGINE_URL}/delete-dir`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to delete directory");
  }
  return await response.json();
}

/**
 * Checks if the engine is online
 */
export async function checkHealth() {
  try {
    const response = await fetch(`${ENGINE_URL}/health`);
    return response.ok;
  } catch (e) {
    return false;
  }
}

/** Max time to wait for a chat response (5 minutes) */
const CHAT_TIMEOUT_MS = 5 * 60 * 1000;

/**
 * Sends a chat query to the engine and streams the response
 * @param {string} query - User question
 * @param {string} path - Workspace path
 * @param {Array} history - Conversation history
 * @param {Function} onLog - Callback for pipeline logs (thoughts/observations)
 * @param {Function} onAnswer - Callback for the final answer
 * @param {Function} onCommand - Callback for UI commands (e.g. open_file)
 * @param {AbortSignal} signal - Optional AbortSignal to cancel the request
 */
export async function sendChat(query, path, history = [], onLog, onAnswer, onCommand, signal, userId = null, agentMode = false) {
  // Always enforce a 5-minute timeout; combine with caller's abort signal when provided
  const timeoutSignal = AbortSignal.timeout(CHAT_TIMEOUT_MS);
  const combinedSignal = signal ? AbortSignal.any([signal, timeoutSignal]) : timeoutSignal;

  const response = await fetch(`${ENGINE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, path, history, user_id: userId, agent_mode: agentMode }),
    signal: combinedSignal,
  });

  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Chat failed");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let fullAnswer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    const chunk = decoder.decode(value, { stream: true });
    const lines = chunk.split("\n");

    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        const data = JSON.parse(line);
        if (data.type === "log" && onLog) {
          onLog(data.content);
        } else if (data.type === "ui_command" && onCommand) {
          onCommand(data.command, data.args);
        } else if (data.type === "answer_token") {
          // Streaming token — accumulate and notify progressively
          fullAnswer += data.content;
          if (onAnswer) onAnswer(fullAnswer);
        } else if (data.type === "answer") {
          // Non-streaming fallback (mock provider) — deliver all at once
          fullAnswer += data.content;
          if (onAnswer) onAnswer(fullAnswer);
        }
      } catch (e) {
        console.error("Error parsing SSE line:", e);
      }
    }
  }

  return fullAnswer;
}

/**
 * Gets the current git status
 * @param {string} path - Workspace path
 */
export async function gitStatus(path) {
  const response = await fetch(`${ENGINE_URL}/git/status?path=${encodeURIComponent(path)}`);
  if (!response.ok) throw new Error("Failed to fetch git status");
  return await response.json();
}

/**
 * Stages files for commit
 * @param {string} path - Workspace path
 * @param {Array} files - List of relative file paths
 */
export async function gitStage(path, files) {
  const response = await fetch(`${ENGINE_URL}/git/stage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, files }),
  });
  if (!response.ok) throw new Error("Failed to stage files");
  return await response.json();
}

/**
 * Commits staged changes
 * @param {string} path - Workspace path
 * @param {string} message - Commit message
 */
export async function gitCommit(path, message) {
  const response = await fetch(`${ENGINE_URL}/git/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, message }),
  });
  if (!response.ok) throw new Error("Failed to commit changes");
  return await response.json();
}

/**
 * Gets the current branch and all branches
 * @param {string} path - Workspace path
 */
export async function getBranches(path) {
  const response = await fetch(`${ENGINE_URL}/git/branch?path=${encodeURIComponent(path)}`);
  if (!response.ok) throw new Error("Failed to fetch branches");
  return await response.json();
}

/**
 * Checks out a specific branch
 * @param {string} path - Workspace path
 * @param {string} branch - Branch name
 */
export async function checkoutBranch(path, branch) {
  const response = await fetch(`${ENGINE_URL}/git/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, branch }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to checkout branch");
  }
  return await response.json();
}

/**
 * Forks a repository
 * @param {string} path - Current workspace path
 * @param {string} repoName - Source repository (owner/repo)
 */
export async function gitFork(path, repoName) {
  const response = await fetch(`${ENGINE_URL}/git/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, repo_name: repoName }),
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Fork failed");
  }
  return await response.json();
}

/**
 * Checks if the user is authenticated with GitHub
 */
export async function checkAuthStatus() {
  const response = await fetch(`${ENGINE_URL}/auth/status`);
  if (!response.ok) return { authenticated: false };
  return await response.json();
}

/**
 * Initiates the GitHub OAuth login flow
 */
export function loginWithGitHub() {
  window.open(`${ENGINE_URL}/auth/login`, "_blank", "width=600,height=700");
}
/**
 * Stashes local changes
 * @param {string} path - Workspace path
 */
export async function gitStash(path) {
  const response = await fetch(`${ENGINE_URL}/git/stash`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to stash changes");
  }
  return await response.json();
}

/**
 * Pops the latest stash
 * @param {string} path - Workspace path
 */
export async function gitStashPop(path) {
  const response = await fetch(`${ENGINE_URL}/git/stash/pop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to pop stash");
  }
  return await response.json();
}

/**
 * Discards local changes to a file
 * @param {string} path - Workspace path
 * @param {string} file - Relative file path
 */
export async function gitDiscard(path, file) {
  const response = await fetch(`${ENGINE_URL}/git/discard`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, file }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to discard changes");
  }
  return await response.json();
}

/**
 * Deletes the .bak backup file after accept or reject
 * @param {string} path - Absolute path to the original file
 */
export async function cleanupBackup(path) {
  const response = await fetch(`${ENGINE_URL}/cleanup-backup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to cleanup backup");
  }
  return await response.json();
}

/**
 * Restores a file from its .bak backup (created before AI edits)
 * @param {string} path - Absolute path to the file
 */
export async function restoreFile(path) {
  const response = await fetch(`${ENGINE_URL}/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to restore file");
  }
  return await response.json();
}

/**
 * Deletes a newly AI-created file when the user rejects it
 * @param {string} path - Absolute path to the file
 */
export async function deleteFile(path) {
  const response = await fetch(`${ENGINE_URL}/delete-file`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to delete file");
  }
  return await response.json();
}

/**
 * Gets an inline code completion from the LLM
 * @param {string} path - File URI or path (for context)
 * @param {string} prefix - Code before cursor
 * @param {string} suffix - Code after cursor
 * @param {string} language - Language ID
 */
export async function getCompletion(path, prefix, suffix, language = "plaintext") {
  const response = await fetch(`${ENGINE_URL}/complete`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, prefix, suffix, language }),
  });
  if (!response.ok) return null;
  return await response.json();
}

/**
 * Peeks the definition of a symbol by name (F12 / Go to Definition)
 * @param {string} name - Symbol name (function, class, method)
 * @returns {{ symbol: string, results: Array<{file,type,name,start_line,end_line,content}> }}
 */
export async function peekSymbol(name) {
  const response = await fetch(`${ENGINE_URL}/peek-symbol?name=${encodeURIComponent(name)}`);
  if (!response.ok) return { symbol: name, results: [] };
  return await response.json();
}

/**
 * Returns added/modified line numbers vs HEAD for a file (for gutter decorations)
 * @param {string} path - Absolute path to the file
 */
export async function getGitDiffLines(path) {
  try {
    const response = await fetch(`${ENGINE_URL}/git/diff-lines?path=${encodeURIComponent(path)}`);
    if (!response.ok) return { added: [], modified: [] };
    return await response.json();
  } catch {
    return { added: [], modified: [] };
  }
}

// ── Chat Session Management ───────────────────────────────────────────────────

/**
 * Lists chat sessions for a user+repo (newest first).
 * @param {string} userId
 * @param {string} repoIdentifier
 */
export async function getChatSessions(userId, repoIdentifier) {
  try {
    const url = `${ENGINE_URL}/chat/sessions?user_id=${encodeURIComponent(userId)}&repo_identifier=${encodeURIComponent(repoIdentifier)}`;
    const response = await fetch(url);
    if (!response.ok) return { sessions: [] };
    return await response.json();
  } catch {
    return { sessions: [] };
  }
}

/**
 * Creates a new chat session.
 * @param {string} userId
 * @param {string} repoIdentifier
 * @param {string|null} title
 */
export async function createChatSession(userId, repoIdentifier, title = null) {
  try {
    const response = await fetch(`${ENGINE_URL}/chat/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, repo_identifier: repoIdentifier, title }),
    });
    if (!response.ok) return { session_id: null };
    return await response.json();
  } catch {
    return { session_id: null };
  }
}

/**
 * Loads messages for a session (for displaying history).
 * @param {string} sessionId
 */
export async function loadSessionMessages(sessionId) {
  try {
    const response = await fetch(`${ENGINE_URL}/chat/sessions/${encodeURIComponent(sessionId)}/messages`);
    if (!response.ok) return { messages: [] };
    return await response.json();
  } catch {
    return { messages: [] };
  }
}

/**
 * Deletes a chat session.
 * @param {string} sessionId
 */
export async function deleteChatSession(sessionId) {
  try {
    const response = await fetch(`${ENGINE_URL}/chat/sessions/${encodeURIComponent(sessionId)}`, {
      method: "DELETE",
    });
    return response.ok;
  } catch {
    return false;
  }
}

/**
 * Lints file content via the backend (ruff for Python, eslint for JS/TS)
 * @param {string} filePath - File path (for language detection)
 * @param {string} content - Current editor content
 * @param {string} workspace - Workspace root path
 * @returns {Promise<{diagnostics: Array}>}
 */
export async function lintCode(filePath, content, workspace = "") {
  try {
    const response = await fetch(`${ENGINE_URL}/lint`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ file_path: filePath, content, workspace }),
    });
    if (!response.ok) return { diagnostics: [] };
    return await response.json();
  } catch {
    return { diagnostics: [] };
  }
}

/**
 * Fetches symbols for a given path
 * @param {string} path - Absolute path to the file or directory
 * @param {string} workspace - Optional workspace root to resolve relative path
 */
export async function getSymbols(path, workspace) {
  let url = `${ENGINE_URL}/symbols?path=${encodeURIComponent(path)}`;
  if (workspace) url += `&workspace=${encodeURIComponent(workspace)}`;

  const response = await fetch(url);
  if (!response.ok) throw new Error("Failed to fetch symbols");
  return await response.json();
}


// ── Agent API ─────────────────────────────────────────────────────────────

/**
 * Rollback agent changes — all files or a single file
 * @param {string} changesetId - The changeset to rollback
 * @param {string|null} filePath - Optional specific file to rollback
 */
export async function agentRollback(changesetId, filePath = null) {
  const response = await fetch(`${ENGINE_URL}/agent/rollback`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changeset_id: changesetId, file_path: filePath }),
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Rollback failed");
  }
  return await response.json();
}

/**
 * Accept agent changes — clean up backups
 * @param {string} changesetId - The changeset to accept
 */
export async function agentAccept(changesetId) {
  const response = await fetch(`${ENGINE_URL}/agent/accept`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ changeset_id: changesetId }),
  });
  if (!response.ok) throw new Error("Accept failed");
  return await response.json();
}

/**
 * Cancel the currently running agent task
 */
export async function agentCancel() {
  const response = await fetch(`${ENGINE_URL}/agent/cancel`, {
    method: "POST",
  });
  if (!response.ok) throw new Error("Cancel failed");
  return await response.json();
}

/**
 * Send a user response to a paused agent (human-in-the-loop)
 * @param {string} userResponse - The user's answer
 */
export async function agentRespond(userResponse) {
  const response = await fetch(`${ENGINE_URL}/agent/respond`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ response: userResponse }),
  });
  if (!response.ok) throw new Error("Respond failed");
  return await response.json();
}

/**
 * List all active changesets
 */
export async function listChangesets() {
  const response = await fetch(`${ENGINE_URL}/agent/changesets`);
  if (!response.ok) throw new Error("Failed to list changesets");
  return await response.json();
}
