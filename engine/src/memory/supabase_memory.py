"""
SupabaseMemory — Persists symbol graphs and project structure in Supabase.

Data is keyed by (user_id, repo_identifier):
  - repo_identifier: "owner/repo" for GitHub repos, or a hash of the local path.
  - Sync: On /init, compare stored last_commit_sha with current HEAD.
    If they match, load from Supabase (skip local rebuild).
    If they differ (or no record), rebuild locally then save in background.

All writes are fire-and-forget background threads so they never block the pipeline.
"""

import os
import json
import hashlib
import subprocess
import threading
import zlib
import base64
from typing import Optional, Dict, Any

import requests

from src.logger import get_logger

logger = get_logger("supabase_memory")

_SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")


def _headers() -> Dict[str, str]:
    return {
        "apikey": _SERVICE_KEY,
        "Authorization": f"Bearer {_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _compress(data: dict) -> str:
    """Compress JSON dict to base64-encoded zlib string for large payloads."""
    raw = json.dumps(data).encode("utf-8")
    compressed = zlib.compress(raw, level=6)
    return base64.b64encode(compressed).decode("ascii")


def _decompress(s: str) -> dict:
    """Decompress base64-encoded zlib string back to dict."""
    compressed = base64.b64decode(s.encode("ascii"))
    raw = zlib.decompress(compressed)
    return json.loads(raw.decode("utf-8"))


def _is_compressed(v: Any) -> bool:
    """Check if a value is our compressed sentinel string."""
    return isinstance(v, str) and v.startswith("z:")


class SupabaseMemory:
    """Reads/writes symbol graph and project structure to Supabase."""

    def __init__(self):
        self._available = bool(_SUPABASE_URL and _SERVICE_KEY)
        if not self._available:
            logger.debug("SupabaseMemory: SUPABASE_URL/SERVICE_KEY not set — operating without persistence")

    # ── Repo identifier helpers ────────────────────────────────────────────────

    @staticmethod
    def make_repo_identifier(workspace_path: str, is_github: bool = False) -> str:
        """
        Canonical key for a workspace:
          GitHub repos → normalized 'owner/repo'
          Local paths  → 'local:<sha8>' where sha8 = first 8 chars of SHA256(abs_path)
        """
        if is_github:
            # Workspace path is the local clone dir — extract from path or accept as-is
            return workspace_path  # caller passes owner/repo for GitHub
        abs_path = os.path.abspath(workspace_path)
        sha8 = hashlib.sha256(abs_path.encode()).hexdigest()[:8]
        folder = os.path.basename(abs_path)
        return f"local:{sha8}:{folder}"

    @staticmethod
    def get_head_sha(workspace_path: str) -> Optional[str]:
        """Returns the current HEAD commit SHA, or None if not a git repo."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True,
                cwd=workspace_path, timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return None

    # ── Snapshot CRUD ──────────────────────────────────────────────────────────

    def get_snapshot(self, user_id: str, repo_identifier: str) -> Optional[Dict]:
        """Fetch repo_snapshot row for this user+repo, or None."""
        if not self._available:
            return None
        url = f"{_SUPABASE_URL}/rest/v1/repo_snapshots"
        params = {
            "user_id": f"eq.{user_id}",
            "repo_identifier": f"eq.{repo_identifier}",
            "select": "id,last_commit_sha,file_structure",
            "limit": "1",
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=5)
            if resp.ok:
                data = resp.json()
                return data[0] if data else None
        except Exception as e:
            logger.warning("SupabaseMemory.get_snapshot error: %s", e)
        return None

    def needs_reindex(self, user_id: str, repo_identifier: str, current_sha: Optional[str]) -> bool:
        """
        Returns True if we should rebuild the symbol index.
        False = Supabase has a fresh snapshot (same commit SHA).
        """
        if not self._available or not current_sha:
            return True
        snapshot = self.get_snapshot(user_id, repo_identifier)
        if not snapshot:
            return True
        return snapshot.get("last_commit_sha") != current_sha

    def get_symbol_graph(self, snapshot_id: str) -> Optional[Dict]:
        """Load the symbol graph entry for a snapshot, or None."""
        if not self._available:
            return None
        url = f"{_SUPABASE_URL}/rest/v1/symbol_graphs"
        params = {
            "repo_snapshot_id": f"eq.{snapshot_id}",
            "select": "symbols,call_graph",
            "limit": "1",
        }
        try:
            resp = requests.get(url, headers=_headers(), params=params, timeout=10)
            if resp.ok:
                data = resp.json()
                if data:
                    row = data[0]
                    # Decompress if needed
                    symbols = row.get("symbols")
                    cg = row.get("call_graph")
                    if _is_compressed(symbols):
                        symbols = _decompress(symbols[2:])
                    if _is_compressed(cg):
                        cg = _decompress(cg[2:])
                    return {"symbols": symbols, "call_graph": cg}
        except Exception as e:
            logger.warning("SupabaseMemory.get_symbol_graph error: %s", e)
        return None

    # ── Background save helpers ────────────────────────────────────────────────

    def save_snapshot_bg(
        self,
        user_id: str,
        repo_identifier: str,
        repo_display: str,
        commit_sha: str,
        file_structure: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Upsert a repo_snapshot row synchronously (called in background thread).
        Returns the snapshot ID if successful.
        """
        if not self._available:
            return None
        url = f"{_SUPABASE_URL}/rest/v1/repo_snapshots"
        payload = {
            "user_id": user_id,
            "repo_identifier": repo_identifier,
            "repo_display": repo_display,
            "last_commit_sha": commit_sha,
            "last_indexed_at": "now()",
        }
        if file_structure is not None:
            payload["file_structure"] = file_structure

        headers = dict(_headers())
        headers["Prefer"] = "return=representation,resolution=merge-duplicates"

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=10)
            if resp.ok:
                data = resp.json()
                return data[0]["id"] if data else None
            logger.warning("save_snapshot failed (%s): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("SupabaseMemory.save_snapshot error: %s", e)
        return None

    def save_symbol_graph_bg(
        self,
        snapshot_id: str,
        symbols: dict,
        call_graph: Optional[dict] = None,
    ) -> None:
        """Upsert symbol_graphs row (called in background thread)."""
        if not self._available or not snapshot_id:
            return

        # Compress large payloads (>512 KB)
        sym_json = json.dumps(symbols)
        sym_val = ("z:" + _compress(symbols)) if len(sym_json) > 512_000 else symbols

        cg_val = None
        if call_graph:
            cg_json = json.dumps(call_graph)
            cg_val = ("z:" + _compress(call_graph)) if len(cg_json) > 512_000 else call_graph

        url = f"{_SUPABASE_URL}/rest/v1/symbol_graphs"
        headers = dict(_headers())
        headers["Prefer"] = "return=minimal,resolution=merge-duplicates"
        payload: Dict[str, Any] = {"repo_snapshot_id": snapshot_id, "symbols": sym_val}
        if cg_val is not None:
            payload["call_graph"] = cg_val

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=15)
            if not resp.ok:
                logger.warning("save_symbol_graph failed (%s): %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("SupabaseMemory.save_symbol_graph error: %s", e)

    # ── Public API (fire-and-forget) ───────────────────────────────────────────

    def schedule_save(
        self,
        user_id: str,
        repo_identifier: str,
        repo_display: str,
        commit_sha: str,
        symbols: dict,
        call_graph: Optional[dict] = None,
        file_structure: Optional[dict] = None,
    ) -> None:
        """Fire-and-forget: save snapshot + symbol graph to Supabase in a background thread."""
        if not self._available:
            return

        def _run():
            snapshot_id = self.save_snapshot_bg(
                user_id, repo_identifier, repo_display, commit_sha, file_structure
            )
            if snapshot_id:
                self.save_symbol_graph_bg(snapshot_id, symbols, call_graph)

        threading.Thread(target=_run, daemon=True).start()

    def load_and_inject_cache(
        self,
        user_id: str,
        repo_identifier: str,
        symbol_cache_dir: str,
        call_graph_cache_dir: str,
    ) -> bool:
        """
        If Supabase has a fresh snapshot, write the symbol and call-graph JSON
        to the local cache files so PipelineContext picks them up on lazy init.

        Returns True if cache was successfully injected, False otherwise.
        """
        if not self._available:
            return False

        snapshot = self.get_snapshot(user_id, repo_identifier)
        if not snapshot:
            return False

        graph_data = self.get_symbol_graph(snapshot["id"])
        if not graph_data or not graph_data.get("symbols"):
            return False

        try:
            os.makedirs(symbol_cache_dir, exist_ok=True)
            sym_path = os.path.join(symbol_cache_dir, "symbol_index.json")
            with open(sym_path, "w", encoding="utf-8") as f:
                json.dump(graph_data["symbols"], f)
            logger.info("SupabaseMemory: injected symbol cache (%d files)", len(graph_data["symbols"]))

            if graph_data.get("call_graph"):
                os.makedirs(call_graph_cache_dir, exist_ok=True)
                cg_path = os.path.join(call_graph_cache_dir, "call_graph.json")
                with open(cg_path, "w", encoding="utf-8") as f:
                    json.dump(graph_data["call_graph"], f)
                logger.info("SupabaseMemory: injected call graph cache")

            return True
        except Exception as e:
            logger.warning("SupabaseMemory.load_and_inject_cache error: %s", e)
            return False
