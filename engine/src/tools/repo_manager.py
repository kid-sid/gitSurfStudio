import os
import subprocess
import shutil
import re
import requests

class RepoManager:
    SKIP_DIRS: set[str] = {
        'node_modules', '.git', '.cache', '__pycache__', 'venv', '.venv',
        'dist', 'build', 'target', 'bin', 'obj', 'vendor', '.idea', '.vscode'
    }

    def __init__(self, cache_dir: str = ".cache"):
        self.cache_dir = os.path.abspath(cache_dir)
        if not os.path.exists(self.cache_dir):
            os.makedirs(self.cache_dir)

    def _get_repo_name(self, repo_input: str) -> str:
        """Extracts 'owner/repo' string from a name or full GitHub URL."""
        # Handle full URLs: https://github.com/owner/repo(.git)? (/tree/main)?
        repo_input = repo_input.strip()
        if repo_input.startswith(("http://", "https://", "git@")):
            # Remove protocol and github.com/
            name = re.sub(r'^(https?://github\.com/|git@github\.com:)', '', repo_input)
        else:
            name = repo_input

        # Remove /tree/... if present
        if "/tree/" in name:
            name = name.split("/tree/")[0]
        
        # Remove .git suffix
        if name.endswith(".git"):
            name = name[:-4]
            
        # Clean up any trailing slashes
        return name.strip("/")

    def _generate_local_tree(self, root_path: str) -> str:
        """Generates an ASCII tree for the local repository."""
        lines = []
        def walk(path, prefix=""):
            try:
                # Filter out skipped directories early
                items = sorted([
                    i for i in os.listdir(path) 
                    if i not in self.SKIP_DIRS
                ])
            except PermissionError:
                return
                
            for i, name in enumerate(items):
                full_path = os.path.join(path, name)
                is_last = (i == len(items) - 1)
                connector = "└── " if is_last else "├── "
                lines.append(f"{prefix}{connector}{name}")
                
                if os.path.isdir(full_path):
                    new_prefix = prefix + ("    " if is_last else "│   ")
                    walk(full_path, new_prefix)

        walk(root_path)
        return "\n".join(lines)

    def sync_repo(self, repo_input: str) -> str:
        """
        Clones or updates the given repo (e.g., "owner/repo" or full URL) in the cache directory.
        Returns the absolute path to the local copy.
        """
        repo_name = self._get_repo_name(repo_input)
        
        # Create a filesystem-safe name for the directory
        safe_name = repo_name.replace("/", "_").replace("\\", "_")
        repo_path = os.path.join(self.cache_dir, safe_name)
        
        repo_url = f"https://github.com/{repo_name}.git"

        if os.path.exists(repo_path):
            if os.path.exists(os.path.join(repo_path, ".git")):
                print(f"[RepoManager] Updating existing repo: {repo_name}...")
                try:
                    subprocess.run(["git", "pull"], cwd=repo_path, check=True, capture_output=True)
                except subprocess.CalledProcessError as e:
                    print(f"[RepoManager] Warning: Failed to pull '{repo_name}'. Error: {e.stderr.decode() if e.stderr else str(e)}")
            else:
                # If path exists but isn't a git repo, wipe and re-clone
                print(f"[RepoManager] {repo_path} exists but is not a Git repo. Cleaning...")
                shutil.rmtree(repo_path)
                self._clone(repo_url, repo_path)
        else:
            self._clone(repo_url, repo_path)
            
        # Generate and save project structure
        tree_str = self._generate_local_tree(repo_path)
        structure_path = os.path.join(repo_path, "project_structure.txt")
        try:
            with open(structure_path, "w", encoding='utf-8') as f:
                f.write(tree_str)
            print(f"[RepoManager] Generated project structure at: {structure_path}")
        except Exception as e:
            print(f"[RepoManager] Error writing project structure: {e}")
            
        return repo_path

    def _clone(self, url: str, path: str):
        """Perform a shallow clone of the repository."""
        try:
            subprocess.run(["git", "clone", "--depth", "1", "--no-single-branch", url, path], check=True)
            print(f"[RepoManager] Cloned {url} to {path}.")
        except subprocess.CalledProcessError as e:
            print(f"[RepoManager] Error cloning {url}: {e}")
            raise

    def fork_repo(self, repo_input: str, token: str) -> str:
        """
        Forks the given repo using the GitHub API.
        Returns the HTML URL of the new fork.
        """
        repo_name = self._get_repo_name(repo_input)
            
        url = f"https://api.github.com/repos/{repo_name}/forks"
        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json"
        }
        
        print(f"[RepoManager] Sending fork request for: {repo_name}")
        response = requests.post(url, headers=headers)
        
        if response.status_code in [202, 201]:
            fork_url = response.json().get("html_url")
            print(f"[RepoManager] Fork initiated: {fork_url}")
            return fork_url
        else:
            error_data = response.json()
            message = error_data.get("message", "Unknown error")
            raise Exception(f"GitHub fork failed: {message}")


class CacheManager:
    """Manages lifecycle of the engine/.cache/ directory."""

    INDEX_DIRS = {"vector_index", "bm25_index", "symbols", "call_graph"}
    MAX_CACHED_REPOS = int(os.environ.get("GITSURF_CACHE_MAX_REPOS", "3"))

    def __init__(self, cache_dir: str):
        self.cache_dir = os.path.abspath(cache_dir)

    def _dir_size_bytes(self, path: str) -> int:
        import contextlib
        total = 0
        for dirpath, _dirnames, filenames in os.walk(path):
            for f in filenames:
                with contextlib.suppress(OSError):
                    total += os.path.getsize(os.path.join(dirpath, f))
        return total

    def list_cached_repos(self) -> list[dict]:
        """List cloned repo directories (those containing .git/), sorted by mtime desc."""
        repos = []
        if not os.path.isdir(self.cache_dir):
            return repos
        for name in os.listdir(self.cache_dir):
            full = os.path.join(self.cache_dir, name)
            if os.path.isdir(full) and name not in self.INDEX_DIRS and os.path.isdir(os.path.join(full, ".git")):
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    mtime = 0.0
                repos.append({"name": name, "path": full, "mtime": mtime})
        repos.sort(key=lambda r: r["mtime"], reverse=True)
        return repos

    def evict_old_repos(self, keep: int | None = None, exclude: str | None = None):
        """Delete oldest cloned repos beyond the keep limit.

        Args:
            keep: Number of repos to retain (default: MAX_CACHED_REPOS).
            exclude: safe_name of a repo that must never be deleted.
        """
        if keep is None:
            keep = self.MAX_CACHED_REPOS
        repos = self.list_cached_repos()
        to_keep = repos[:keep]
        to_delete = repos[keep:]
        kept_names = {r["name"] for r in to_keep}
        if exclude:
            kept_names.add(exclude)
        for repo in to_delete:
            if repo["name"] in kept_names:
                continue
            print(f"[CacheManager] Evicting old repo: {repo['name']}")
            shutil.rmtree(repo["path"], ignore_errors=True)

    def cleanup_search_indexes(self):
        """Delete all search index subdirectories (rebuilt lazily on next query)."""
        for name in self.INDEX_DIRS:
            idx_path = os.path.join(self.cache_dir, name)
            if os.path.isdir(idx_path):
                shutil.rmtree(idx_path, ignore_errors=True)

    def get_cache_stats(self) -> dict:
        """Return cache size, repo count, and per-index sizes."""
        repos = self.list_cached_repos()
        repo_list = []
        for r in repos:
            size_mb = round(self._dir_size_bytes(r["path"]) / (1024 * 1024), 2)
            repo_list.append({
                "name": r["name"],
                "size_mb": size_mb,
                "last_used": r["mtime"],
            })

        indexes = {}
        for name in self.INDEX_DIRS:
            idx_path = os.path.join(self.cache_dir, name)
            if os.path.isdir(idx_path):
                indexes[name] = round(self._dir_size_bytes(idx_path) / (1024 * 1024), 2)
            else:
                indexes[name] = 0.0

        total = sum(r["size_mb"] for r in repo_list) + sum(indexes.values())
        return {
            "total_size_mb": round(total, 2),
            "repo_count": len(repo_list),
            "repos": repo_list,
            "indexes": indexes,
        }

    def purge_all(self, exclude_active: str | None = None):
        """Delete everything in .cache/ except the active workspace directory."""
        if not os.path.isdir(self.cache_dir):
            return
        for name in os.listdir(self.cache_dir):
            if exclude_active and name == exclude_active:
                continue
            full = os.path.join(self.cache_dir, name)
            if os.path.isdir(full):
                shutil.rmtree(full, ignore_errors=True)
            else:
                import contextlib
                with contextlib.suppress(OSError):
                    os.remove(full)

