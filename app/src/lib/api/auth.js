/**
 * Auth API: GitHub OAuth status and login.
 */

import { ENGINE_URL } from "./client.js";

/**
 * Checks if the user is authenticated with GitHub
 */
export async function checkAuthStatus() {
  const response = await fetch(`${ENGINE_URL}/auth/status`);
  if (!response.ok) return { authenticated: false };
  return await response.json();
}

/**
 * Initiates the GitHub OAuth login flow
 */
export function loginWithGitHub() {
  window.open(`${ENGINE_URL}/auth/login`, "_blank", "width=600,height=700");
}
