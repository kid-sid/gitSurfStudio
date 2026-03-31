/**
 * Workspace API: init, file tree, read, write, mkdir, rename, delete, restore.
 */

import { ENGINE_URL } from "./client.js";

/**
 * Initializes a workspace (local or GitHub)
 * @param {string} input - Local path or GitHub URL
 * @param {string|null} userId - Supabase user ID for persistent memory
 */
export async function initWorkspace(input, userId = null) {
  const response = await fetch(`${ENGINE_URL}/init`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input, user_id: userId }),
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
  const response = await fetch(
    `${ENGINE_URL}/files?path=${encodeURIComponent(path)}`,
  );
  if (!response.ok) throw new Error("Failed to fetch file tree");
  return await response.json();
}

/**
 * Reads a file's content
 * @param {string} path - Absolute path to the file
 */
export async function readFile(path) {
  const response = await fetch(
    `${ENGINE_URL}/read?path=${encodeURIComponent(path)}`,
  );
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
 * Creates a directory
 * @param {string} path - Relative or absolute path
 */
export async function createDirectory(path) {
  const response = await fetch(`${ENGINE_URL}/mkdir`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to create directory");
  }
  return await response.json();
}

/**
 * Renames (moves) a file or directory
 * @param {string} oldPath - Current relative or absolute path
 * @param {string} newPath - Desired relative or absolute path
 */
export async function renameEntry(oldPath, newPath) {
  const response = await fetch(`${ENGINE_URL}/rename`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ old_path: oldPath, new_path: newPath }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to rename");
  }
  return await response.json();
}

/**
 * Recursively deletes a directory
 * @param {string} path - Directory path
 */
export async function deleteDirectory(path) {
  const response = await fetch(`${ENGINE_URL}/delete-dir`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to delete directory");
  }
  return await response.json();
}

/**
 * Restores a file from its .bak backup (created before AI edits)
 * @param {string} path - Absolute path to the file
 */
export async function restoreFile(path) {
  const response = await fetch(`${ENGINE_URL}/restore`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to restore file");
  }
  return await response.json();
}

/**
 * Deletes the .bak backup file after accept or reject
 * @param {string} path - Absolute path to the original file
 */
export async function cleanupBackup(path) {
  const response = await fetch(`${ENGINE_URL}/cleanup-backup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to cleanup backup");
  }
  return await response.json();
}

/**
 * Deletes a newly AI-created file when the user rejects it
 * @param {string} path - Absolute path to the file
 */
export async function deleteFile(path) {
  const response = await fetch(`${ENGINE_URL}/delete-file`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to delete file");
  }
  return await response.json();
}
