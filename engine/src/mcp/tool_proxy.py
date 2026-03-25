"""
MCPToolProxy — bridges an MCP tool into the existing agent tool dispatch pattern.

The orchestrator dispatches tools as:
    fn = getattr(tool_instance, method)
    observation = fn(**kwargs)

For MCP tools, `method` is always "execute" and kwargs are passed directly
as MCP tool arguments.
"""

from typing import Dict, Any


class MCPToolProxy:
    """
    Wraps a single MCP tool so it behaves like a built-in tool in the agent loop.
    """

    def __init__(
        self,
        manager: Any,
        server_name: str,
        tool_name: str,
        description: str,
        input_schema: Dict,
    ):
        self._manager = manager
        self._server_name = server_name
        self._tool_name = tool_name
        self.description = description
        self.input_schema = input_schema

    def execute(self, **kwargs) -> str:
        """Synchronously dispatch the MCP tool call and return the result string."""
        return self._manager.call_tool(self._server_name, self._tool_name, kwargs)
