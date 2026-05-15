"""Adapter shell — MCP server exposing Driver methods as agent-facing tools.

Imports :mod:`containerlab_adapter.driver` and registers each public
function as an MCP tool with the same name HarnessIT expects from
Doppelgänger. The split (Driver vs Adapter) is per Architecture v0.6
§4.1 — the Adapter stays thin so the Driver remains reusable from
REPL/tests without MCP scaffolding.
"""

from containerlab_adapter.adapter.server import build_server

__all__ = ["build_server"]
