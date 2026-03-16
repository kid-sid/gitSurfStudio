import subprocess
import os
from typing import List, Dict, Optional

class GitTool:
    """
    A tool to handle local Git operations within the workspace.
    """
    def __init__(self, root_path: str):
        self.root_path = os.path.abspath(root_path)

    def _run_git(self, args: List[str]) -> str:
        """Runs a git command and returns the output."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.root_path,
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            raise Exception(f"Git command failed: {e.stderr.strip() or e.stdout.strip()}")

    def get_status(self) -> List[Dict[str, str]]:
        """
        Returns the current git status --porcelain.
        """
        try:
            output = self._run_git(["status", "--porcelain"])
            if not output:
                return []
            
            changes = []
            for line in output.splitlines():
                if len(line) < 4:
                    continue
                # status --porcelain format: XY path
                status_code = line[:2]
                file_path = line[3:]
                changes.append({
                    "status": status_code,
                    "path": file_path
                })
            return changes
        except Exception as e:
            print(f"[GitTool] Status error: {e}")
            return []

    def stage_files(self, files: List[str]) -> str:
        """Stages files (git add)."""
        try:
            self._run_git(["add"] + files)
            return f"[Success] Staged {len(files)} files."
        except Exception as e:
            return f"[Error] Failed to stage files: {e}"

    def commit(self, message: str) -> str:
        """Commits changes (git commit -m)."""
        try:
            self._run_git(["commit", "-m", message])
            return "[Success] Changes committed."
        except Exception as e:
            return f"[Error] Failed to commit: {e}"

    def get_diff(self, file_path: Optional[str] = None) -> str:
        """Returns the git diff for a file or the whole repo."""
        args = ["diff"]
        if file_path:
            args.append(file_path)
        try:
            return self._run_git(args)
        except Exception as e:
            return f"[Error] Failed to get diff: {e}"
