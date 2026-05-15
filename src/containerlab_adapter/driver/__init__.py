"""Driver — subprocess wrapper around containerlab CLI + SSH to Cumulus nodes.

Public surface mirrors Doppelgänger's MCP tool surface (Doppelgänger
v0.3 §2.2). The :class:`ContainerlabClient` is the substrate-facing
plumbing (deploy/destroy/inspect via ``containerlab`` CLI). Each
agent-facing tool function takes a client and returns the response
envelope HarnessIT expects.

The ``ContainerlabClient`` subprocess wrappers are implemented
concretely — they're honest CLI calls. The higher-level tool
functions (``get_topology``, ``get_fabric_counters``, etc.) raise
``NotImplementedError("pending Stage A Scout observations")`` until
real Cumulus VX telemetry shapes the parsers.
"""

from containerlab_adapter.driver.client import ContainerlabClient
from containerlab_adapter.driver.counters import get_fabric_counters, get_host_counters
from containerlab_adapter.driver.flows import get_flow_records
from containerlab_adapter.driver.scenarios import list_scenarios, run_scenario
from containerlab_adapter.driver.topology import get_topology

__all__ = [
    "ContainerlabClient",
    "get_fabric_counters",
    "get_flow_records",
    "get_host_counters",
    "get_topology",
    "list_scenarios",
    "run_scenario",
]
