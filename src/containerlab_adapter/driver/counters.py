"""Fabric and host counter retrieval against running SONiC nodes.

``get_fabric_counters`` parses four SONiC ``show`` commands per switch
into per-(switch, port) records carrying nested rx/tx port counters,
queue-level rollups, per-priority PFC sent/rcvd, and PG watermarks —
matching Doppelgänger v0.3 §4.1.

**v0.1 scope.** The Stage-A Scout fixtures under ``scout-outputs/`` are
empty (no traffic flowed during the capture). The parsers correctly
extract the N/A shape and are structured to extend to populated rows;
the populated row format (BPS unit strings, percentage strings,
comma-separated integers) is captured defensively in ``_parse_value``
and re-validated against a live populated capture when scenario traffic
generation lands.

``get_host_counters`` still pending — needs the host-image
``ethtool -S`` field shape, which Stage A did not capture.
"""

from __future__ import annotations

import re
import time
from typing import Any, Iterator

from containerlab_adapter.driver._node_utils import (
    SWITCH_ROLES,
    classify_role,
    strip_lab_prefix,
)
from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.envelope import envelope


# ---------- low-level helpers ----------

_SUDO_NOT_FOUND = re.compile(r"^/bin/sh:\s*\d+:\s*sudo:\s*not found\s*$")
_PFC_COLUMNS = 8


def _clean_lines(text: str) -> Iterator[str]:
    """Yield trimmed lines from SONiC show-command output.

    Filters the benign ``/bin/sh: 1: sudo: not found`` warning the
    netreplica image emits to stderr — it can leak into stdout when
    captured by tools that merge streams (Stage A's scout captures did
    this with ``2>&1``), and parsers must be robust to its presence.
    """
    for line in text.splitlines():
        if _SUDO_NOT_FOUND.match(line):
            continue
        yield line.rstrip()


def _parse_value(token: str) -> Any:
    """Parse a single SONiC counter token.

    ``N/A`` collapses to None (not 0 — absence is data, distinct from
    a measured zero). Plain integers (with optional thousands
    separators) parse to int. Anything else round-trips as the raw
    string; this is the extension point for populated formats whose
    shape we have not yet seen in a real capture (e.g. ``"1.23 KB/s"``,
    ``"12.34%"``, link-state letters like ``"U"``).
    """
    token = token.strip()
    if token in ("N/A", "n/a", "NA", ""):
        return None
    no_commas = token.replace(",", "")
    if no_commas.lstrip("-").isdigit():
        return int(no_commas)
    return token


# ---------- per-table parsers ----------

def parse_interfaces_counters(text: str) -> list[dict[str, Any]]:
    """Parse ``show interfaces counters`` into per-port rx/tx dicts.

    The SONiC table has a 14-column shape: IFACE, STATE, then six
    RX_* and six TX_* columns. Returns one record per port row.
    """
    rows: list[dict[str, Any]] = []
    in_data = False
    for line in _clean_lines(text):
        if not line:
            continue
        if "IFACE" in line and "STATE" in line:
            in_data = False  # header found; data begins after dashes
            continue
        if line.lstrip().startswith("---"):
            in_data = True
            continue
        if not in_data:
            continue
        parts = line.split()
        # TODO populated capture: rows with BPS in "1.23 KB/s" form
        # split into 16 tokens; collapse the BPS pairs back to a single
        # token (or store {value, unit}) when we have a real populated
        # sample to validate against.
        if len(parts) != 14:
            continue
        rows.append({
            "port": parts[0],
            "state": _parse_value(parts[1]),
            "rx": {
                "ok":   _parse_value(parts[2]),
                "bps":  _parse_value(parts[3]),
                "util": _parse_value(parts[4]),
                "err":  _parse_value(parts[5]),
                "drp":  _parse_value(parts[6]),
                "ovr":  _parse_value(parts[7]),
            },
            "tx": {
                "ok":   _parse_value(parts[8]),
                "bps":  _parse_value(parts[9]),
                "util": _parse_value(parts[10]),
                "err":  _parse_value(parts[11]),
                "drp":  _parse_value(parts[12]),
                "ovr":  _parse_value(parts[13]),
            },
        })
    return rows


def parse_queue_counters(text: str) -> list[dict[str, Any]]:
    """Parse ``show queue counters`` into per-(port, txq) rows.

    The SONiC output is a sequence of identically-shaped tables (one
    per port-prefix-group in our scout-empty capture); rows are
    collected across all tables. Empty tables yield no rows.
    """
    rows: list[dict[str, Any]] = []
    in_data = False
    for line in _clean_lines(text):
        if not line:
            in_data = False
            continue
        if "Port" in line and "TxQ" in line:
            in_data = False
            continue
        if line.lstrip().startswith("---"):
            in_data = True
            continue
        if not in_data:
            continue
        parts = line.split()
        if len(parts) != 6:
            continue
        rows.append({
            "port": parts[0],
            "txq": _parse_value(parts[1]),
            "pkts": _parse_value(parts[2]),
            "bytes": _parse_value(parts[3]),
            "drop_pkts": _parse_value(parts[4]),
            "drop_bytes": _parse_value(parts[5]),
        })
    return rows


def parse_pfc_counters(text: str) -> dict[str, dict[str, list[Any]]]:
    """Parse ``show pfc counters`` (Rx and Tx tables) into per-port dicts.

    Returns ``{port: {"rx": [<PFC0..PFC7>], "tx": [<PFC0..PFC7>]}}``.
    Per-priority direction (Rx vs Tx) is load-bearing for DCQCN
    diagnosis (Doppelgänger §4.1) — aggregate "all PFC" counts erase
    the per-priority signal.
    """
    by_port: dict[str, dict[str, list[Any]]] = {}
    direction: str | None = None
    in_data = False
    for line in _clean_lines(text):
        if not line:
            in_data = False
            continue
        if "Port Rx" in line:
            direction = "rx"
            in_data = False
            continue
        if "Port Tx" in line:
            direction = "tx"
            in_data = False
            continue
        if line.lstrip().startswith("---"):
            in_data = True
            continue
        if not in_data or direction is None:
            continue
        parts = line.split()
        if len(parts) != 1 + _PFC_COLUMNS:
            continue
        port = parts[0]
        values = [_parse_value(p) for p in parts[1:1 + _PFC_COLUMNS]]
        slot = by_port.setdefault(port, {"rx": [None] * _PFC_COLUMNS,
                                        "tx": [None] * _PFC_COLUMNS})
        slot[direction] = values
    return by_port


def parse_pg_watermark_headroom(text: str) -> dict[str, Any]:
    """Parse ``show priority-group watermark headroom`` per-port.

    Stage A captured a degraded shape: only the Port column was
    rendered (no PG columns). For each port we observe, record the
    raw value list when present, else None. When live capture surfaces
    the populated multi-column shape this parser extends.
    """
    by_port: dict[str, Any] = {}
    in_data = False
    for line in _clean_lines(text):
        if not line:
            continue
        if line.strip() == "Port" or (
            "Port" in line and "PG" not in line and "TxQ" not in line
            and "Rx" not in line and "Tx" not in line
        ):
            in_data = False
            continue
        if line.lstrip().startswith("---"):
            in_data = True
            continue
        if not in_data:
            continue
        parts = line.split()
        if not parts or not parts[0].startswith("Ethernet"):
            continue
        port = parts[0]
        by_port[port] = (
            [_parse_value(p) for p in parts[1:]] if len(parts) > 1 else None
        )
    return by_port


# ---------- orchestrator ----------

_FABRIC_SHOW_COMMANDS = {
    "interfaces": "show interfaces counters",
    "queue":      "show queue counters",
    "pfc":        "show pfc counters",
    "pg_wm":      "show priority-group watermark headroom",
}

# Known-benign stderr signatures from `show priority-group watermark headroom`.
# The command exits non-zero when no buffer pool watermark counters are
# instantiated (a minimal config_db.json with no BUFFER_POOL_TABLE or
# BUFFER_PG_TABLE entries — exactly what the sonic-substrate-recipe ships
# for the 1-port reference). Substrate-Phase research note 2026-05-21.
# Treat these as "no PG data" rather than a fatal counter retrieval failure.
_PG_WATERMARK_EMPTY_MARKERS = (
    "Object map is empty",
    "No counter values found",
)


def _safe_pg_watermark_fetch(
    client: ContainerlabClient,
    container_name: str,
    **exec_kwargs,
) -> str:
    """Run the PG watermark show command, returning empty text if the
    command surfaces a known no-data condition. Other failures propagate
    unchanged so genuine transport or auth problems still fail loud."""
    try:
        return client.exec_on_node(
            container_name, _FABRIC_SHOW_COMMANDS["pg_wm"], **exec_kwargs
        )
    except ContainerlabError as exc:
        stderr = (exc.stderr or "") + (str(exc) or "")
        if any(marker in stderr for marker in _PG_WATERMARK_EMPTY_MARKERS):
            return ""
        raise


def get_fabric_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Snapshot per-(switch, port) fabric counters across all switches.

    Iterates every node containerlab reports as a switch (role ∈
    {leaf, spine}); for each switch runs the four show commands via
    ``client.exec_on_node`` and merges the outputs into a flat list of
    per-port records. Each record carries the switch's short name and
    the port identifier alongside nested rx/tx port counters, a
    queue-level list, per-priority PFC Rx+Tx vectors, and the PG
    headroom watermark.

    Raises :class:`ContainerlabError` if no lab is deployed.
    """
    inspect_data = client.inspect()
    if not inspect_data:
        raise ContainerlabError(
            "containerlab inspect returned no deployed labs — "
            "deploy a topology before calling get_fabric_counters",
            cmd=["containerlab", "inspect"],
            returncode=0,
        )

    lab_name, lab_records = next(iter(inspect_data.items()))

    records: list[dict[str, Any]] = []
    for raw in lab_records:
        container_name = raw.get("name", "")
        short_name = strip_lab_prefix(container_name, lab_name)
        kind = raw.get("kind") or ""
        role = classify_role(short_name, kind)
        if role not in SWITCH_ROLES:
            continue

        # mgmt_ip is required by the SSH-exec path for kind=sonic-vm.
        # Docker-exec kinds ignore it.
        mgmt_ip = (raw.get("ipv4_address") or "").split("/")[0] or None
        exec_kwargs = {"kind": kind, "mgmt_ip": mgmt_ip}

        iface_rows = parse_interfaces_counters(
            client.exec_on_node(container_name, _FABRIC_SHOW_COMMANDS["interfaces"], **exec_kwargs)
        )
        queue_rows = parse_queue_counters(
            client.exec_on_node(container_name, _FABRIC_SHOW_COMMANDS["queue"], **exec_kwargs)
        )
        pfc_by_port = parse_pfc_counters(
            client.exec_on_node(container_name, _FABRIC_SHOW_COMMANDS["pfc"], **exec_kwargs)
        )
        pg_by_port = parse_pg_watermark_headroom(
            _safe_pg_watermark_fetch(client, container_name, **exec_kwargs)
        )

        # Interfaces table is the index — it lists every port on the
        # switch even when counters are zero / N/A. Other tables merge
        # in by port. Ports that appear only in pfc/queue but not in
        # interfaces are dropped (would indicate a parser disagreement
        # worth surfacing, but v0.1 stays silent and lets the agent
        # see only the well-formed records).
        by_port: dict[str, dict[str, Any]] = {}
        for row in iface_rows:
            by_port[row["port"]] = {
                "switch": short_name,
                "port": row["port"],
                "state": row["state"],
                "rx": row["rx"],
                "tx": row["tx"],
                "queues": [],
                "pfc_rx": [None] * _PFC_COLUMNS,
                "pfc_tx": [None] * _PFC_COLUMNS,
                "pg_watermark_headroom": None,
            }
        for q in queue_rows:
            if q["port"] in by_port:
                by_port[q["port"]]["queues"].append({
                    "txq": q["txq"],
                    "pkts": q["pkts"],
                    "bytes": q["bytes"],
                    "drop_pkts": q["drop_pkts"],
                    "drop_bytes": q["drop_bytes"],
                })
        for port, pfc in pfc_by_port.items():
            if port in by_port:
                by_port[port]["pfc_rx"] = pfc["rx"]
                by_port[port]["pfc_tx"] = pfc["tx"]
        for port, pg in pg_by_port.items():
            if port in by_port:
                by_port[port]["pg_watermark_headroom"] = pg

        records.extend(by_port.values())

    return envelope(
        records,
        source=f"containerlab.fabric_counters({lab_name})",
        observed_at_ns=time.time_ns(),
    )


_ETHTOOL_HEADER = re.compile(r"^NIC statistics:\s*$")
_ETHTOOL_STAT = re.compile(r"^\s*(\S+):\s*(.+?)\s*$")
_HOST_DATA_INTERFACE = "eth1"


def parse_ethtool_S(text: str) -> dict[str, Any]:
    """Parse ``ethtool -S <iface>`` into a flat stat->value dict.

    Lines look like ``     rx_queue_0_drops: 0`` (six-space indent +
    key + colon + value). The ``NIC statistics:`` header is stripped.
    Integer values parse to int; anything else round-trips as string.
    Returns an empty dict if no stats follow the header — veth on some
    kernels emits no counters at all, and the agent should see that
    honestly rather than receive synthesized zeros.

    The field we most care about for fabric diagnosis is
    ``rx_queue_0_drops`` — host-side ingress drop, which is invisible
    to switch egress counters (Doppelgänger v0.3 §4.5).
    """
    stats: dict[str, Any] = {}
    for line in text.splitlines():
        if _ETHTOOL_HEADER.match(line):
            continue
        if not line.strip():
            continue
        match = _ETHTOOL_STAT.match(line)
        if not match:
            continue
        key, raw = match.group(1), match.group(2)
        stats[key] = _parse_value(raw)
    return stats


def get_host_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Snapshot per-host ``ethtool -S`` stats on the data interface.

    Iterates every node containerlab reports as a host (role=host);
    runs ``ethtool -S eth1`` on each via ``client.exec_on_node`` and
    parses the output. The data interface is hardcoded to ``eth1``
    for v0.1 — that's the convention our topology YAMLs follow (eth0
    is containerlab's mgmt network, eth1+ are data-plane links).
    Multi-interface hosts are a v0.2 extension.

    Raises :class:`ContainerlabError` if no lab is deployed.
    """
    inspect_data = client.inspect()
    if not inspect_data:
        raise ContainerlabError(
            "containerlab inspect returned no deployed labs — "
            "deploy a topology before calling get_host_counters",
            cmd=["containerlab", "inspect"],
            returncode=0,
        )

    lab_name, lab_records = next(iter(inspect_data.items()))

    records: list[dict[str, Any]] = []
    for raw in lab_records:
        container_name = raw.get("name", "")
        short_name = strip_lab_prefix(container_name, lab_name)
        kind = raw.get("kind") or ""
        role = classify_role(short_name, kind)
        if role != "host":
            continue

        mgmt_ip = (raw.get("ipv4_address") or "").split("/")[0] or None
        output = client.exec_on_node(
            container_name, f"ethtool -S {_HOST_DATA_INTERFACE}",
            kind=kind, mgmt_ip=mgmt_ip,
        )
        stats = parse_ethtool_S(output)
        records.append({
            "host": short_name,
            "interface": _HOST_DATA_INTERFACE,
            "stats": stats,
        })

    return envelope(
        records,
        source=f"containerlab.host_counters({lab_name})",
        observed_at_ns=time.time_ns(),
    )
