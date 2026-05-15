"""Fabric and host counter retrieval against running SONiC nodes.

Two tools, both pending Stage A Scout:

- ``get_fabric_counters`` — per-(switch, port, queue) counters with
  PG watermarks and per-priority PFC, matching Doppelgänger v0.3 §4.1
  SONiC-shape rollup (the rollup was modeled directly on SONiC's
  shape, so the substrate-substitution should be especially clean
  here). Source on SONiC: SSH per leaf/spine, run

      show interfaces counters detailed Ethernet<N>
      show queue counters Ethernet<N>
      show pfc counters
      show priority-group watermark shared
      show priority-group watermark headroom
      show priority-group persistent-watermark

  …or gNMI for structured JSON output if the text-CLI proves
  fragile to parse.

- ``get_host_counters`` — host-side ingress drop / PHY counters per
  Doppelgänger v0.3 §4.5. Source: SSH per host container, run
  ``ethtool -S <iface>``, parse.

The MCP-side contract is identical to Doppelgänger's; the data
source shifts from NS-3 emission files to live SONiC telemetry.
Doppelgänger v0.3's counter shape was itself modeled on SONiC, so
the parser work for ``get_fabric_counters`` should be modest.
"""

from __future__ import annotations

from typing import Any

from containerlab_adapter.driver.client import ContainerlabClient


def get_fabric_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Per-(switch, port, queue) fabric counters from running SONiC nodes.

    Pending Stage A Scout. SONiC's ``show queue counters`` and
    ``show priority-group watermark`` output shapes need empirical
    confirmation; some `show` commands are text-only and need
    parsing rules, others support `--json`. gNMI is the structured
    path if text-CLI parsing proves brittle. Per-queue / per-priority
    rollup logic mirrors Doppelgänger v0.3 §4.1.
    """
    raise NotImplementedError(
        "get_fabric_counters pending Stage A Scout — SONiC telemetry shape unconfirmed"
    )


def get_host_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Host-side ingress drop / PHY counters per Doppelgänger v0.3 §4.5.

    Pending Stage A Scout. Source is ``ethtool -S`` on each host
    container; field names depend on host image (containerlab default
    is ``ghcr.io/srl-labs/network-multitool``).
    """
    raise NotImplementedError(
        "get_host_counters pending Stage A Scout — host telemetry source unconfirmed"
    )
