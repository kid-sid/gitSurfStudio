/**
 * Editor API: completion, symbol peek, linting, symbols.
 */

import { ENGINE_URL } from "./client.js";

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
 */
export async function peekSymbol(name) {
  const response = await fetch(
    `${ENGINE_URL}/peek-symbol?name=${encodeURIComponent(name)}`,
  );
  if (!response.ok) return { symbol: name, results: [] };
  return await response.json();
}

/**
 * Lints file content via the backend (ruff for Python, eslint for JS/TS)
 * @param {string} filePath - File path (for language detection)
 * @param {string} content - Current editor content
 * @param {string} workspace - Workspace root path
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
 * Sends lint diagnostics to the AI for automatic fixing
 * @param {string} filePath - File path
 * @param {string} content - Current file content
 * @param {Array} diagnostics - Array of lint diagnostic objects
 */
export async function fixLint(filePath, content, diagnostics) {
  const response = await fetch(`${ENGINE_URL}/fix-lint`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ file_path: filePath, content, diagnostics }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to fix lint errors");
  }
  return await response.json();
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
