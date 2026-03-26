"""
Changeset: Tracks all file changes made by an agent task for review and rollback.

Each agent task creates one Changeset. Before any file mutation, the original
content is snapshotted. The user can accept or rollback changes individually
or as a batch.
"""

import os
import uuid
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class FileChange:
    """Record of a single file mutation."""
    path: str                          # absolute path
    rel_path: str                      # relative to workspace
    action: str                        # "modified" | "created" | "deleted"
    original_content: Optional[str]    # None for created files
    new_content: Optional[str]         # None for deleted files
    original_hash: Optional[str]       # SHA-256 of original content
    step_id: Optional[int] = None      # which plan step made this change

    @property
    def diff_summary(self) -> str:
        """Quick summary of the change."""
        if self.action == "created":
            lines = len((self.new_content or "").splitlines())
            return f"+ {self.rel_path} ({lines} lines)"
        if self.action == "deleted":
            return f"- {self.rel_path}"
        # modified
        old_lines = len((self.original_content or "").splitlines())
        new_lines = len((self.new_content or "").splitlines())
        delta = new_lines - old_lines
        return f"~ {self.rel_path} ({delta:+d} lines)"


@dataclass
class Changeset:
    """
    Tracks all file changes for a single agent task.
    Supports per-file and bulk rollback.
    """
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    workspace_path: str = ""
    goal: str = ""
    changes: List[FileChange] = field(default_factory=list)
    commands_run: List[Dict] = field(default_factory=list)  # [{command, output, step_id}]
    status: str = "active"  # active | accepted | rolled_back

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def snapshot_before_write(self, abs_path: str, rel_path: str, step_id: Optional[int] = None):
        """Snapshot a file before it gets written. Call this before every file mutation."""
        # Don't double-snapshot the same file
        for change in self.changes:
            if change.path == abs_path:
                return  # already tracked

        original_content = None
        original_hash = None
        action = "created"

        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    original_content = f.read()
                original_hash = self._hash(original_content)
                action = "modified"
            except Exception:
                pass

        self.changes.append(FileChange(
            path=abs_path,
            rel_path=rel_path,
            action=action,
            original_content=original_content,
            new_content=None,  # filled after write
            original_hash=original_hash,
            step_id=step_id,
        ))

    def record_write(self, abs_path: str, new_content: str):
        """Record what was written to a file (call after write)."""
        for change in self.changes:
            if change.path == abs_path:
                change.new_content = new_content
                return

    def record_delete(self, abs_path: str, rel_path: str, original_content: str, step_id: Optional[int] = None):
        """Record a file deletion."""
        self.changes.append(FileChange(
            path=abs_path,
            rel_path=rel_path,
            action="deleted",
            original_content=original_content,
            new_content=None,
            original_hash=self._hash(original_content),
            step_id=step_id,
        ))

    def record_command(self, command: str, output: str, step_id: Optional[int] = None):
        """Record a terminal command execution."""
        self.commands_run.append({
            "command": command,
            "output": output[:2000],
            "step_id": step_id,
        })

    def check_conflict(self, abs_path: str) -> bool:
        """
        Check if a file has been modified since we snapshotted it.
        Returns True if there's a conflict (file changed externally).
        """
        for change in self.changes:
            if change.path == abs_path and change.original_hash:
                try:
                    with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                        current = f.read()
                    return self._hash(current) != change.original_hash
                except Exception:
                    return True
        return False

    def rollback_file(self, abs_path: str) -> str:
        """Rollback a single file to its original state."""
        for change in self.changes:
            if change.path != abs_path:
                continue

            if change.action == "created":
                # Delete the file that was created
                if os.path.exists(abs_path):
                    os.remove(abs_path)
                    # Also remove .bak if it exists
                    bak = abs_path + ".bak"
                    if os.path.exists(bak):
                        os.remove(bak)
                change.status = "rolled_back" if hasattr(change, "status") else None
                return f"[Rollback] Deleted created file: {change.rel_path}"

            if change.action == "modified" and change.original_content is not None:
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(change.original_content)
                return f"[Rollback] Restored: {change.rel_path}"

            if change.action == "deleted" and change.original_content is not None:
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(change.original_content)
                return f"[Rollback] Recreated deleted file: {change.rel_path}"

        return f"[Error] No tracked change for: {abs_path}"

    def rollback_all(self) -> List[str]:
        """Rollback all changes in reverse order."""
        results = []
        for change in reversed(self.changes):
            result = self.rollback_file(change.path)
            results.append(result)
        self.status = "rolled_back"
        return results

    def accept(self):
        """Accept all changes — clean up .bak files."""
        for change in self.changes:
            bak = change.path + ".bak"
            if os.path.exists(bak):
                try:
                    os.remove(bak)
                except Exception:
                    pass
        self.status = "accepted"

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "files": [
                {
                    "path": c.rel_path,
                    "action": c.action,
                    "diff_summary": c.diff_summary,
                    "step_id": c.step_id,
                }
                for c in self.changes
            ],
            "commands_run": len(self.commands_run),
        }

    def summary(self) -> str:
        """Human-readable summary."""
        lines = [f"Changeset {self.id}: {self.goal}"]
        for c in self.changes:
            lines.append(f"  {c.diff_summary}")
        if self.commands_run:
            lines.append(f"  Commands run: {len(self.commands_run)}")
        return "\n".join(lines)
