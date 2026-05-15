"""``get_topology`` — return the fabric graph for a containerlab deployment.

Maps onto Doppelgänger v0.3 §4 / Architecture v0.6 §3.1 (the
``get_topology`` MCP tool). For containerlab, the topology comes from
``containerlab inspect`` plus the topology YAML (the YAML carries
declared neighbor relationships; inspect carries runtime state like
container IPs and MAC addresses).

Pending Stage A Scout. The exact mapping from containerlab's inspect
JSON onto HarnessIT's topology envelope needs real output to pin.
"""

from __future__ import annotations

from typing import Any

from containerlab_adapter.driver.client import ContainerlabClient


def get_topology(client: ContainerlabClient) -> dict[str, Any]:
    """Return the topology envelope for a deployed containerlab fabric.

    Pending Stage A Scout. The mapping from ``containerlab inspect``
    JSON (which includes container ids, IPs, MAC addresses) plus the
    topology YAML (which declares port-to-port adjacency) onto the
    contract's topology shape needs real responses to pin.
    """
    raise NotImplementedError(
        "get_topology pending Stage A Scout — see STAGE_A_SCOUT.md"
    )
