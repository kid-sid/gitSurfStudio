/**
 * FileWatcher — connects to the backend WS /watch endpoint
 * and dispatches CustomEvents on window for file changes.
 *
 * Events dispatched:
 *   "fs-changed"  → { detail: { path, change } }
 *     change: "added" | "modified" | "deleted"
 *
 *   "fs-batch"    → { detail: { changes: [{path, change}, ...] } }
 *     Batched version, fired every ~500ms with accumulated changes.
 */

import { ENGINE_URL } from "./api/client.js";

let ws = null;
let reconnectTimer = null;
let batchBuffer = [];
let batchTimer = null;
const BATCH_INTERVAL = 500;
const RECONNECT_DELAY = 3000;

function getWsUrl() {
  const url = new URL(ENGINE_URL);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.origin;
}

function flushBatch() {
  if (batchBuffer.length === 0) return;
  const changes = [...batchBuffer];
  batchBuffer = [];
  window.dispatchEvent(new CustomEvent("fs-batch", { detail: { changes } }));
}

function handleMessage(data) {
  try {
    const msg = JSON.parse(data);
    if (msg.type === "change") {
      window.dispatchEvent(
        new CustomEvent("fs-changed", { detail: { path: msg.path, change: msg.change } })
      );
      batchBuffer.push({ path: msg.path, change: msg.change });
      clearTimeout(batchTimer);
      batchTimer = setTimeout(flushBatch, BATCH_INTERVAL);
    }
  } catch {
    // ignore malformed messages
  }
}

export function startWatcher(workspacePath) {
  stopWatcher();
  if (!workspacePath) return;

  const wsUrl = `${getWsUrl()}/watch?path=${encodeURIComponent(workspacePath)}`;

  try {
    ws = new WebSocket(wsUrl);

    ws.onmessage = (e) => handleMessage(e.data);

    ws.onclose = () => {
      ws = null;
      // Auto-reconnect
      reconnectTimer = setTimeout(() => startWatcher(workspacePath), RECONNECT_DELAY);
    };

    ws.onerror = () => {
      // onclose will fire after this
    };
  } catch {
    reconnectTimer = setTimeout(() => startWatcher(workspacePath), RECONNECT_DELAY);
  }
}

export function stopWatcher() {
  clearTimeout(reconnectTimer);
  clearTimeout(batchTimer);
  flushBatch();
  if (ws) {
    try { ws.close(); } catch { /* ignore */ }
    ws = null;
  }
}
