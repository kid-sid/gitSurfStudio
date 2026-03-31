/**
 * Chat API: streaming chat, sessions CRUD.
 */

import { ENGINE_URL } from "./client.js";

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
 * @param {string|null} userId - Supabase user ID
 */
export async function sendChat(
  query,
  path,
  history = [],
  onLog,
  onAnswer,
  onCommand,
  signal,
  userId = null,
) {
  const timeoutSignal = AbortSignal.timeout(CHAT_TIMEOUT_MS);
  const combinedSignal = signal
    ? AbortSignal.any([signal, timeoutSignal])
    : timeoutSignal;

  const response = await fetch(`${ENGINE_URL}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query,
      path,
      history,
      user_id: userId,
    }),
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
          fullAnswer += data.content;
          if (onAnswer) onAnswer(fullAnswer);
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
export async function createChatSession(
  userId,
  repoIdentifier,
  title = null,
) {
  try {
    const response = await fetch(`${ENGINE_URL}/chat/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: userId,
        repo_identifier: repoIdentifier,
        title,
      }),
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
    const response = await fetch(
      `${ENGINE_URL}/chat/sessions/${encodeURIComponent(sessionId)}/messages`,
    );
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
    const response = await fetch(
      `${ENGINE_URL}/chat/sessions/${encodeURIComponent(sessionId)}`,
      { method: "DELETE" },
    );
    return response.ok;
  } catch {
    return false;
  }
}
