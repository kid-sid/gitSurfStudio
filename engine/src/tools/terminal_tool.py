"""
TerminalTool: Safe shell command execution for the coding agent.

Runs commands in a subprocess with allowlist/blocklist enforcement,
timeout handling, and output truncation.
"""

import os
import re
import subprocess
import sys

# Commands that can run without user approval
SAFE_COMMANDS = {
    "pytest", "python", "python3", "ruff", "mypy", "flake8", "black", "isort",
    "npm", "npx", "node", "eslint", "prettier", "tsc",
    "cat", "ls", "dir", "find", "grep", "rg", "head", "tail", "wc",
    "echo", "pwd", "which", "where", "type",
    "pip", "pip3", "cargo", "go", "rustc", "javac",
    "uv", "virtualenv",
}

# Patterns that are always blocked
BLOCKED_PATTERNS = [
    r"\brm\s+-rf\b",
    r"\brm\s+-r\b",
    r"\bsudo\b",
    r"\bchmod\b",
    r"\bchown\b",
    r"\bcurl\b.*\|\s*(sh|bash)",
    r"\bwget\b.*\|\s*(sh|bash)",
    r"\bgit\s+push\b",
    r"\bgit\s+push\s+--force\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bdel\s+/[sS]\b",        # Windows recursive delete
    r"\bformat\s+[A-Z]:\b",    # Windows format drive
    r"\b(shutdown|reboot|halt|poweroff)\b",
    r"\bkill\s+-9\b",
    r"\bkillall\b",
    r"\bmkfs\b",
    r"\bdd\s+if=\b",
    r">\s*/dev/(sd|null|zero)",  # destructive redirects
]
_BLOCKED_RE = [re.compile(p, re.IGNORECASE) for p in BLOCKED_PATTERNS]

# Per-command-type timeout defaults (seconds)
TIMEOUTS = {
    "pytest": 60,
    "npm": 120,
    "npx": 120,
    "ruff": 15,
    "eslint": 30,
    "prettier": 15,
    "tsc": 60,
    "python": 60,
    "node": 60,
    "cargo": 120,
    "go": 60,
    "uv": 120,
    "virtualenv": 60,
}

MAX_OUTPUT_LINES = 200


class TerminalTool:
    """
    Safe shell command execution for the agent.
    Enforces allowlist/blocklist, timeouts, and output limits.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)

    def run_command(
        self,
        command: str,
        timeout_sec: int = 30,
        cwd: str | None = None,
    ) -> str:
        """
        Execute a shell command and return stdout+stderr.

        Args:
            command: The shell command to run
            timeout_sec: Max seconds before killing the process (default 30)
            cwd: Working directory (default: workspace root)

        Returns:
            Command output (stdout + stderr combined), truncated if too long
        """
        # ── Safety checks ─────────────────────────────────────────
        if not command or not command.strip():
            return "[Error] Empty command"

        for pattern in _BLOCKED_RE:
            if pattern.search(command):
                return f"[Error] Command blocked by safety policy: {command}"

        # Determine the base command for allowlist check
        # Strip surrounding quotes (Windows full-path commands like "C:\path\python.exe")
        # and the .exe suffix so "python.exe" matches "python" in the allowlist.
        raw_token = command.strip().split()[0].strip('"').strip("'")
        base_cmd = raw_token.split("/")[-1].split("\\")[-1]
        if base_cmd.lower().endswith(".exe"):
            base_cmd = base_cmd[:-4]

        if base_cmd not in SAFE_COMMANDS:
            return (
                f"[Error] Command '{base_cmd}' is not in the safe commands list. "
                f"Safe commands: {', '.join(sorted(SAFE_COMMANDS))}"
            )

        # Resolve working directory — use smart inference if no explicit cwd
        work_dir = cwd if cwd is not None else self._infer_cwd(base_cmd)
        if not os.path.isdir(work_dir):
            return f"[Error] Working directory does not exist: {work_dir}"

        # Use command-specific timeout if available
        cmd_timeout = TIMEOUTS.get(base_cmd, timeout_sec)

        # ── Windows venv workaround (Python 3.13 venvlauncher bug) ─
        if sys.platform == "win32" and re.search(r"python3?\s+.*-m\s+venv\b", command):
            return self._create_venv_windows(command, work_dir, cmd_timeout)

        # ── Execute ───────────────────────────────────────────────
        print(f"   [Terminal] Running: {command} (timeout: {cmd_timeout}s)")
        print(f"[UI_COMMAND] agent_terminal_output {command}")

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=work_dir,
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=cmd_timeout,
                env={**os.environ, "PYTHONPATH": self.workspace_path},
            )

            output = result.stdout or ""
            if result.stderr:
                output += "\n--- STDERR ---\n" + result.stderr

            # Truncate if needed
            output = self._truncate_output(output)

            exit_info = f"\n[Exit code: {result.returncode}]"
            return output + exit_info

        except subprocess.TimeoutExpired:
            return f"[Error] Command timed out after {cmd_timeout}s: {command}"
        except Exception as e:
            return f"[Error] Failed to execute command: {e}"

    def run_test(self, test_path: str | None = None) -> str:
        """
        Convenience wrapper: run project tests.
        Auto-detects pytest (Python) or npm test (JS/TS).
        """
        # Check for Python tests
        engine_dir = os.path.join(self.workspace_path, "engine")
        if os.path.isdir(os.path.join(engine_dir, "tests")):
            cmd = "pytest tests/ -v --tb=short"
            if test_path:
                cmd = f"pytest {test_path} -v --tb=short"
            return self.run_command(cmd, timeout_sec=60, cwd=engine_dir)

        # Check for npm tests
        app_dir = os.path.join(self.workspace_path, "app")
        if os.path.isfile(os.path.join(app_dir, "package.json")):
            return self.run_command("npm test", timeout_sec=120, cwd=app_dir)

        return "[Error] No test framework detected (no engine/tests/ or app/package.json)"

    def run_lint(self, file_path: str | None = None) -> str:
        """
        Convenience wrapper: run linter on file or project.
        Uses ruff for Python, eslint for JS/TS/Svelte.
        """
        if file_path and file_path.endswith(".py"):
            engine_dir = os.path.join(self.workspace_path, "engine")
            cmd = f"ruff check {file_path}" if file_path else "ruff check ."
            return self.run_command(cmd, timeout_sec=15, cwd=engine_dir)

        if file_path and file_path.endswith((".js", ".ts", ".svelte", ".jsx", ".tsx")):
            app_dir = os.path.join(self.workspace_path, "app")
            return self.run_command(f"npx eslint {file_path}", timeout_sec=30, cwd=app_dir)

        # Default: run both
        results = []
        engine_dir = os.path.join(self.workspace_path, "engine")
        if os.path.isfile(os.path.join(engine_dir, "ruff.toml")):
            results.append("=== Python (ruff) ===")
            results.append(self.run_command("ruff check .", timeout_sec=15, cwd=engine_dir))

        app_dir = os.path.join(self.workspace_path, "app")
        if os.path.isfile(os.path.join(app_dir, "package.json")):
            results.append("=== Frontend (eslint) ===")
            results.append(self.run_command("npm run lint", timeout_sec=30, cwd=app_dir))

        return "\n".join(results) if results else "[Error] No linter configuration found"

    def _infer_cwd(self, base_cmd: str) -> str:
        """Return the most relevant sub-directory for a command, falling back to workspace root."""
        python_cmds = {"python", "python3", "pip", "pip3", "pytest", "ruff",
                       "mypy", "flake8", "black", "isort", "uv", "virtualenv"}
        node_cmds = {"npm", "npx", "node", "eslint", "prettier", "tsc"}

        engine_dir = os.path.join(self.workspace_path, "engine")
        app_dir = os.path.join(self.workspace_path, "app")

        if base_cmd in python_cmds and os.path.isdir(engine_dir):
            return engine_dir
        if base_cmd in node_cmds and os.path.isdir(app_dir):
            return app_dir
        return self.workspace_path

    def _create_venv_windows(self, command: str, work_dir: str, timeout: int) -> str:
        """
        Windows-safe venv creation.  Tries three strategies in order:
          1. Original command as-is
          2. python -m venv --without-pip <path>  (avoids venvlauncher.exe copy)
          3. uv venv <path>  (modern tool, no launcher dependency)
        """
        def _run(cmd: str) -> tuple[int, str]:
            result = subprocess.run(
                cmd, shell=True, cwd=work_dir,
                capture_output=True, encoding="utf-8", errors="replace", timeout=timeout,
                env={**os.environ, "PYTHONPATH": self.workspace_path},
            )
            out = result.stdout
            if result.stderr:
                out += "\n--- STDERR ---\n" + result.stderr
            return result.returncode, self._truncate_output(out)

        # Strategy 1: original command
        rc, out = _run(command)
        if rc == 0 and "unable to copy" not in out.lower():
            return out + f"\n[Exit code: {rc}]"

        # Strategy 2: --without-pip flag (avoids the launcher copy)
        # Extract venv path from original command
        m = re.search(r"-m\s+venv\s+(--[\w-]+\s+)*(\S+)", command)
        venv_path = m.group(2) if m else "venv"
        fallback_cmd = f"python -m venv --without-pip {venv_path}"
        rc2, out2 = _run(fallback_cmd)
        if rc2 == 0:
            # Install pip into the freshly-created venv
            pip_cmd = f"{venv_path}\\Scripts\\python.exe -m ensurepip --upgrade"
            _, pip_out = _run(pip_cmd)
            return (
                f"[Warning] Original venv command failed; retried with --without-pip.\n"
                f"{out2}\n{pip_out}\n[Exit code: {rc2}]"
            )

        # Strategy 3: uv venv (if uv is installed)
        uv_cmd = f"uv venv {venv_path}"
        rc3, out3 = _run(uv_cmd)
        if rc3 == 0:
            return (
                f"[Warning] python -m venv failed; created venv with 'uv venv' instead.\n"
                f"{out3}\n[Exit code: {rc3}]"
            )

        # All strategies failed — return original error
        return (
            f"[Error] venv creation failed on Windows (Python 3.13 venvlauncher bug).\n"
            f"Original error:\n{out}\n"
            f"--without-pip attempt:\n{out2}\n"
            f"uv venv attempt:\n{out3}\n"
            "Fix: upgrade to Python 3.13.1+, or run 'pip install uv' then retry."
        )

    def _truncate_output(self, output: str) -> str:
        """Truncate output to MAX_OUTPUT_LINES, keeping first and last portions."""
        lines = output.splitlines()
        if len(lines) <= MAX_OUTPUT_LINES:
            return output

        keep_first = 50
        keep_last = MAX_OUTPUT_LINES - keep_first
        truncated_count = len(lines) - MAX_OUTPUT_LINES

        head = lines[:keep_first]
        tail = lines[-keep_last:]
        return "\n".join(head) + f"\n\n[... {truncated_count} lines truncated ...]\n\n" + "\n".join(tail)
