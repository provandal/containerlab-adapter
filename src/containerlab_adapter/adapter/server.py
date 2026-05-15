"""MCP server stub.

Pending Stage B. The MCP server registration shape will mirror
Doppelgänger's adapter — same tool names, same response envelope. The
implementation is held until the Driver methods themselves have real
implementations to expose.
"""

from __future__ import annotations

from typing import Any


def build_server() -> Any:
    """Construct the MCP server for the containerlab Adapter.

    Pending Stage B. Will register tools matching Doppelgänger v0.3
    §2.2: list_scenarios, run_scenario, get_topology, get_fabric_counters,
    get_flow_records, get_host_counters, compare_runs.
    """
    raise NotImplementedError(
        "build_server pending Stage B — Driver implementations come first"
    )
