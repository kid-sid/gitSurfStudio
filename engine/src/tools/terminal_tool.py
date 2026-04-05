"""
TerminalTool: Safe shell command execution for the coding agent.

Runs commands in a subprocess with allowlist/blocklist enforcement,
timeout handling, and output truncation.
"""

import collections
import contextlib
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time

# Commands that can run without user approval
SAFE_COMMANDS = {
    "pytest", "python", "python3", "ruff", "mypy", "flake8", "black", "isort",
    "npm", "npx", "node", "eslint", "prettier", "tsc",
    "cat", "ls", "dir", "find", "grep", "rg", "head", "tail", "wc",
    "echo", "pwd", "which", "where", "type",
    "pip", "pip3", "cargo", "go", "rustc", "javac",
    "uv", "virtualenv",
    # Extended set
    "make", "curl", "git", "docker", "zip", "unzip",
}

# git subcommands that are allowed (read-only, no side effects)
GIT_READONLY_SUBCOMMANDS = {
    "status", "log", "diff", "show", "branch", "stash",
    "describe", "rev-parse", "shortlog", "ls-files", "ls-tree",
}

# docker subcommands that are allowed (read-only, no side effects)
DOCKER_READONLY_SUBCOMMANDS = {
    "ps", "logs", "inspect", "images", "stats", "top", "version", "info",
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
    "make": 120,
    "git": 15,
    "docker": 15,
    "curl": 30,
}

MAX_OUTPUT_LINES = 200

# Pattern for extracting error-relevant lines during smart truncation
_ERROR_LINE_RE = re.compile(
    r"(error|Error|ERROR|FAILED|failed|exception|Exception|Traceback|✗|\[ERROR\]|WARN|warning)",
)


class TerminalTool:
    """
    Safe shell command execution for the agent.
    Enforces allowlist/blocklist, timeouts, and output limits.
    """

    def __init__(self, workspace_path: str):
        self.workspace_path = os.path.abspath(workspace_path)
        self._bg_processes: dict = {}     # label → {proc, buffer, thread, command}
        self._history: list = []          # [{command, cwd, exit_code, timestamp}]
        self._active_venv: str | None = None

    # ──────────────────────────────────────────────────────────────────
    # Core command execution
    # ──────────────────────────────────────────────────────────────────

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

        # ── Subcommand guards for git and docker ──────────────────
        if base_cmd == "git":
            tokens = command.strip().split()
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd not in GIT_READONLY_SUBCOMMANDS:
                return (
                    f"[Error] git subcommand '{subcmd}' is not allowed via TerminalTool. "
                    f"Use GitTool for write operations. "
                    f"Allowed read-only subcommands: {', '.join(sorted(GIT_READONLY_SUBCOMMANDS))}"
                )

        if base_cmd == "docker":
            tokens = command.strip().split()
            subcmd = tokens[1] if len(tokens) > 1 else ""
            if subcmd not in DOCKER_READONLY_SUBCOMMANDS:
                return (
                    f"[Error] docker subcommand '{subcmd}' is not allowed. "
                    f"Allowed read-only subcommands: {', '.join(sorted(DOCKER_READONLY_SUBCOMMANDS))}"
                )

        # Resolve working directory — use smart inference if no explicit cwd
        work_dir = cwd if cwd is not None else self._infer_cwd(base_cmd)
        if not os.path.isdir(work_dir):
            return f"[Error] Working directory does not exist: {work_dir}"

        # Use command-specific timeout if available
        cmd_timeout = TIMEOUTS.get(base_cmd, timeout_sec)

        # ── Windows venv workaround (Python 3.13 venvlauncher bug) ─
        if sys.platform == "win32" and re.search(r"python3?\s+.*-m\s+venv\b", command):
            return self._create_venv_windows(command, work_dir, cmd_timeout)  # pyright: ignore[reportUnreachable]

        # Build environment — inject active venv if set
        env = {**os.environ, "PYTHONPATH": self.workspace_path}
        if self._active_venv:
            env = self._build_venv_env(env)

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
                env=env,
            )

            output = result.stdout or ""
            if result.stderr:
                output += "\n--- STDERR ---\n" + result.stderr

            output = self._truncate_output(output)

            self._history.append({
                "command": command,
                "cwd": work_dir,
                "exit_code": result.returncode,
                "timestamp": time.strftime("%H:%M:%S"),
            })

            exit_info = f"\n[Exit code: {result.returncode}]"
            return output + exit_info

        except subprocess.TimeoutExpired:
            return f"[Error] Command timed out after {cmd_timeout}s: {command}"
        except Exception as e:
            return f"[Error] Failed to execute command: {e}"

    # ──────────────────────────────────────────────────────────────────
    # Convenience wrappers
    # ──────────────────────────────────────────────────────────────────

    def run_test(self, test_path: str | None = None) -> str:
        """
        Convenience wrapper: run project tests.
        Auto-detects pytest (Python) or npm test (JS/TS).
        """
        engine_dir = os.path.join(self.workspace_path, "engine")
        if os.path.isdir(os.path.join(engine_dir, "tests")):
            cmd = "pytest tests/ -v --tb=short"
            if test_path:
                cmd = f"pytest {test_path} -v --tb=short"
            return self.run_command(cmd, timeout_sec=60, cwd=engine_dir)

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

    def run_format(self, file_path: str) -> str:
        """
        Run code formatter on a file.
        Uses ruff format for Python, prettier for JS/TS/Svelte/CSS/JSON.
        """
        if file_path.endswith(".py"):
            engine_dir = os.path.join(self.workspace_path, "engine")
            cwd = engine_dir if os.path.isdir(engine_dir) else self.workspace_path
            return self.run_command(f"ruff format {file_path}", timeout_sec=15, cwd=cwd)

        if file_path.endswith((".js", ".ts", ".svelte", ".jsx", ".tsx", ".json", ".css")):
            app_dir = os.path.join(self.workspace_path, "app")
            cwd = app_dir if os.path.isdir(app_dir) else self.workspace_path
            return self.run_command(f"npx prettier --write {file_path}", timeout_sec=15, cwd=cwd)

        return f"[Error] Unsupported file type for formatting: {file_path}"

    def install_package(self, manager: str, package: str, dev: bool = False) -> str:
        """
        Install a package via pip or npm with structured output.

        Args:
            manager: "pip" or "npm"
            package: Package name (e.g. "requests", "lodash")
            dev: npm only — install as devDependency (--save-dev)

        Returns:
            Success/failure message with install output
        """
        if manager == "pip":
            output = self.run_command(f"pip install {package}")
            if "Successfully installed" in output or "already satisfied" in output.lower():
                return f"[OK] pip install {package} succeeded.\n{output}"
            return output

        if manager == "npm":
            flag = "--save-dev" if dev else "--save"
            app_dir = os.path.join(self.workspace_path, "app")
            cwd = app_dir if os.path.isdir(app_dir) else self.workspace_path
            output = self.run_command(f"npm install {flag} {package}", cwd=cwd)
            if "added" in output.lower() or "up to date" in output.lower():
                return f"[OK] npm install {package} succeeded.\n{output}"
            return output

        return f"[Error] Unknown package manager '{manager}'. Supported: pip, npm"

    def run_script(self, script_content: str, ext: str = "py") -> str:
        """
        Write script content to a temp file, execute it, then delete it.
        Useful for multi-command sequences that need shell flow control.

        Args:
            script_content: The script source code to run
            ext: File extension — "py" (python) or "js" (node)

        Returns:
            Script output or error
        """
        ext = ext.lstrip(".")
        interpreter_map = {"py": "python", "js": "node"}

        if ext not in interpreter_map:
            return (
                f"[Error] Unsupported script type '.{ext}'. "
                f"Supported: {', '.join(interpreter_map.keys())}"
            )

        interpreter = interpreter_map[ext]

        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=f".{ext}",
                delete=False,
                encoding="utf-8",
            ) as f:
                f.write(script_content)
                script_path = f.name
        except Exception as e:
            return f"[Error] Failed to write temp script: {e}"

        try:
            return self.run_command(
                f"{interpreter} {script_path}",
                cwd=self._infer_cwd(interpreter),
            )
        finally:
            with contextlib.suppress(OSError):
                os.unlink(script_path)

    # ──────────────────────────────────────────────────────────────────
    # Background process management
    # ──────────────────────────────────────────────────────────────────

    def run_background(self, command: str, label: str, cwd: str | None = None) -> str:
        """
        Start a long-running process in the background (dev servers, watchers, etc.).
        Output is captured in a ring buffer readable via get_background_output().

        Args:
            command: Shell command to run (must be in SAFE_COMMANDS allowlist)
            label: Unique name to reference this process later
            cwd: Working directory (auto-inferred if not provided)

        Returns:
            Success message with PID, or error
        """
        if not command or not command.strip():
            return "[Error] Empty command"

        for pattern in _BLOCKED_RE:
            if pattern.search(command):
                return f"[Error] Command blocked by safety policy: {command}"

        raw_token = command.strip().split()[0].strip('"').strip("'")
        base_cmd = raw_token.split("/")[-1].split("\\")[-1]
        if base_cmd.lower().endswith(".exe"):
            base_cmd = base_cmd[:-4]

        if base_cmd not in SAFE_COMMANDS:
            return (
                f"[Error] Command '{base_cmd}' is not in the safe commands list. "
                f"Safe commands: {', '.join(sorted(SAFE_COMMANDS))}"
            )

        if label in self._bg_processes:
            entry = self._bg_processes[label]
            if entry["proc"].poll() is None:
                return (
                    f"[Error] Background process '{label}' is already running "
                    f"(PID {entry['proc'].pid}). "
                    f"Call stop_background('{label}') first."
                )

        work_dir = cwd if cwd is not None else self._infer_cwd(base_cmd)
        if not os.path.isdir(work_dir):
            return f"[Error] Working directory does not exist: {work_dir}"

        env = {**os.environ, "PYTHONPATH": self.workspace_path}
        if self._active_venv:
            env = self._build_venv_env(env)

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=work_dir,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
        except Exception as e:
            return f"[Error] Failed to start background process: {e}"

        buffer: collections.deque = collections.deque(maxlen=1000)

        def _drain():
            for line in proc.stdout:
                buffer.append(line.rstrip())

        thread = threading.Thread(target=_drain, daemon=True, name=f"bg-drain-{label}")
        thread.start()

        self._bg_processes[label] = {
            "proc": proc,
            "buffer": buffer,
            "thread": thread,
            "command": command,
        }

        print(f"[UI_COMMAND] agent_terminal_output {command}")
        return f"[OK] Started background process '{label}' (PID {proc.pid}): {command}"

    def stop_background(self, label: str) -> str:
        """
        Terminate a background process started with run_background().

        Args:
            label: The label used in run_background()

        Returns:
            Confirmation message with exit code, or error if label not found
        """
        if label not in self._bg_processes:
            running = list(self._bg_processes.keys())
            return (
                f"[Error] No background process with label '{label}'. "
                f"Running: {running}"
            )

        entry = self._bg_processes.pop(label)
        proc = entry["proc"]

        if proc.poll() is not None:
            return f"[OK] Process '{label}' had already exited (Exit code: {proc.returncode})"

        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

        return f"[OK] Stopped background process '{label}' (PID {proc.pid}, Exit code: {proc.returncode})"

    def list_background(self) -> str:
        """
        List all background processes with their status and recent output.

        Returns:
            Formatted list of running/exited background processes
        """
        if not self._bg_processes:
            return "No background processes running."

        lines = ["Background processes:"]
        for label, entry in self._bg_processes.items():
            proc = entry["proc"]
            status = "running" if proc.poll() is None else f"exited ({proc.returncode})"
            tail = list(entry["buffer"])[-3:]
            lines.append(f"  [{label}] PID={proc.pid} status={status}")
            lines.append(f"    cmd: {entry['command']}")
            if tail:
                lines.append(f"    recent: {' | '.join(tail)}")
        return "\n".join(lines)

    def get_background_output(self, label: str, lines: int = 50) -> str:
        """
        Read recent stdout/stderr from a background process.

        Args:
            label: The label used in run_background()
            lines: Number of most-recent lines to return (default 50)

        Returns:
            Recent output from the process's ring buffer
        """
        if label not in self._bg_processes:
            running = list(self._bg_processes.keys())
            return (
                f"[Error] No background process with label '{label}'. "
                f"Running: {running}"
            )

        entry = self._bg_processes[label]
        proc = entry["proc"]
        buf = list(entry["buffer"])
        tail = buf[-lines:] if len(buf) > lines else buf

        status = "running" if proc.poll() is None else f"exited ({proc.returncode})"
        header = f"Process '{label}' (PID {proc.pid}, {status}) — last {len(tail)} lines:\n"
        return header + ("\n".join(tail) if tail else "(no output yet)")

    # ──────────────────────────────────────────────────────────────────
    # Virtual environment management
    # ──────────────────────────────────────────────────────────────────

    def activate_venv(self, path: str) -> str:
        """
        Record a virtual environment so subsequent run_command calls use it.
        Prepends the venv's bin/Scripts directory to PATH for all future commands.

        Args:
            path: Path to the venv root (absolute, or relative to workspace)

        Returns:
            Confirmation message or error if path is invalid
        """
        if not os.path.isabs(path):
            path = os.path.join(self.workspace_path, path)

        scripts_dir = os.path.join(path, "Scripts" if sys.platform == "win32" else "bin")
        if not os.path.isdir(scripts_dir):
            return f"[Error] Not a valid venv (missing Scripts/bin dir): {path}"

        self._active_venv = path
        return (
            f"[OK] Virtual environment activated: {path}\n"
            "Subsequent run_command calls will use this venv."
        )

    # ──────────────────────────────────────────────────────────────────
    # Port and process inspection
    # ──────────────────────────────────────────────────────────────────

    def check_port(self, port: int) -> str:
        """
        Check whether a TCP port on localhost is currently in use.

        Args:
            port: Port number to check (1–65535)

        Returns:
            "Port N is IN USE." or "Port N is FREE."
        """
        try:
            port = int(port)
        except (ValueError, TypeError):
            return f"[Error] Invalid port: {port}"

        if not (1 <= port <= 65535):
            return f"[Error] Port must be between 1 and 65535, got {port}"

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))

        if result == 0:
            return f"Port {port} is IN USE."
        return f"Port {port} is FREE."

    # ──────────────────────────────────────────────────────────────────
    # History
    # ──────────────────────────────────────────────────────────────────

    def get_history(self, n: int = 10) -> str:
        """
        Return the last n commands executed via run_command.

        Args:
            n: Number of history entries to return (default 10)

        Returns:
            Formatted command history with exit codes and timestamps
        """
        if not self._history:
            return "No command history."

        recent = self._history[-n:]
        lines = [f"Last {len(recent)} command(s):"]
        for i, entry in enumerate(recent, 1):
            lines.append(
                f"  {i}. [{entry['exit_code']}] {entry['command']}"
                f"  (cwd: {entry['cwd']}, {entry['timestamp']})"
            )
        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────────────

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

    def _build_venv_env(self, env: dict) -> dict:
        """Return a copy of env with the active venv's Scripts/bin prepended to PATH."""
        scripts = os.path.join(
            self._active_venv,
            "Scripts" if sys.platform == "win32" else "bin",
        )
        current_path = env.get("PATH", os.environ.get("PATH", ""))
        env = dict(env)
        env["PATH"] = scripts + os.pathsep + current_path
        env["VIRTUAL_ENV"] = self._active_venv
        return env

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
        m = re.search(r"-m\s+venv\s+(--[\w-]+\s+)*(\S+)", command)
        venv_path = m.group(2) if m else "venv"
        fallback_cmd = f"python -m venv --without-pip {venv_path}"
        rc2, out2 = _run(fallback_cmd)
        if rc2 == 0:
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
        """
        Truncate output to MAX_OUTPUT_LINES.
        Keeps head + tail, and fills the middle budget with error-relevant lines
        so critical failure messages are not silently dropped.
        """
        lines = output.splitlines()
        if len(lines) <= MAX_OUTPUT_LINES:
            return output

        keep_head = 20
        keep_tail = 50
        budget = MAX_OUTPUT_LINES - keep_head - keep_tail

        head = lines[:keep_head]
        tail = lines[-keep_tail:]
        middle = lines[keep_head:-keep_tail]

        error_lines = [line for line in middle if _ERROR_LINE_RE.search(line)]

        selected = error_lines if len(error_lines) <= budget else error_lines[:budget]

        dropped = len(middle) - len(selected)
        separator = f"\n[... {dropped} lines not shown; {len(selected)} error lines extracted ...]\n"

        return "\n".join(head) + separator + "\n".join(selected) + "\n" + "\n".join(tail)
