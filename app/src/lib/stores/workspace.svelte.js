/**
 * Workspace store — shared state for the active workspace.
 *
 * Usage in components:
 *   import { workspace } from '$lib/stores/workspace.svelte.js';
 *   workspace.path = '/some/path';
 *   console.log(workspace.engineOnline);
 */

export const workspace = $state({
  /** Absolute path to the active workspace */
  path: "",
  /** Whether this workspace is a cloned GitHub repo */
  isGitHubRepo: false,
  /** Whether the backend engine is reachable */
  engineOnline: false,
  /** Whether MCP servers have finished loading */
  mcpReady: false,
  /** Number of MCP tools available */
  mcpToolCount: 0,
});
