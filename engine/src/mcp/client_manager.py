"""
MCPClientManager — manages connections to MCP servers defined in mcp_config.json.

Each MCP server runs as a subprocess (stdio transport). A single background
thread hosts a dedicated asyncio event loop so MCP calls can be dispatched
synchronously from the existing (synchronous) agent action loop.
"""

import asyncio
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    logger.warning("[MCP] 'mcp' package not installed — MCP integration disabled. Run: pip install mcp")


class _ServerConnection:
    """Lifecycle manager for a single MCP server subprocess."""

    def __init__(self, name: str, config: Dict):
        self.name = name
        self.config = config
        self.tools: List[Dict] = []
        self._session: Optional[Any] = None   # ClientSession once connected
        self._exit_stack: Optional[Any] = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def connect(self) -> None:
        if not MCP_AVAILABLE:
            return

        transport = self.config.get("transport", "stdio")
        if transport != "stdio":
            logger.warning("[MCP] Only stdio transport is supported. Skipping '%s'.", self.name)
            return

        command = self.config.get("command", "npx")
        args = self.config.get("args", [])

        # Merge env overrides with current environment, expanding ${VAR} references
        env_overrides = {
            k: os.path.expandvars(v)
            for k, v in self.config.get("env", {}).items()
        }
        env = {**os.environ, **env_overrides}

        try:
            from contextlib import AsyncExitStack

            self._exit_stack = AsyncExitStack()
            server_params = StdioServerParameters(command=command, args=args, env=env)

            read, write = await self._exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(read, write)
            )
            await self._session.initialize()

            resp = await self._session.list_tools()
            self.tools = [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "input_schema": t.inputSchema if hasattr(t, "inputSchema") else {},
                }
                for t in resp.tools
            ]
            self._connected = True
            logger.info(
                "[MCP] Connected to '%s' — %d tool(s) discovered.", self.name, len(self.tools)
            )
        except Exception as exc:
            logger.error("[MCP] Failed to connect to '%s': %s", self.name, exc)
            self._connected = False

    async def call_tool(self, tool_name: str, arguments: Dict) -> str:
        if not self._connected or self._session is None:
            return f"[Error] MCP server '{self.name}' is not connected."
        try:
            result = await self._session.call_tool(tool_name, arguments=arguments)
            if hasattr(result, "content") and result.content:
                parts = [
                    block.text if hasattr(block, "text") else str(block)
                    for block in result.content
                ]
                return "\n".join(parts)
            return str(result)
        except Exception as exc:
            return f"[Error] MCP tool '{tool_name}' on '{self.name}' failed: {exc}"

    async def disconnect(self) -> None:
        if self._exit_stack:
            try:
                await self._exit_stack.aclose()
            except Exception as exc:
                logger.warning("[MCP] Error disconnecting '%s': %s", self.name, exc)
        self._connected = False
        self._session = None


class MCPClientManager:
    """
    Owns all MCP server connections.

    Uses a dedicated background thread + event loop so that async MCP calls
    can be invoked synchronously from the existing (sync) agent action loop.
    """

    def __init__(self):
        self._servers: Dict[str, _ServerConnection] = {}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None

    # ── Internal async runner ───────────────────────────────────────────────

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever, daemon=True, name="mcp-event-loop"
            )
            self._thread.start()
        return self._loop

    def _run(self, coro) -> Any:
        """Schedule *coro* on the background loop and block until it completes."""
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=60)

    # ── Public API ──────────────────────────────────────────────────────────

    async def _connect_all(self, servers: Dict[str, Dict]) -> None:
        """Connect to all servers in parallel using asyncio.gather."""
        connections: Dict[str, _ServerConnection] = {}
        for name, cfg in servers.items():
            if cfg.get("enabled", True):
                connections[name] = _ServerConnection(name, cfg)
            else:
                logger.info("[MCP] Skipping disabled server '%s'.", name)

        # All npx spawns fire simultaneously — reduces 3-server startup from ~15s to ~5s
        results = await asyncio.gather(
            *(conn.connect() for conn in connections.values()),
            return_exceptions=True,
        )
        for (name, conn), result in zip(connections.items(), results):
            if isinstance(result, Exception):
                logger.error("[MCP] Server '%s' failed to connect: %s", name, result)
            self._servers[name] = conn   # register even on failure (connected=False)

    def initialize(self, config_path: str) -> None:
        """
        Load mcp_config.json and connect to all enabled servers in parallel.
        Safe to call from synchronous startup code.
        """
        if not MCP_AVAILABLE:
            return

        if not os.path.isfile(config_path):
            logger.info("[MCP] Config not found at '%s'. MCP disabled.", config_path)
            return

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except Exception as exc:
            logger.error("[MCP] Failed to parse mcp_config.json: %s", exc)
            return

        servers = config.get("mcpServers", {})
        try:
            self._run(self._connect_all(servers))
        except Exception as exc:
            logger.error("[MCP] Parallel server connect failed: %s", exc)

    def list_all_tools(self) -> List[Dict]:
        """Return a flat list of all tools from all connected servers."""
        tools = []
        for server_name, conn in self._servers.items():
            if conn.connected:
                for t in conn.tools:
                    tools.append(
                        {
                            "server": server_name,
                            "name": t["name"],
                            "description": t["description"],
                            "input_schema": t.get("input_schema", {}),
                        }
                    )
        return tools

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict) -> str:
        """Synchronously call an MCP tool and return its text output."""
        conn = self._servers.get(server_name)
        if conn is None:
            return f"[Error] Unknown MCP server: '{server_name}'"
        return self._run(conn.call_tool(tool_name, arguments))

    def shutdown(self) -> None:
        """Disconnect all servers and stop the background event loop."""
        for conn in self._servers.values():
            try:
                self._run(conn.disconnect())
            except Exception:
                pass
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._loop.stop)
