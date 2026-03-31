/**
 * Preview API: start/stop dev server for live preview.
 */

import { ENGINE_URL } from "./client.js";

/**
 * Starts a dev server for live preview
 * @param {string} workspace - Workspace path
 * @param {string} [command] - Optional custom dev command
 * @param {number} [port] - Optional port number
 */
export async function startPreview(workspace, command = "", port = 3000) {
  const body = { workspace };
  if (command) body.command = command;
  if (port !== 3000) body.port = port;

  const response = await fetch(`${ENGINE_URL}/preview/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to start preview");
  }
  return await response.json();
}

/**
 * Stops the running dev server
 * @param {string} workspace - Workspace path
 */
export async function stopPreview(workspace) {
  const response = await fetch(`${ENGINE_URL}/preview/stop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ workspace }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to stop preview");
  }
  return await response.json();
}

/**
 * Gets preview status
 */
export async function getPreviewStatus() {
  const response = await fetch(`${ENGINE_URL}/preview/status`);
  if (!response.ok) return { previews: {} };
  return await response.json();
}
