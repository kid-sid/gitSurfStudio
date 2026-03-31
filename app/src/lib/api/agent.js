/**
 * Agent API: rollback, accept, cancel, respond, list changesets.
 */

import { ENGINE_URL } from "./client.js";

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
