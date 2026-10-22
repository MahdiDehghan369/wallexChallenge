"""Wallex MCP integration (T-107).

No Wallex MCP documentation was provided (``docs/vendor/wallex_mcp/`` is empty), so the native tool
backend is used. When a human adds ``docs/vendor/wallex_mcp/server.json`` like::

    {"transport": "stdio", "command": "npx", "args": ["-y", "<wallex-mcp-package>"],
     "tool_map": {"get_ticker": "<mcp tool name>", "get_orderbook_summary": "<mcp tool name>"}}

``McpToolRegistry`` connects with the Python MCP SDK and maps the allowed read-only tools onto the
listed MCP tools. Tools that are not in ``tool_map`` fall back to the native implementation, and MCP
order tools are never reachable (the allow-list of ``agents.tools`` applies first).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from siavash.agents.tools import ALLOWED_TOOLS, ToolNotAllowed, ToolRegistry, _clip


def mcp_server_config(vendor_dir: str | Path = "docs/vendor/wallex_mcp") -> dict[str, Any] | None:
    """Parsed ``server.json`` or ``None``."""
    p = Path(vendor_dir) / "server.json"
    if not p.exists():
        return None
    cfg = json.loads(p.read_text(encoding="utf-8"))
    bad = [k for k in cfg.get("tool_map", {}) if k not in ALLOWED_TOOLS]
    if bad:
        raise ValueError(f"server.json maps non-allowed tools: {bad}")
    return cfg


class McpToolRegistry(ToolRegistry):
    backend = "mcp"

    def __init__(self, server_cfg: dict[str, Any], *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.server_cfg = server_cfg
        self.tool_map: dict[str, str] = dict(server_cfg.get("tool_map", {}))

    async def _call_mcp(self, mcp_name: str, args: dict[str, Any]) -> Any:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        if self.server_cfg.get("transport", "stdio") != "stdio":
            raise RuntimeError("only stdio MCP transport is wired; add HTTP support when the docs require it")
        params = StdioServerParameters(
            command=self.server_cfg["command"], args=list(self.server_cfg.get("args", []))
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            res = await session.call_tool(mcp_name, args)
            texts = [getattr(c, "text", "") for c in res.content]
            return {"mcp_tool": mcp_name, "result": "\n".join(t for t in texts if t)}

    async def execute(self, name: str, args: dict[str, Any]) -> str:
        if name not in ALLOWED_TOOLS:
            raise ToolNotAllowed(f"tool {name!r} is not allowed")
        if name not in self.tool_map:
            return await super().execute(name, args)
        try:
            out = _clip(await self._call_mcp(self.tool_map[name], args))
            self._log(name, args, "ok", len(out))
            return out
        except Exception as e:
            self._log(name, args, f"mcp_error: {type(e).__name__}", 0)
            return await super().execute(name, args)
