"""Cache management routes: status, cleanup, purge."""

import os

from fastapi import APIRouter, Request

from src.engine_state import state
from src.models import CacheCleanupResponse, CacheStatusResponse
from src.routes import limiter, logger

router = APIRouter(prefix="/cache")


def _active_safe_name() -> str | None:
    """Return the safe_name of the currently active workspace if it lives inside .cache."""
    wp = state.workspace_path
    if not wp:
        return None
    cache_dir = state.cache_manager.cache_dir
    if os.path.commonpath([cache_dir, wp]) == cache_dir:
        return os.path.basename(wp)
    return None


@router.get("/status", response_model=CacheStatusResponse)
async def cache_status():
    """Return cache size, repo list, and per-index sizes."""
    return state.cache_manager.get_cache_stats()


@router.post("/cleanup", response_model=CacheCleanupResponse)
@limiter.limit("10/minute")
async def cache_cleanup(request: Request):
    """Evict old repos beyond LRU limit and delete stale search indexes."""
    stats_before = state.cache_manager.get_cache_stats()
    size_before = stats_before["total_size_mb"]
    repos_before = stats_before["repo_count"]

    exclude = _active_safe_name()

    # Skip index cleanup if an agent pipeline is actively running
    if state.active_executor is None:
        state.cache_manager.cleanup_search_indexes()

    state.cache_manager.evict_old_repos(exclude=exclude)

    stats_after = state.cache_manager.get_cache_stats()
    freed = round(size_before - stats_after["total_size_mb"], 2)
    removed = repos_before - stats_after["repo_count"]

    logger.info("[CacheManager] Cleanup: removed %d repos, freed %.2f MB", removed, freed)
    return {"status": "ok", "repos_removed": removed, "space_freed_mb": max(freed, 0.0)}


@router.delete("", response_model=CacheCleanupResponse)
@limiter.limit("2/minute")
async def cache_purge(request: Request):
    """Purge all cached data except the active workspace."""
    stats_before = state.cache_manager.get_cache_stats()
    size_before = stats_before["total_size_mb"]
    repos_before = stats_before["repo_count"]

    exclude = _active_safe_name()
    state.cache_manager.purge_all(exclude_active=exclude)

    stats_after = state.cache_manager.get_cache_stats()
    freed = round(size_before - stats_after["total_size_mb"], 2)
    removed = repos_before - stats_after["repo_count"]

    logger.info("[CacheManager] Purge: removed %d repos, freed %.2f MB", removed, freed)
    return {"status": "ok", "repos_removed": removed, "space_freed_mb": max(freed, 0.0)}
