/**
 * Shared API client configuration.
 */

export const ENGINE_URL =
  typeof window !== "undefined" &&
  window.location.hostname !== "localhost" &&
  window.location.hostname !== "127.0.0.1"
    ? `${window.location.protocol}//${window.location.hostname}:8002`
    : "http://127.0.0.1:8002";

/**
 * Checks if the engine is online
 */
export async function checkHealth() {
  try {
    const response = await fetch(`${ENGINE_URL}/health`);
    return response.ok;
  } catch (e) {
    return false;
  }
}
