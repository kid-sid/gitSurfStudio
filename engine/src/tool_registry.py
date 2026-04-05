"""
Tool registry for GitSurf Studio.

Centralizes tool descriptions (for the LLM), tool instantiation,
and MCP server background initialization.
"""

import threading
from src.logger import get_logger

from src.tools.file_editor_tool import FileEditorTool
from src.tools.search_tool import SearchTool
from src.tools.web_tool import WebSearchTool
from src.tools.git_tool import GitTool
from src.tools.editor_ui_tool import EditorUITool
from src.tools.find_by_name_tool import FindByNameTool
from src.tools.list_files_tool import ListFilesTool
from src.tools.notify_user_tool import NotifyUserTool
from src.tools.browser_tool import BrowserTool
from src.tools.terminal_tool import TerminalTool
from src.mcp.client_manager import MCPClientManager
from src.mcp.tool_proxy import MCPToolProxy

logger = get_logger("tool_registry")


AVAILABLE_TOOLS = """
Tool: FileEditorTool
Description: Read, write, modify, or delete files inside the project directory.
Methods:
  - read_file(rel_path, start_line=None, end_line=None)
  - write_file(rel_path, content)
  - replace_in_file(rel_path, target, replacement, allow_multiple=False)
    NOTE: target must match exactly once unless allow_multiple=True.
    On ambiguity, returns the line numbers of all matches to help refine the target.
  - multi_replace_file_content(rel_path, replacement_chunks)
    Replaces multiple non-contiguous chunks in a file. replacement_chunks is a list of dicts with 'targetContent' and 'replacementContent' keys.
  - delete_file(rel_path)

Tool: FindByNameTool
Description: Search for files using glob patterns.
Methods:
  - find_by_name(pattern, type="any", max_depth=None, full_path=False)
    Type can be file, directory, or any.

Tool: ListFilesTool
Description: List files and folders.
Methods:
  - list_dir(rel_path="."): Lists shallow contents of a directory.
  - list_recursive(rel_path="."): Lists all relative paths recursively.

Tool: NotifyUserTool
Description: Pause agent execution and request human feedback or approval.
Methods:
  - notify_user(message, paths_to_review=None, blocked_on_user=True)
    Blocks execution and returns string response from user.

Tool: EditorUITool
Description: Control the IDE user interface.
Methods:
  - open_file(rel_path)
    Opens the specified file in an editor tab.

Tool: GitTool
Description: Handle local Git operations (status, stage, commit, diff).
Methods:
  - get_status()
  - stage_files(files)
  - commit(message)
  - get_diff(file_path=None)

Tool: SearchTool
Description: Search for text patterns in the codebase using ripgrep.
Methods:
  - search(query, search_path=".")
  - search_and_chunk(query, search_path=".", context_lines=10)

Tool: WebSearchTool
Description: Search the web or fetch URL content for documentation/errors.
Methods:
  - search(query, num_results=5)
  - fetch_url(url)

Tool: SymbolPeekerTool
Description: Peek the definition of any function or class by name (like F12 in VS Code). Returns the full source block, file path, and line range. Use this to inspect a symbol's current implementation before editing it — avoids reading entire files.
Methods:
  - peek_symbol(symbol_name): Returns [{file, type, name, start_line, end_line, content}]

Tool: BrowserTool
Description: High-level browser automation for verifying pages, testing interactions, and debugging client-side issues. Uses Playwright under the hood. Prefer this over raw mcp__playwright__* calls for multi-step browser workflows.
Methods:
  - verify_page(url, checks=None, wait_ms=2000)
    Navigate to URL, capture snapshot + screenshot, optionally check for expected text.
    checks: JSON array of strings, e.g. '["Submit button", "Welcome"]'
  - test_interaction(url, steps)
    Execute a sequence of browser actions and report pass/fail per step.
    steps: JSON array, e.g. '[{"action":"click","element":"Login"},{"action":"snapshot","expect":"Dashboard"}]'
  - debug_page(url)
    Capture snapshot, screenshot, and console messages for debugging.
  - scrape_rendered(url)
    Fetch content from a JavaScript-rendered page (use instead of WebSearchTool.fetch_url for SPAs).

Tool: TerminalTool
Description: Execute shell commands safely in the workspace (tests, linting, builds, dev servers).
Methods:
  - run_command(command, timeout_sec=30, cwd=None)
    Run a shell command. Allowed: pytest, ruff, npm, node, make, git (read-only), docker (read-only), curl, etc.
  - run_test(test_path=None)
    Run project tests (auto-detects pytest or npm test).
  - run_lint(file_path=None)
    Run linter (ruff for Python, eslint for JS/TS/Svelte).
  - run_format(file_path)
    Run formatter (ruff format for .py, prettier for .js/.ts/.svelte/.json/.css).
  - run_script(script_content, ext="py")
    Write a temp script and execute it. ext: "py" (python) or "js" (node). Useful for multi-step shell flows.
  - install_package(manager, package, dev=False)
    Install a package via "pip" or "npm" with structured success/failure output.
  - activate_venv(path)
    Activate a virtual environment; all subsequent run_command calls will use it.
  - run_background(command, label, cwd=None)
    Start a long-running process (dev server, watcher) in the background. Reference by label.
  - stop_background(label)
    Terminate a background process started with run_background().
  - list_background()
    List all running background processes with status and recent output.
  - get_background_output(label, lines=50)
    Read recent stdout/stderr from a background process's ring buffer.
  - check_port(port)
    Check if a TCP port on localhost is in use. Returns "Port N is IN USE." or "Port N is FREE."
  - get_history(n=10)
    Return the last n commands executed, with exit codes and timestamps.
"""


def _build_mcp_schema_hint(input_schema: dict) -> str:
    """
    Build a rich, human-readable parameter hint for an MCP tool.
    Marks required params with *, optional with ?, includes descriptions and enum values.
    """
    props = input_schema.get("properties", {})
    required = set(input_schema.get("required", []))
    if not props:
        return ""
    parts = []
    for k, v in props.items():
        req_marker = "*" if k in required else "?"
        typ = v.get("type", "any")
        if "enum" in v:
            typ = "enum[" + "|".join(str(e) for e in v["enum"]) + "]"
        elif typ == "object" and "properties" in v:
            nested = ", ".join(
                f"{nk}: {nv.get('type', 'any')}" for nk, nv in v["properties"].items()
            )
            typ = f"object{{{nested}}}"
        hint = f"{k}{req_marker}: {typ}"
        desc = v.get("description", "")
        if desc:
            hint += f' "{desc[:60]}"'
        parts.append(hint)
    return ", ".join(parts)


def register_tools(state, workspace_path: str, pipeline_ctx):
    """Instantiate all agent tools and register them on state."""
    from src.engine_state import SymbolPeekerTool

    file_editor = FileEditorTool(root_path=workspace_path)
    git_tool = GitTool(root_path=workspace_path)
    editor_ui = EditorUITool(root_path=workspace_path)
    searcher = SearchTool()
    web_tool = WebSearchTool()

    terminal_tool = TerminalTool(workspace_path=workspace_path)
    state.terminal_tool = terminal_tool

    state.agent_tools = {
        "FileEditorTool": file_editor,
        "GitTool": git_tool,
        "EditorUITool": editor_ui,
        "SearchTool": searcher,
        "WebSearchTool": web_tool,
        "SymbolPeekerTool": SymbolPeekerTool(pipeline_ctx, workspace_path),
        "BrowserTool": BrowserTool(tools_getter=lambda: state.agent_tools),
        "TerminalTool": terminal_tool,
        "FindByNameTool": FindByNameTool(workspace_path),
        "ListFilesTool": ListFilesTool(workspace_path),
        "NotifyUserTool": NotifyUserTool(
            ask_callback=lambda msg, opts=None: state.active_executor._ask_user(msg, opts) if state.active_executor else "ok"
        ),
    }
    state.git_tool = git_tool


def register_mcp_tools(state, config_path: str) -> None:
    """
    Runs in a daemon thread. Connects to all MCP servers in parallel, then
    registers proxies into state.agent_tools and updates state.available_tools.
    """
    try:
        manager = MCPClientManager()
        manager.initialize(config_path)

        mcp_tools_desc = ""
        for tool_info in manager.list_all_tools():
            full_key = f"mcp__{tool_info['server']}__{tool_info['name']}"
            shorthand = tool_info["name"].replace("-", "_")
            proxy = MCPToolProxy(
                manager=manager,
                server_name=tool_info["server"],
                tool_name=tool_info["name"],
                description=tool_info["description"],
                input_schema=tool_info.get("input_schema", {}),
            )
            schema_hint = _build_mcp_schema_hint(tool_info.get("input_schema", {}))
            with state._lock:
                state.agent_tools[full_key] = proxy
                state.agent_tools[shorthand] = proxy   # alias

            mcp_tools_desc += (
                f"\nTool: {full_key}  (alias: {shorthand})\n"
                f"Description: {tool_info['description']}\n"
                f"Methods:\n"
                f"  - execute({schema_hint})\n"
                f"  NOTE: method must always be \"execute\"\n"
            )

        with state._lock:
            state.mcp_manager = manager
            if mcp_tools_desc:
                state.available_tools = (
                    AVAILABLE_TOOLS
                    + "\n\n## MCP Tools (* = required, ? = optional)\n"
                    + mcp_tools_desc
                )
            state.mcp_ready = True

        tool_count = len(manager.list_all_tools())
        logger.info("[MCP] Ready — %d tool(s) registered.", tool_count)
    except Exception as exc:
        logger.error("[MCP] Background init failed: %s", exc)
        with state._lock:
            state.mcp_ready = True   # mark ready anyway so status endpoint doesn't hang


def start_mcp_background(state, config_path: str) -> None:
    """Start MCP tool registration in a background daemon thread."""
    state.mcp_ready = False
    mcp_thread = threading.Thread(
        target=register_mcp_tools,
        args=(state, config_path),
        daemon=True,
        name="mcp-init",
    )
    mcp_thread.start()
