import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  console.warn(
    "[Supabase] VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY are not set. " +
      "Copy app/.env.example to app/.env and fill in your project credentials."
  );
}

export const supabase = supabaseUrl && supabaseAnonKey
  ? createClient(supabaseUrl, supabaseAnonKey)
  : null;

/**
 * Saves (or updates last_opened for) a workspace entry for the current user.
 * Safe to call on every init — uses upsert on the unique(user_id, path) constraint.
 * @param {string} path  - local path or GitHub URL
 * @param {boolean} isGitHub
 */
export async function saveWorkspace(path, isGitHub) {
  if (!supabase) return;
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return;

  const name = path.split(/[\\/]/).filter(Boolean).pop() ?? path;

  await supabase.from("workspaces").upsert(
    { user_id: user.id, path, name, is_github: isGitHub, last_opened: new Date().toISOString() },
    { onConflict: "user_id,path" }
  );
}

/**
 * Returns the user's recent workspaces, newest first (max 10).
 * @returns {Promise<Array<{id, name, path, is_github, last_opened}>>}
 */
export async function getRecentWorkspaces() {
  if (!supabase) return [];
  const { data, error } = await supabase
    .from("workspaces")
    .select("id, name, path, is_github, last_opened")
    .order("last_opened", { ascending: false })
    .limit(10);

  if (error) {
    console.error("[Supabase] getRecentWorkspaces:", error.message);
    return [];
  }
  return data ?? [];
}

/**
 * Deletes a workspace entry by id.
 * @param {string} id
 */
export async function deleteWorkspace(id) {
  if (!supabase) return;
  await supabase.from("workspaces").delete().eq("id", id);
}
