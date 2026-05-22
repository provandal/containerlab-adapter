"""Fabric and host counter retrieval against running SONiC nodes.

``get_fabric_counters`` reads SONiC's COUNTERS_DB (Redis db 2) directly
via a single bash-batched ``redis-cli`` dump per switch, then composes
per-(switch, port) records matching Doppelgänger v0.3 §4.1.

**Why this bypasses ``show interfaces counters``.** On the
``vrnetlab/sonic_sonic-vs:20260520`` substrate the SAI port objects in
ASIC_DB and the COUNTERS_PORT_NAME_MAP that ``show`` iterates only
populate on a fresh ``swss`` restart — the cold-boot state we observed
had per-OID counters ticking but ``show`` returning header-only rows.
Reading COUNTERS_DB directly avoids that fragility AND gives us access
to the full per-priority PFC vector + per-queue + per-PG stats without
parsing five different table layouts.

The transport contract is unchanged: one ``client.exec_on_node`` call
per switch over the kind-specific shell (docker exec or sshpass ssh).
Authority over which Redis databases hold what lives in canonical SONiC
docs and is sticky:

  - db 2  COUNTERS_DB:  COUNTERS_PORT_NAME_MAP, COUNTERS_QUEUE_NAME_MAP,
                        COUNTERS_PG_NAME_MAP, plus ``COUNTERS:oid:0x*``
                        hashes carrying ``SAI_PORT_STAT_*`` /
                        ``SAI_QUEUE_STAT_*`` /
                        ``SAI_INGRESS_PRIORITY_GROUP_STAT_*`` fields.
  - db 6  STATE_DB:     ``PORT_TABLE|<port>`` operational state.

``get_host_counters`` is unchanged — ``ethtool -S eth1`` against each
host node, exactly as before.
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

_PFC_COLUMNS = 8
_PG_COLUMNS = 8


def _parse_int(token: str | None) -> int | None:
    """Parse a redis-cli integer cell. ``None``/missing → None."""
    if token is None:
        return None
    token = token.strip()
    if not token:
        return None
    no_commas = token.replace(",", "")
    if no_commas.lstrip("-").isdigit():
        return int(no_commas)
    return None


# ---------- the on-switch dump script ----------
#
# A single bash invocation that dumps everything the parser needs in
# one round-trip. Section markers are unambiguous (no SAI key starts
# with ``=``) so the parser can run as a flat state machine.
#
# The script uses ``sudo`` because the SONiC ``admin`` user can't read
# the Redis databases directly. The dispatch path through
# ``client.exec_on_node`` already wraps in ``bash -lc`` for the docker
# branch; the sonic-vm SSH branch does the same. Embedded single
# quotes inside printf would tangle with the outer shell wrap, so we
# use ``echo`` with safe literal strings only.
_FABRIC_DUMP_SCRIPT = r"""
set -u
echo '===PORT_MAP==='
sudo redis-cli -n 2 hgetall COUNTERS_PORT_NAME_MAP
echo '===QUEUE_MAP==='
sudo redis-cli -n 2 hgetall COUNTERS_QUEUE_NAME_MAP
echo '===PG_MAP==='
sudo redis-cli -n 2 hgetall COUNTERS_PG_NAME_MAP
echo '===PORT_OPER==='
for k in $(sudo redis-cli -n 6 keys 'PORT_TABLE|Ethernet*'); do
  echo "---KEY:${k}---"
  sudo redis-cli -n 6 hgetall "$k"
done
echo '===OIDS_BEGIN==='
for key in $(sudo redis-cli -n 2 --scan --pattern 'COUNTERS:oid:0x*'); do
  # Emit the marker as "oid:0x..." so it matches the values stored in
  # the name-map hashes (Redis stores them without the COUNTERS: prefix).
  echo "---OID:${key#COUNTERS:}---"
  sudo redis-cli -n 2 hgetall "$key"
done
echo '===OIDS_END==='
"""


# ---------- parser ----------

_SECTION_MARKER = re.compile(r"^===([A-Z_]+)===$")
_KEY_MARKER = re.compile(r"^---KEY:(.+)---$")
_OID_MARKER = re.compile(r"^---OID:(oid:0x[0-9a-fA-F]+)---$")


def parse_fabric_dump(text: str) -> dict[str, Any]:
    """Parse the bash-batched COUNTERS_DB dump.

    Returns a dict with four sub-dicts::

      {
        "port_map":  {"Ethernet0": "oid:0x1000000000002", ...},
        "queue_map": {"Ethernet0:0": "oid:0x15...", ...},
        "pg_map":    {"Ethernet0:0": "oid:0x1a...", ...},
        "port_oper": {"Ethernet0": {"oper_status": "up", ...}, ...},
        "oids":      {"oid:0x...": {"SAI_PORT_STAT_...": "10", ...}, ...},
      }

    Each ``hgetall`` block in the input is a sequence of alternating
    key/value lines; an empty hash produces no lines at all. The state
    machine handles both cases uniformly.
    """
    port_map: dict[str, str] = {}
    queue_map: dict[str, str] = {}
    pg_map: dict[str, str] = {}
    port_oper: dict[str, dict[str, str]] = {}
    oids: dict[str, dict[str, str]] = {}

    section: str | None = None
    current_key: str | None = None  # for PORT_OPER: PORT_TABLE|Ethernet0
    current_oid: str | None = None  # for OIDS:   oid:0x...
    buf: list[str] = []  # alternating-line buffer for current hash

    def flush_alternating(target: dict[str, str]) -> None:
        # Pair up alternating key/value lines into target.
        for i in range(0, len(buf) - 1, 2):
            target[buf[i]] = buf[i + 1]
        buf.clear()

    for raw in text.splitlines():
        line = raw.rstrip()

        sec = _SECTION_MARKER.match(line)
        if sec:
            # flush whatever was in progress
            if section == "PORT_MAP":
                flush_alternating(port_map)
            elif section == "QUEUE_MAP":
                flush_alternating(queue_map)
            elif section == "PG_MAP":
                flush_alternating(pg_map)
            elif section == "PORT_OPER" and current_key is not None:
                d: dict[str, str] = {}
                flush_alternating(d)
                # PORT_TABLE|Ethernet0 → Ethernet0
                short = current_key.split("|", 1)[1] if "|" in current_key else current_key
                port_oper[short] = d
                current_key = None
            elif section == "OIDS_BEGIN" and current_oid is not None:
                d2: dict[str, str] = {}
                flush_alternating(d2)
                oids[current_oid] = d2
                current_oid = None
            section = sec.group(1)
            continue

        if section == "PORT_OPER":
            km = _KEY_MARKER.match(line)
            if km:
                # close previous key
                if current_key is not None:
                    d = {}
                    flush_alternating(d)
                    short = current_key.split("|", 1)[1] if "|" in current_key else current_key
                    port_oper[short] = d
                current_key = km.group(1)
                continue
            if current_key is not None and line:
                buf.append(line)
            continue

        if section == "OIDS_BEGIN":
            om = _OID_MARKER.match(line)
            if om:
                # close previous OID
                if current_oid is not None:
                    d2 = {}
                    flush_alternating(d2)
                    oids[current_oid] = d2
                current_oid = om.group(1)
                continue
            if current_oid is not None and line:
                buf.append(line)
            continue

        if section in ("PORT_MAP", "QUEUE_MAP", "PG_MAP") and line:
            buf.append(line)

    # Final flush — last block in the dump has no trailing marker.
    if section == "PORT_OPER" and current_key is not None:
        d = {}
        flush_alternating(d)
        short = current_key.split("|", 1)[1] if "|" in current_key else current_key
        port_oper[short] = d
    elif section in ("OIDS_BEGIN",) and current_oid is not None:
        d2 = {}
        flush_alternating(d2)
        oids[current_oid] = d2

    return {
        "port_map": port_map,
        "queue_map": queue_map,
        "pg_map": pg_map,
        "port_oper": port_oper,
        "oids": oids,
    }


# ---------- SAI → envelope composer ----------

def _compose_port_record(
    switch_name: str,
    port_name: str,
    port_oid: str,
    dump: dict[str, Any],
) -> dict[str, Any]:
    """Build one per-(switch, port) record from the parsed dump.

    The shape is identical to the v0.1 envelope so existing agent
    prompts and Doppelgänger v0.3 §4.1 expectations stay valid.
    """
    port_counters = dump["oids"].get(port_oid, {})
    oper = dump["port_oper"].get(port_name, {})

    rx = {
        "ok":   _parse_int(port_counters.get("SAI_PORT_STAT_IF_IN_UCAST_PKTS")),
        "bps":  None,
        "util": None,
        "err":  _parse_int(port_counters.get("SAI_PORT_STAT_IF_IN_ERRORS")),
        "drp":  _parse_int(port_counters.get("SAI_PORT_STAT_IF_IN_DISCARDS")),
        "ovr":  None,
    }
    tx = {
        "ok":   _parse_int(port_counters.get("SAI_PORT_STAT_IF_OUT_UCAST_PKTS")),
        "bps":  None,
        "util": None,
        "err":  _parse_int(port_counters.get("SAI_PORT_STAT_IF_OUT_ERRORS")),
        "drp":  _parse_int(port_counters.get("SAI_PORT_STAT_IF_OUT_DISCARDS")),
        "ovr":  None,
    }
    pfc_rx = [
        _parse_int(port_counters.get(f"SAI_PORT_STAT_PFC_{i}_RX_PKTS"))
        for i in range(_PFC_COLUMNS)
    ]
    pfc_tx = [
        _parse_int(port_counters.get(f"SAI_PORT_STAT_PFC_{i}_TX_PKTS"))
        for i in range(_PFC_COLUMNS)
    ]

    # Queue rollups — filter the queue_map to entries that belong to this port.
    queues: list[dict[str, Any]] = []
    prefix = f"{port_name}:"
    for alias, qoid in dump["queue_map"].items():
        if not alias.startswith(prefix):
            continue
        try:
            txq = int(alias[len(prefix):])
        except ValueError:
            # Some platforms suffix UC/MC; skip — surface only canonical txq rows.
            continue
        qc = dump["oids"].get(qoid, {})
        queues.append({
            "txq":        txq,
            "pkts":       _parse_int(qc.get("SAI_QUEUE_STAT_PACKETS")),
            "bytes":      _parse_int(qc.get("SAI_QUEUE_STAT_BYTES")),
            "drop_pkts":  _parse_int(qc.get("SAI_QUEUE_STAT_DROPPED_PACKETS")),
            "drop_bytes": _parse_int(qc.get("SAI_QUEUE_STAT_DROPPED_BYTES")),
        })
    queues.sort(key=lambda q: q["txq"])

    # PG watermark headroom — one value per priority-group index 0..7.
    # Absent PG map entries surface as None at that position; if no PG
    # data exists for this port at all, surface None (matches v0.1 shape
    # for substrates with no buffer pool, e.g. the 1-port reference).
    pg_values: list[Any] = [None] * _PG_COLUMNS
    pg_found = False
    for alias, poid in dump["pg_map"].items():
        if not alias.startswith(prefix):
            continue
        try:
            pg_idx = int(alias[len(prefix):])
        except ValueError:
            continue
        if 0 <= pg_idx < _PG_COLUMNS:
            pgc = dump["oids"].get(poid, {})
            pg_values[pg_idx] = _parse_int(
                pgc.get("SAI_INGRESS_PRIORITY_GROUP_STAT_XOFF_ROOM_WATERMARK_BYTES")
            )
            pg_found = True
    pg_watermark_headroom: Any = pg_values if pg_found else None

    state = oper.get("oper_status") or oper.get("admin_status")
    return {
        "switch": switch_name,
        "port": port_name,
        "state": state,
        "rx": rx,
        "tx": tx,
        "queues": queues,
        "pfc_rx": pfc_rx,
        "pfc_tx": pfc_tx,
        "pg_watermark_headroom": pg_watermark_headroom,
    }


# ---------- orchestrator ----------

def get_fabric_counters(client: ContainerlabClient) -> dict[str, Any]:
    """Snapshot per-(switch, port) fabric counters across all switches.

    Iterates every node containerlab reports as a switch (role ∈
    SWITCH_ROLES); for each switch executes the single bash dump script
    via ``client.exec_on_node``, parses the dump, and composes
    per-(switch, port) records keyed off COUNTERS_PORT_NAME_MAP.

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

        mgmt_ip = (raw.get("ipv4_address") or "").split("/")[0] or None
        dump_text = client.exec_on_node(
            container_name,
            _FABRIC_DUMP_SCRIPT,
            kind=kind,
            mgmt_ip=mgmt_ip,
        )
        dump = parse_fabric_dump(dump_text)

        for port_name, port_oid in dump["port_map"].items():
            records.append(_compose_port_record(short_name, port_name, port_oid, dump))

    return envelope(
        records,
        source=f"containerlab.fabric_counters({lab_name})",
        observed_at_ns=time.time_ns(),
    )


# ---------- host counters (unchanged) ----------

_ETHTOOL_HEADER = re.compile(r"^NIC statistics:\s*$")
_ETHTOOL_STAT = re.compile(r"^\s*(\S+):\s*(.+?)\s*$")
_HOST_DATA_INTERFACE = "eth1"


def _parse_ethtool_token(token: str) -> Any:
    token = token.strip()
    if token in ("N/A", "n/a", "NA", ""):
        return None
    no_commas = token.replace(",", "")
    if no_commas.lstrip("-").isdigit():
        return int(no_commas)
    return token


def parse_ethtool_S(text: str) -> dict[str, Any]:
    """Parse ``ethtool -S <iface>`` into a flat stat→value dict.

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
        stats[key] = _parse_ethtool_token(raw)
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
