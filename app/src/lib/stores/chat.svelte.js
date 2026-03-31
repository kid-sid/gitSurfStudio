/**
 * Chat store — shared state for chat panel.
 *
 * Usage in components:
 *   import { chat } from '$lib/stores/chat.svelte.js';
 *   chat.isLoading = true;
 */

export const chat = $state({
  /** Chat message history */
  messages: [],
  /** Whether a chat request is in progress */
  isLoading: false,
  /** Current agent execution plan (null if none) */
  agentPlan: null,
  /** Active chat sessions list */
  sessions: [],
  /** Currently active session ID */
  activeSessionId: null,
});
