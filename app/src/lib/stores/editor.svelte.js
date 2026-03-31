/**
 * Editor store — shared state for open files and active editor tab.
 *
 * Usage in components:
 *   import { editor } from '$lib/stores/editor.svelte.js';
 *   editor.activeFile = 'src/main.py';
 *   editor.openFiles = [...editor.openFiles, 'src/main.py'];
 */

export const editor = $state({
  /** Path of the currently active file in the editor */
  activeFile: "",
  /** Array of currently open file paths (tabs) */
  openFiles: [],
});
