/**
 * Git API: status, stage, commit, branch, checkout, stash, discard, fork, diff-lines.
 */

import { ENGINE_URL } from "./client.js";

/**
 * Gets the current git status
 * @param {string} path - Workspace path
 */
export async function gitStatus(path) {
  const response = await fetch(
    `${ENGINE_URL}/git/status?path=${encodeURIComponent(path)}`,
  );
  if (!response.ok) throw new Error("Failed to fetch git status");
  return await response.json();
}

/**
 * Stages files for commit
 * @param {string} path - Workspace path
 * @param {Array} files - List of relative file paths
 */
export async function gitStage(path, files) {
  const response = await fetch(`${ENGINE_URL}/git/stage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, files }),
  });
  if (!response.ok) throw new Error("Failed to stage files");
  return await response.json();
}

/**
 * Commits staged changes
 * @param {string} path - Workspace path
 * @param {string} message - Commit message
 */
export async function gitCommit(path, message) {
  const response = await fetch(`${ENGINE_URL}/git/commit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, message }),
  });
  if (!response.ok) throw new Error("Failed to commit changes");
  return await response.json();
}

/**
 * Gets the current branch and all branches
 * @param {string} path - Workspace path
 */
export async function getBranches(path) {
  const response = await fetch(
    `${ENGINE_URL}/git/branch?path=${encodeURIComponent(path)}`,
  );
  if (!response.ok) throw new Error("Failed to fetch branches");
  return await response.json();
}

/**
 * Checks out a specific branch
 * @param {string} path - Workspace path
 * @param {string} branch - Branch name
 */
export async function checkoutBranch(path, branch) {
  const response = await fetch(`${ENGINE_URL}/git/checkout`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, branch }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to checkout branch");
  }
  return await response.json();
}

/**
 * Forks a repository
 * @param {string} path - Current workspace path
 * @param {string} repoName - Source repository (owner/repo)
 */
export async function gitFork(path, repoName) {
  const response = await fetch(`${ENGINE_URL}/git/fork`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, repo_name: repoName }),
  });
  if (!response.ok) {
    const err = await response.json();
    throw new Error(err.detail || "Fork failed");
  }
  return await response.json();
}

/**
 * Stashes local changes
 * @param {string} path - Workspace path
 */
export async function gitStash(path) {
  const response = await fetch(`${ENGINE_URL}/git/stash`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to stash changes");
  }
  return await response.json();
}

/**
 * Pops the latest stash
 * @param {string} path - Workspace path
 */
export async function gitStashPop(path) {
  const response = await fetch(`${ENGINE_URL}/git/stash/pop`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to pop stash");
  }
  return await response.json();
}

/**
 * Discards local changes to a file
 * @param {string} path - Workspace path
 * @param {string} file - Relative file path
 */
export async function gitDiscard(path, file) {
  const response = await fetch(`${ENGINE_URL}/git/discard`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path, file }),
  });
  if (!response.ok) {
    const error = await response.json();
    throw new Error(error.detail || "Failed to discard changes");
  }
  return await response.json();
}

/**
 * Returns added/modified line numbers vs HEAD for a file (for gutter decorations)
 * @param {string} path - Absolute path to the file
 */
export async function getGitDiffLines(path) {
  try {
    const response = await fetch(
      `${ENGINE_URL}/git/diff-lines?path=${encodeURIComponent(path)}`,
    );
    if (!response.ok) return { added: [], modified: [] };
    return await response.json();
  } catch {
    return { added: [], modified: [] };
  }
}
