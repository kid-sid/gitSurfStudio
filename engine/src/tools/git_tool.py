import os
from typing import List, Dict, Optional
from git import Repo, GitCommandError, InvalidGitRepositoryError
from src.logger import get_logger

logger = get_logger("git_tool")


class GitTool:
    """
    A tool to handle local Git operations within the workspace.
    Uses GitPython to avoid subprocess shell-injection risks.
    """

    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)
        try:
            self.repo = Repo(self.root_path)
        except InvalidGitRepositoryError:
            raise Exception(f"[GitTool] Not a git repository: {self.root_path}")

    def get_status(self) -> List[Dict[str, str]]:
        """Returns the current git status (porcelain-style)."""
        try:
            changes = []
            for item in self.repo.index.diff(None):  # unstaged
                changes.append({"status": " M", "path": item.a_path})
            for item in self.repo.index.diff("HEAD"):  # staged
                changes.append({"status": "M ", "path": item.a_path})
            for item in self.repo.untracked_files:
                changes.append({"status": "??", "path": item})
            return changes
        except Exception as e:
            logger.error("Status error: %s", e)
            return []

    def stage_files(self, files: List[str]) -> str:
        """Stages files (git add)."""
        try:
            self.repo.index.add(files)
            return f"[Success] Staged {len(files)} files."
        except GitCommandError as e:
            return f"[Error] Failed to stage files: {e}"

    def commit(self, message: str) -> str:
        """Commits staged changes."""
        try:
            self.repo.index.commit(message)
            return "[Success] Changes committed."
        except GitCommandError as e:
            return f"[Error] Failed to commit: {e}"

    def get_diff(self, file_path: Optional[str] = None) -> str:
        """Returns the unified diff for a file or the whole repo (unstaged + staged)."""
        try:
            parts: List[str] = []
            paths = [file_path] if file_path else []

            # Unstaged: working tree vs index
            for d in self.repo.index.diff(None, paths=paths or None, create_patch=True):
                if d.diff:
                    parts.append(d.diff.decode("utf-8", errors="replace"))

            # Staged: index vs HEAD (only when HEAD exists)
            if self.repo.head.is_valid():
                for d in self.repo.index.diff("HEAD", paths=paths or None, create_patch=True):
                    if d.diff:
                        parts.append(d.diff.decode("utf-8", errors="replace"))

            return "\n".join(parts)
        except GitCommandError as e:
            return f"[Error] Failed to get diff: {e}"

    def get_branches(self) -> Dict:
        """Returns the current branch and all available branch names."""
        try:
            branches = set()
            current = None

            for branch in self.repo.branches:
                branches.add(branch.name)
                if branch == self.repo.active_branch:
                    current = branch.name

            # Include remote branches
            for ref in self.repo.remotes["origin"].refs if self.repo.remotes else []:
                name = ref.name.replace("origin/", "", 1)
                if name != "HEAD":
                    branches.add(name)

            return {"current": current, "branches": sorted(list(branches))}
        except Exception as e:
            logger.error("Branches error: %s", e)
            return {"current": None, "branches": []}

    def checkout_branch(self, branch_name: str) -> str:
        """Checks out a branch, handling local and remote cases."""
        # 1. Existing local branch
        local = next((b for b in self.repo.branches if b.name == branch_name), None)
        if local:
            local.checkout()
            return f"Switched to branch '{branch_name}'"

        # 2. Fetch from remote and create a tracking branch
        origin = next((r for r in self.repo.remotes if r.name == "origin"), None)
        if origin:
            try:
                origin.fetch(refspec=branch_name)
            except GitCommandError:
                pass  # branch may already be fetched
            remote_ref = next(
                (ref for ref in origin.refs if ref.remote_head == branch_name), None
            )
            if remote_ref:
                local = self.repo.create_head(branch_name, remote_ref)
                local.set_tracking_branch(remote_ref)
                local.checkout()
                return f"Switched to and tracking remote branch 'origin/{branch_name}'"

        raise Exception(f"Branch '{branch_name}' not found locally or on remote 'origin'")

    def stash_changes(self) -> str:
        """Stashes local changes.
        Note: GitPython has no native stash API; repo.git.stash() is used here
        but takes no user input so carries no injection risk.
        """
        self.repo.git.stash()
        return "Changes stashed successfully"

    def pop_stash(self) -> str:
        """Pops the latest stash."""
        self.repo.git.stash("pop")
        return "Stash popped successfully"

    def discard_changes(self, file_path: str) -> str:
        """Discards local changes to a specific file."""
        status = self.get_status()
        is_untracked = any(
            c["path"] == file_path and c["status"].strip() == "??" for c in status
        )

        if is_untracked:
            full_path = os.path.join(self.root_path, file_path)
            if os.path.exists(full_path):
                os.remove(full_path)
            return f"Deleted untracked file {file_path}"
        else:
            self.repo.index.checkout([file_path], force=True)
            return f"Discarded changes for {file_path}"
