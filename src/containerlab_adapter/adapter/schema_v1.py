"""Translate native containerlab-adapter shapes to substrate-schema v1.1.

The native driver layer (``containerlab_adapter.driver``) emits shapes
inherited from Doppelgänger v0.3 §4.1 — parallel ``pfc_rx[8]`` /
``pfc_tx[8]`` arrays on each port record, ``pg_watermark_headroom[8]``
at port level, ``txq``/``pkts``/``drop_pkts`` naming on queue records.
Schema v1.1 normalizes to per-queue nested counters with renamed PFC
fields and per-queue ``pg_watermark_bytes``.

This module is the one-way mapping at the adapter boundary; the driver
keeps its native shape (the v0.1 corpus of agent prompts and tests
keys on it).
"""

from __future__ import annotations

import time
from typing import Any, Iterable

from substrate_schema import SCHEMA_VERSION

SUBSTRATE_KIND = "containerized"
ADAPTER_VERSION = "v0.2"

# Schema's accepted role enum (substrate_schema NodeRoleLiteral)
_VALID_ROLES = {"leaf", "spine", "host", "tor", "core", "unknown"}


def envelope_dict(
    *,
    source: str,
    observed_at_ns: int | None = None,
    staleness_class: str = "fresh",
    confidence: str = "high",
) -> dict[str, Any]:
    """Build a schema v1.1 envelope dict for a containerlab response.

    Defaults reflect containerlab-adapter's substrate properties:
    ``confidence`` is ``"high"`` (containerlab CLI + redis-cli are
    authoritative reads); ``staleness_class`` defaults to ``"fresh"``
    (each tool re-queries on demand against a live deployment).
    """
    return {
        "observed_at_ns": (
            observed_at_ns if observed_at_ns is not None else time.time_ns()
        ),
        "confidence": confidence,
        "staleness_class": staleness_class,
        "source": source,
        "substrate_kind": SUBSTRATE_KIND,
        "schema_version": SCHEMA_VERSION,
    }


# ---------------------------------------------------------- translate_topology

def _coerce_role(role: str | None) -> str:
    if role and role in _VALID_ROLES:
        return role
    return "unknown"


def translate_topology(
    native_payload: dict[str, Any],
    *,
    lab_name: str,
    observed_at_ns: int | None = None,
) -> dict[str, Any]:
    """Translate native ``get_topology`` payload to schema TopologyResponse.

    Native payload (``{shape, nodes, links, counts}``) is reshaped:

    * Each node keeps its short name; ``role`` is coerced to the schema
      enum; ``kind``, ``image``, ``mgmt_ipv4``, ``container_id``,
      ``state`` are dropped (substrate-specific extras, consumers
      ignore unknown fields per §2 principle 4, but the schema model
      only retains ``hwsku``/``asn``/``mgmt_ip``).
    * Native ``mgmt_ipv4`` → schema ``mgmt_ip``.
    * Native ``kind`` → schema ``hwsku`` (the closest SONiC-side
      analog of the SKU concept).
    * Links pass through unchanged — native already uses
      ``{node, port}`` endpoint shape.
    """
    nodes_out: list[dict[str, Any]] = []
    for native in native_payload.get("nodes") or []:
        node: dict[str, Any] = {
            "name": native.get("name") or "",
            "role": _coerce_role(native.get("role")),
        }
        if native.get("kind"):
            node["hwsku"] = native["kind"]
        if native.get("mgmt_ipv4"):
            node["mgmt_ip"] = native["mgmt_ipv4"]
        nodes_out.append(node)

    links_out: list[dict[str, Any]] = []
    for link in native_payload.get("links") or []:
        endpoints = link.get("endpoints") or []
        if len(endpoints) != 2:
            continue
        links_out.append({"endpoints": endpoints})

    return {
        "envelope": envelope_dict(
            source=f"containerlab.inspect+yaml({lab_name})",
            observed_at_ns=observed_at_ns,
            staleness_class="fresh",
        ),
        "topology": {"nodes": nodes_out, "links": links_out},
    }


# ---------------------------------------------------- translate_fabric_counters

def _translate_queue(
    txq_record: dict[str, Any],
    *,
    pfc_rx_at_q: int | None,
    pfc_tx_at_q: int | None,
    pg_watermark_at_q: int | None,
) -> dict[str, Any]:
    """Fold native txq record + parallel PFC/PG into per-queue schema shape.

    Native txq record carries volumetric counters (pkts/bytes/drop_pkts/
    drop_bytes); schema v1.1 PortQueue uses tx_packets/tx_bytes/
    dropped_packets. PFC sent/received counts and PG watermark are
    injected from the parallel arrays at the same q_index.
    """
    out: dict[str, Any] = {
        "q_index": txq_record["txq"],
    }
    if txq_record.get("pkts") is not None:
        out["tx_packets"] = txq_record["pkts"]
    if txq_record.get("bytes") is not None:
        out["tx_bytes"] = txq_record["bytes"]
    if txq_record.get("drop_pkts") is not None:
        out["dropped_packets"] = txq_record["drop_pkts"]
    if pfc_tx_at_q is not None:
        out["pfc_pause_sent_count"] = pfc_tx_at_q
    if pfc_rx_at_q is not None:
        out["pfc_pause_rcvd_count"] = pfc_rx_at_q
    if pg_watermark_at_q is not None:
        out["pg_watermark_bytes"] = pg_watermark_at_q
    return out


def _translate_port(native_port: dict[str, Any]) -> dict[str, Any]:
    """Translate one native port record into schema SwitchPort shape.

    Native ``rx``/``tx`` sub-dicts (ok/bps/util/err/drp/ovr) → schema
    port-level ``rx_packets`` / ``rx_errors`` / ``rx_discards`` etc.
    Bandwidth-rate fields (``bps``/``util``) are dropped — schema has
    no analog. Per-queue ``queues`` array is normalized; parallel
    ``pfc_rx[]``/``pfc_tx[]``/``pg_watermark_headroom[]`` are folded
    into the per-queue records at matching ``q_index``.
    """
    out: dict[str, Any] = {"name": native_port.get("port") or ""}
    state = native_port.get("state")
    if state in ("up", "down"):
        out["oper_status"] = state
        out["admin_status"] = state

    rx = native_port.get("rx") or {}
    tx = native_port.get("tx") or {}
    if rx.get("ok") is not None:
        out["rx_packets"] = rx["ok"]
    if rx.get("err") is not None:
        out["rx_errors"] = rx["err"]
    if rx.get("drp") is not None:
        out["rx_discards"] = rx["drp"]
    if tx.get("ok") is not None:
        out["tx_packets"] = tx["ok"]
    if tx.get("err") is not None:
        out["tx_errors"] = tx["err"]
    if tx.get("drp") is not None:
        out["tx_discards"] = tx["drp"]

    pfc_rx = native_port.get("pfc_rx") or []
    pfc_tx = native_port.get("pfc_tx") or []
    pg_wm = native_port.get("pg_watermark_headroom") or []

    queues_out: list[dict[str, Any]] = []
    for q in native_port.get("queues") or []:
        q_index = q["txq"]
        queues_out.append(
            _translate_queue(
                q,
                pfc_rx_at_q=pfc_rx[q_index] if q_index < len(pfc_rx) else None,
                pfc_tx_at_q=pfc_tx[q_index] if q_index < len(pfc_tx) else None,
                pg_watermark_at_q=(
                    pg_wm[q_index] if q_index < len(pg_wm) else None
                ),
            )
        )
    out["queues"] = queues_out
    return out


def translate_fabric_counters(
    native_ports: list[dict[str, Any]],
    *,
    lab_name: str,
    observed_at_ns: int | None = None,
) -> dict[str, Any]:
    """Translate native fabric counter records to schema FabricCountersResponse.

    Native is a flat list of per-(switch, port) records; schema groups
    by switch. Each port record's parallel ``pfc_rx[]`` / ``pfc_tx[]``
    / ``pg_watermark_headroom[]`` arrays are folded into the
    per-queue records inside ``queues``.
    """
    switches_by_name: dict[str, dict[str, Any]] = {}
    for native in native_ports:
        switch_name = native.get("switch") or ""
        record = switches_by_name.setdefault(
            switch_name, {"name": switch_name, "ports": []}
        )
        record["ports"].append(_translate_port(native))

    switches = [switches_by_name[name] for name in sorted(switches_by_name)]
    return {
        "envelope": envelope_dict(
            source=f"containerlab.fabric_counters({lab_name})",
            observed_at_ns=observed_at_ns,
            staleness_class="fresh",
        ),
        "switches": switches,
    }


# ------------------------------------------------------ translate_host_counters

def _sum_drops(stats: dict[str, Any], pattern: str) -> int | None:
    """Sum every stat whose key matches ``rx_queue_N_drops`` style pattern.

    Returns None when no matching stat exists (so the schema can omit
    rather than zero-fill, per §3.2 omit-not-zero rule).
    """
    total = 0
    found = False
    for key, value in stats.items():
        if pattern in key and isinstance(value, int):
            total += value
            found = True
    return total if found else None


def translate_host_counters(
    native_hosts: list[dict[str, Any]],
    *,
    lab_name: str,
    observed_at_ns: int | None = None,
) -> dict[str, Any]:
    """Translate native host counter records to schema HostCountersResponse.

    Native record is ``{host, interface, stats: {rx_queue_N_drops, ...}}``.
    Schema HostInterface accepts ``rx_packets`` / ``tx_packets`` /
    ``rx_drops`` / ``tx_drops`` / ``drops_per_million``. Map the common
    ethtool stat names; the bulk of substrate-specific stats are
    dropped (consumers can read them via the substrate-specific
    ``get_host_counters_native`` if exposed).

    ``rx_drops`` is the sum across all ``rx_queue_N_drops`` keys
    (parts of the silent-drops diagnostic surface per §4.5 of the
    legacy doc); ``rx_packets`` is the sum across ``rx_queue_N_packets``;
    similarly for tx.
    """
    hosts_by_name: dict[str, dict[str, Any]] = {}
    for native in native_hosts:
        host_name = native.get("host") or ""
        iface_name = native.get("interface") or "eth1"
        stats = native.get("stats") or {}

        iface: dict[str, Any] = {"name": iface_name}
        rx_packets = _sum_drops(stats, "rx_queue_") if False else stats.get(
            "rx_packets"
        )
        if isinstance(stats.get("rx_packets"), int):
            iface["rx_packets"] = stats["rx_packets"]
        if isinstance(stats.get("tx_packets"), int):
            iface["tx_packets"] = stats["tx_packets"]
        rx_drops = _sum_drops(stats, "rx_queue_") or _sum_drops(stats, "_drops")
        # Prefer canonical rx_dropped / rx_drop / rx_errors aggregate
        # if present; else the per-queue sum.
        if isinstance(stats.get("rx_dropped"), int):
            iface["rx_drops"] = stats["rx_dropped"]
        elif rx_drops is not None:
            iface["rx_drops"] = rx_drops
        if isinstance(stats.get("tx_dropped"), int):
            iface["tx_drops"] = stats["tx_dropped"]

        record = hosts_by_name.setdefault(
            host_name, {"name": host_name, "interfaces": []}
        )
        record["interfaces"].append(iface)

    hosts = [hosts_by_name[name] for name in sorted(hosts_by_name)]
    return {
        "envelope": envelope_dict(
            source=f"containerlab.host_counters({lab_name})",
            observed_at_ns=observed_at_ns,
            staleness_class="fresh",
        ),
        "hosts": hosts,
    }


# ------------------------------------------------------ translate_flow_records

def empty_flow_records(
    *,
    lab_name: str,
    observed_at_ns: int | None = None,
) -> dict[str, Any]:
    """Return a schema-conformant 'unsupported' FlowRecordsResponse.

    Per schema §3.3: substrates that cannot expose per-flow records
    return ``flows: []`` with ``envelope.confidence: "low"`` and
    ``envelope.staleness_class: "unsupported"``. containerlab-adapter
    v0.2 has no flow trace.
    """
    return {
        "envelope": envelope_dict(
            source=f"containerlab.flow_records({lab_name})",
            observed_at_ns=observed_at_ns,
            staleness_class="unsupported",
            confidence="low",
        ),
        "flows": [],
    }


# -------------------------------------------------------- translate_run_scenario

def translate_run_scenario(
    *,
    scenario_name: str,
    run_id: str,
    wall_clock_seconds: float,
    lab_name: str,
    observed_at_ns: int | None = None,
) -> dict[str, Any]:
    """Translate a containerlab deploy result to schema RunScenarioResponse."""
    if observed_at_ns is None:
        observed_at_ns = time.time_ns()
    wall_clock_ns = int(wall_clock_seconds * 1_000_000_000)
    return {
        "envelope": envelope_dict(
            source=f"containerlab.run_scenario({lab_name})",
            observed_at_ns=observed_at_ns,
            staleness_class="fresh",
        ),
        "run": {
            "scenario_name": scenario_name,
            "run_id": run_id,
            "status": "completed",
            "started_at_ns": observed_at_ns - wall_clock_ns,
            "completed_at_ns": observed_at_ns,
            "wall_clock_ns": wall_clock_ns,
        },
    }
