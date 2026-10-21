"""Optional stdio client for the existing COMSOL MCP server."""

from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

from app.config import LEGACY_ROOT


class LegacyMCPBridge:
    """Expose the legacy MCP without importing its COMSOL globals into FastAPI."""

    async def status(self, include_tools: bool = False) -> dict[str, Any]:
        if not LEGACY_ROOT.exists():
            return {"status": "Warning", "detail": "Legacy project not found", "tools": []}
        if not importlib.util.find_spec("mcp") or not importlib.util.find_spec("mph"):
            return {
                "status": "Warning",
                "detail": "Install requirements-comsol.txt to enable the legacy MCP bridge",
                "tools": [],
                "server": str(LEGACY_ROOT / "src" / "server.py"),
            }
        if not include_tools:
            return {
                "status": "Ready",
                "detail": "Legacy COMSOL MCP dependencies are available",
                "tools": [],
                "server": str(LEGACY_ROOT / "src" / "server.py"),
            }
        try:
            from mcp import ClientSession, StdioServerParameters
            from mcp.client.stdio import stdio_client

            parameters = StdioServerParameters(
                command=sys.executable,
                args=["-m", "src.server"],
                cwd=str(LEGACY_ROOT),
                env={**os.environ, "COMSOL_AGENT_ROOT": str(LEGACY_ROOT)},
            )
            async with stdio_client(parameters) as (reader, writer):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    tools = [
                        {"name": tool.name, "description": (tool.description or "")[:240]}
                        for tool in response.tools
                    ]
            return {
                "status": "Ready",
                "detail": f"Legacy MCP responded with {len(tools)} tools",
                "tools": tools,
                "server": str(LEGACY_ROOT / "src" / "server.py"),
            }
        except Exception as exc:
            return {
                "status": "Warning",
                "detail": f"Legacy MCP could not start: {type(exc).__name__}: {exc}",
                "tools": [],
                "server": str(LEGACY_ROOT / "src" / "server.py"),
            }


legacy_mcp_bridge = LegacyMCPBridge()

