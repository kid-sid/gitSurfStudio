"""
LintTool — Real-time code linting via ruff (Python) and eslint (JS/TS/JSX/TSX).

Pipes editor content directly to the linter's stdin so no temp files are needed.
Results are content-hash cached to avoid re-running on unchanged content.
"""

import os
import json
import hashlib
import subprocess
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict

from src.logger import get_logger

logger = get_logger("lint_tool")


@dataclass
class LintDiagnostic:
    line: int
    column: int
    end_line: Optional[int]
    end_column: Optional[int]
    severity: str        # "error" | "warning"
    message: str
    code: Optional[str]  # e.g. "E501", "no-unused-vars"
    source: str          # "ruff" | "eslint"

    def to_dict(self) -> Dict:
        return asdict(self)


# Ruff exit codes: 0 = no issues, 1 = issues found, 2 = error
_RUFF_ERROR_CODES = {"E9", "F821", "F823", "F811", "F401", "SyntaxError"}


def _ruff_severity(code: Optional[str]) -> str:
    """Map ruff rule code to error/warning."""
    if not code:
        return "warning"
    # E9xx = syntax / runtime errors; treat as errors
    if code.startswith("E9") or code.startswith("F8"):
        return "error"
    return "warning"


class LintTool:
    """Runs real-time linting for Python (ruff) and JS/TS (eslint)."""

    SUPPORTED_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx"}

    def __init__(self):
        self._cache: Dict[str, List[Dict]] = {}

    def lint_content(self, content: str, file_path: str, workspace: str = "") -> List[Dict]:
        """
        Lint file content in memory.

        Args:
            content: Current editor content (may be unsaved).
            file_path: Relative or absolute path — used for language detection and
                       as the stdin-filename so lint rules know what file they're checking.
            workspace: Workspace root directory (used as cwd for eslint).

        Returns:
            List of LintDiagnostic dicts.
        """
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in self.SUPPORTED_EXTENSIONS:
            return []

        # Content-hash cache to avoid re-linting identical content
        cache_key = hashlib.md5(f"{file_path}:{content}".encode()).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        if ext == ".py":
            results = self._run_ruff(content, file_path)
        elif ext in {".js", ".ts", ".jsx", ".tsx"}:
            results = self._run_eslint(content, file_path, workspace)
        else:
            results = []

        # Keep cache bounded at 100 entries (FIFO eviction)
        self._cache[cache_key] = results
        if len(self._cache) > 100:
            oldest = next(iter(self._cache))
            del self._cache[oldest]

        return results

    # ── Python (ruff) ──────────────────────────────────────────────────────────

    def _run_ruff(self, content: str, file_path: str) -> List[Dict]:
        try:
            result = subprocess.run(
                [
                    "ruff", "check",
                    "--output-format", "json",
                    "--stdin-filename", file_path,
                    "-",                          # read from stdin
                ],
                input=content,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError:
            logger.debug("ruff not found — skipping Python lint")
            return []
        except subprocess.TimeoutExpired:
            logger.debug("ruff timed out on %s", file_path)
            return []

        if not result.stdout:
            return []

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        diagnostics = []
        for item in raw:
            loc = item.get("location", {})
            end_loc = item.get("end_location", {})
            code = item.get("code")
            diagnostics.append(LintDiagnostic(
                line=loc.get("row", 1),
                column=loc.get("column", 1),
                end_line=end_loc.get("row"),
                end_column=end_loc.get("column"),
                severity=_ruff_severity(code),
                message=item.get("message", ""),
                code=code,
                source="ruff",
            ).to_dict())

        return diagnostics

    # ── JavaScript / TypeScript (eslint) ──────────────────────────────────────

    def _run_eslint(self, content: str, file_path: str, workspace: str) -> List[Dict]:
        cwd = workspace if workspace and os.path.isdir(workspace) else None

        try:
            result = subprocess.run(
                [
                    "npx", "--no-install", "eslint",
                    "--stdin",
                    "--stdin-filename", file_path,
                    "--format", "json",
                ],
                input=content,
                capture_output=True,
                text=True,
                timeout=10,
                cwd=cwd,
            )
        except FileNotFoundError:
            logger.debug("npx/eslint not found — skipping JS lint")
            return []
        except subprocess.TimeoutExpired:
            logger.debug("eslint timed out on %s", file_path)
            return []

        if not result.stdout:
            return []

        try:
            raw = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        diagnostics = []
        for file_result in raw:
            for msg in file_result.get("messages", []):
                sev = msg.get("severity", 1)
                diagnostics.append(LintDiagnostic(
                    line=msg.get("line", 1),
                    column=msg.get("column", 1),
                    end_line=msg.get("endLine"),
                    end_column=msg.get("endColumn"),
                    severity="error" if sev == 2 else "warning",
                    message=msg.get("message", ""),
                    code=msg.get("ruleId"),
                    source="eslint",
                ).to_dict())

        return diagnostics
