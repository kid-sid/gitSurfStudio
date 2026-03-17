/**
 * GitSurf Studio — API Client
 * Bridge between the Svelte frontend and the Python AI Engine.
 */

const ENGINE_URL = "http://127.0.0.1:8002";

/**
 * Initializes a workspace (local or GitHub)
 * @param {string} input - Local path or GitHub URL
 */
export async function initWorkspace(input) {
  const response = await fetch(`${ENGINE_URL}/init`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input }),
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
export async function sendChat(query, path, history = [], onLog, onAnswer, onCommand, signal) {
  const response = await fetch(`${ENGINE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, path, history }),
    signal,
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
        } else if (data.type === "answer") {
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
