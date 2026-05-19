"""``get_topology`` — return the fabric graph for a containerlab deployment.

Implements the ``get_topology`` MCP tool from Doppelgänger v0.3 §2.2 /
Architecture v0.6 §3.1. Data comes from two sources:

- ``containerlab inspect`` — runtime state: which nodes are up, their
  management-network IPs, container IDs, kinds.
- The topology YAML at ``client.topology_path`` — declared link
  adjacency, which ``inspect`` does not surface.

The two are cross-referenced into a single payload keyed on short node
names (``leaf1``, not ``clab-hash-polarization-leaf1``). The lab name
appears only in the envelope's ``source`` field, never in per-node data
— per Doppelgänger §6.5 leak-prevention, scenario identifiers must not
echo back inside data records.
"""

from __future__ import annotations

import time
from typing import Any

import yaml

from containerlab_adapter.driver._node_utils import (
    ROLE_PREFIXES,
    classify_role,
    strip_lab_prefix,
)
from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.envelope import envelope


def _parse_endpoint(endpoint: str) -> dict[str, str]:
    """``spine1:eth1`` -> ``{"node": "spine1", "port": "eth1"}``."""
    node, _, port = endpoint.partition(":")
    return {"node": node, "port": port}


def get_topology(client: ContainerlabClient) -> dict[str, Any]:
    """Return the topology envelope for a deployed containerlab fabric.

    Raises :class:`ContainerlabError` if no lab is deployed (empty
    inspect output). The error surfaces the actual condition rather
    than returning an empty topology that would silently mislead the
    agent.
    """
    inspect_data = client.inspect()
    if not inspect_data:
        raise ContainerlabError(
            "containerlab inspect returned no deployed labs — "
            "deploy a topology before calling get_topology",
            cmd=["containerlab", "inspect"],
            returncode=0,
        )

    lab_name = next(iter(inspect_data))
    raw_nodes = inspect_data[lab_name]

    yaml_text = client.topology_path.read_text(encoding="utf-8")
    yaml_data = yaml.safe_load(yaml_text) or {}
    yaml_topology = yaml_data.get("topology", {}) or {}
    yaml_nodes = yaml_topology.get("nodes", {}) or {}
    yaml_links = yaml_topology.get("links", []) or []

    counts = {role: 0 for role in ROLE_PREFIXES}
    nodes: list[dict[str, Any]] = []
    for raw in raw_nodes:
        short_name = strip_lab_prefix(raw.get("name", ""), lab_name)
        declared = yaml_nodes.get(short_name, {}) or {}
        kind = raw.get("kind") or declared.get("kind") or "unknown"
        role = classify_role(short_name, kind)
        ipv4 = (raw.get("ipv4_address") or "").split("/")[0] or None
        nodes.append({
            "name": short_name,
            "role": role,
            "kind": kind,
            "image": raw.get("image"),
            "mgmt_ipv4": ipv4,
            "container_id": raw.get("container_id"),
            "state": raw.get("state"),
        })
        if role in counts:
            counts[role] += 1

    links: list[dict[str, Any]] = []
    for link in yaml_links:
        endpoints = link.get("endpoints", []) or []
        links.append({
            "endpoints": [_parse_endpoint(ep) for ep in endpoints],
        })

    shape = "leaf-spine" if counts["leaf"] and counts["spine"] else "unknown"

    payload = {
        "shape": shape,
        "nodes": nodes,
        "links": links,
        "counts": counts,
    }
    return envelope(
        payload,
        source=f"containerlab.inspect+yaml({lab_name})",
        observed_at_ns=time.time_ns(),
    )
