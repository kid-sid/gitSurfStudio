/**
 * GitSurf Studio — API Client
 * Bridge between the Svelte frontend and the Python AI Engine.
 */

const ENGINE_URL = "http://127.0.0.1:8000";

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
 */
export async function sendChat(query, path, history = [], onLog, onAnswer) {
  const response = await fetch(`${ENGINE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, path, history }),
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
