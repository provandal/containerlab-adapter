"""Fabric and host counter retrieval against running Cumulus VX nodes.

Two tools, both pending Stage A Scout:

- ``get_fabric_counters`` — per-(switch, port, queue) counters with
  PG watermarks and per-priority PFC, matching Doppelgänger v0.3 §4.1
  SONiC-shape rollup. Source on Cumulus VX: SSH per leaf/spine, run
  ``nv show qos buffer-pool --json``, ``cl-counters``, parse output.
- ``get_host_counters`` — host-side ingress drop / PHY counters per
  Doppelgänger v0.3 §4.5. Source: SSH per host container, run
  ``ethtool -S <iface>``, parse.

The MCP-side contract is identical to Doppelgänger's; the data
source shifts from NS-3 emission files to live Cumulus telemetry.
"""

from __future__ import annotations

from typing import Any

from containerlab_adapter.driver.client import ContainerlabClient


def get_fabric_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Per-(switch, port, queue) fabric counters from running Cumulus VX.

    Pending Stage A Scout. The Cumulus ``nv show qos buffer-pool
    --json`` output shape needs empirical confirmation; per-queue
    rollup may require additional commands (``cl-counters`` and
    ``ethtool -S`` per interface) and aggregation logic.
    """
    raise NotImplementedError(
        "get_fabric_counters pending Stage A Scout — Cumulus telemetry shape unknown"
    )


def get_host_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Host-side ingress drop / PHY counters per Doppelgänger v0.3 §4.5.

    Pending Stage A Scout. Source is ``ethtool -S`` on each host
    container; field names depend on host image (containerlab default
    is ``ghcr.io/srl-labs/network-multitool`` or similar).
    """
    raise NotImplementedError(
        "get_host_counters pending Stage A Scout — host telemetry source unconfirmed"
    )
