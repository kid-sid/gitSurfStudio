/**
 * Barrel re-export — import everything from one place.
 *
 * Usage:
 *   import { sendChat, gitStatus, checkHealth } from '$lib/api';
 */

export { ENGINE_URL, checkHealth } from "./client.js";
export {
  initWorkspace,
  getFileTree,
  readFile,
  writeFile,
  createDirectory,
  renameEntry,
  deleteDirectory,
  restoreFile,
  cleanupBackup,
  deleteFile,
  getCacheStatus,
  cleanupCache,
  purgeCache,
} from "./workspace.js";
export {
  sendChat,
  getChatSessions,
  createChatSession,
  loadSessionMessages,
  deleteChatSession,
} from "./chat.js";
export {
  gitStatus,
  gitStage,
  gitCommit,
  getBranches,
  checkoutBranch,
  gitFork,
  gitStash,
  gitStashPop,
  gitDiscard,
  getGitDiffLines,
} from "./git.js";
export {
  getCompletion,
  peekSymbol,
  lintCode,
  fixLint,
  getSymbols,
} from "./editor.js";
export {
  agentRollback,
  agentAccept,
  agentCancel,
  agentRespond,
  listChangesets,
} from "./agent.js";
export { checkAuthStatus, loginWithGitHub } from "./auth.js";
export { startPreview, stopPreview, getPreviewStatus } from "./preview.js";
