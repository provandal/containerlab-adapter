"""Hermetic tests for ``driver/counters.py``.

The orchestrator now reads COUNTERS_DB directly via a single
section-marker-delimited bash dump per switch, replacing the four
``show ...`` commands the v0.1 parsers consumed. Tests exercise:

  - ``parse_fabric_dump`` on synthetic + real-shape dump inputs.
  - ``get_fabric_counters`` end-to-end with ``exec_on_node`` mocked to
    return synthetic dumps for the three-switch hash-polarization
    topology — the same scout ``inspect.json`` is reused so the
    orchestrator's lab-discovery path stays exercised.
  - ``parse_ethtool_S`` + ``get_host_counters`` — unchanged from v0.1.
  - ``ContainerlabClient.exec_on_node`` transport branching — unchanged
    from v0.1, both docker-exec and sshpass-ssh paths.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.counters import (
    get_fabric_counters,
    get_host_counters,
    parse_ethtool_S,
    parse_fabric_dump,
)
from containerlab_adapter.scenarios import hash_polarization


REPO_ROOT = Path(__file__).resolve().parents[1]
SCOUT = REPO_ROOT / "scout-outputs"
SWITCHES = ("leaf1", "leaf2", "spine1")


# ============================================================
# parse_fabric_dump
# ============================================================

# A minimal synthetic dump exercising every section + the empty-hgetall
# edge case (one queue OID returns no fields, which is what we observed
# on the live vrnetlab/sonic_sonic-vs:20260520 substrate).
SYNTHETIC_DUMP = """\
===PORT_MAP===
Ethernet0
oid:0x1000000000002
===QUEUE_MAP===
Ethernet0:0
oid:0x15000000000006
Ethernet0:1
oid:0x15000000000007
===PG_MAP===
Ethernet0:0
oid:0x1a000000000047
Ethernet0:3
oid:0x1a00000000004a
===PORT_OPER===
---KEY:PORT_TABLE|Ethernet0---
admin_status
up
oper_status
up
speed
40000
===OIDS_BEGIN===
---OID:oid:0x1000000000002---
SAI_PORT_STAT_IF_IN_OCTETS
220
SAI_PORT_STAT_IF_IN_UCAST_PKTS
2
SAI_PORT_STAT_IF_IN_DISCARDS
10
SAI_PORT_STAT_IF_IN_ERRORS
0
SAI_PORT_STAT_IF_OUT_OCTETS
520
SAI_PORT_STAT_IF_OUT_UCAST_PKTS
4
SAI_PORT_STAT_IF_OUT_DISCARDS
0
SAI_PORT_STAT_IF_OUT_ERRORS
0
SAI_PORT_STAT_PFC_0_RX_PKTS
0
SAI_PORT_STAT_PFC_0_TX_PKTS
0
SAI_PORT_STAT_PFC_3_RX_PKTS
17
SAI_PORT_STAT_PFC_3_TX_PKTS
42
SAI_PORT_STAT_PFC_7_RX_PKTS
0
SAI_PORT_STAT_PFC_7_TX_PKTS
0
---OID:oid:0x15000000000006---
---OID:oid:0x15000000000007---
SAI_QUEUE_STAT_PACKETS
1234
SAI_QUEUE_STAT_BYTES
56789
SAI_QUEUE_STAT_DROPPED_PACKETS
7
SAI_QUEUE_STAT_DROPPED_BYTES
890
---OID:oid:0x1a000000000047---
SAI_INGRESS_PRIORITY_GROUP_STAT_DROPPED_PACKETS
0
SAI_INGRESS_PRIORITY_GROUP_STAT_SHARED_WATERMARK_BYTES
0
SAI_INGRESS_PRIORITY_GROUP_STAT_XOFF_ROOM_WATERMARK_BYTES
0
---OID:oid:0x1a00000000004a---
SAI_INGRESS_PRIORITY_GROUP_STAT_DROPPED_PACKETS
3
SAI_INGRESS_PRIORITY_GROUP_STAT_SHARED_WATERMARK_BYTES
1024
SAI_INGRESS_PRIORITY_GROUP_STAT_XOFF_ROOM_WATERMARK_BYTES
4096
===OIDS_END===
"""


class TestParseFabricDump:
    def test_port_map_parsed(self):
        d = parse_fabric_dump(SYNTHETIC_DUMP)
        assert d["port_map"] == {"Ethernet0": "oid:0x1000000000002"}

    def test_queue_map_parsed(self):
        d = parse_fabric_dump(SYNTHETIC_DUMP)
        assert d["queue_map"] == {
            "Ethernet0:0": "oid:0x15000000000006",
            "Ethernet0:1": "oid:0x15000000000007",
        }

    def test_pg_map_parsed(self):
        d = parse_fabric_dump(SYNTHETIC_DUMP)
        assert d["pg_map"] == {
            "Ethernet0:0": "oid:0x1a000000000047",
            "Ethernet0:3": "oid:0x1a00000000004a",
        }

    def test_port_oper_parsed(self):
        d = parse_fabric_dump(SYNTHETIC_DUMP)
        assert d["port_oper"]["Ethernet0"]["oper_status"] == "up"
        assert d["port_oper"]["Ethernet0"]["admin_status"] == "up"
        assert d["port_oper"]["Ethernet0"]["speed"] == "40000"

    def test_oid_hash_parsed_with_real_sai_keys(self):
        d = parse_fabric_dump(SYNTHETIC_DUMP)
        port = d["oids"]["oid:0x1000000000002"]
        assert port["SAI_PORT_STAT_IF_IN_OCTETS"] == "220"
        assert port["SAI_PORT_STAT_PFC_3_TX_PKTS"] == "42"

    def test_empty_oid_hash_present_with_no_fields(self):
        """Live substrate observation: some queue OIDs are registered in
        COUNTERS_QUEUE_NAME_MAP but their COUNTERS:oid:... hash returns
        no fields. The parser must surface that as an empty dict, not
        skip the OID — otherwise downstream code thinks the OID didn't
        exist at all."""
        d = parse_fabric_dump(SYNTHETIC_DUMP)
        assert "oid:0x15000000000006" in d["oids"]
        assert d["oids"]["oid:0x15000000000006"] == {}

    def test_empty_input_yields_empty_sections(self):
        d = parse_fabric_dump("")
        assert d == {
            "port_map": {},
            "queue_map": {},
            "pg_map": {},
            "port_oper": {},
            "oids": {},
        }

    def test_only_markers_no_data(self):
        """All sections present but every hgetall returned empty."""
        text = (
            "===PORT_MAP===\n"
            "===QUEUE_MAP===\n"
            "===PG_MAP===\n"
            "===PORT_OPER===\n"
            "===OIDS_BEGIN===\n"
            "===OIDS_END===\n"
        )
        d = parse_fabric_dump(text)
        for section in ("port_map", "queue_map", "pg_map", "port_oper", "oids"):
            assert d[section] == {}


# ============================================================
# get_fabric_counters orchestrator (hermetic, multi-switch)
# ============================================================

def _fake_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


def _synthetic_dump_for_switch(short: str) -> str:
    """Return a per-switch synthetic dump matching the hash-polarization
    topology shape (spine1 → 2 ports, leaf1/leaf2 → 3 ports each)."""
    ports = ["Ethernet0", "Ethernet4"] if short == "spine1" else \
            ["Ethernet0", "Ethernet4", "Ethernet8"]

    # Per-switch unique OID base to make merging detectable.
    oid_base = {"spine1": 0x100, "leaf1": 0x200, "leaf2": 0x300}[short]

    lines: list[str] = []
    lines.append("===PORT_MAP===")
    port_oids: dict[str, str] = {}
    for i, port in enumerate(ports):
        oid = f"oid:0x{oid_base + i:013x}"
        port_oids[port] = oid
        lines.append(port)
        lines.append(oid)

    lines.append("===QUEUE_MAP===")
    queue_oids: dict[str, str] = {}
    for port in ports:
        for q in range(2):  # two queues per port, keep fixture small
            alias = f"{port}:{q}"
            qoid = f"oid:0x{oid_base + 0x1000 + (ports.index(port) << 4) + q:013x}"
            queue_oids[alias] = qoid
            lines.append(alias)
            lines.append(qoid)

    lines.append("===PG_MAP===")
    pg_oids: dict[str, str] = {}
    for port in ports:
        for pg in range(2):
            alias = f"{port}:{pg}"
            poid = f"oid:0x{oid_base + 0x2000 + (ports.index(port) << 4) + pg:013x}"
            pg_oids[alias] = poid
            lines.append(alias)
            lines.append(poid)

    lines.append("===PORT_OPER===")
    for port in ports:
        lines.append(f"---KEY:PORT_TABLE|{port}---")
        lines.append("admin_status")
        lines.append("up")
        lines.append("oper_status")
        lines.append("up")

    lines.append("===OIDS_BEGIN===")
    for port, oid in port_oids.items():
        lines.append(f"---OID:{oid}---")
        lines.append("SAI_PORT_STAT_IF_IN_UCAST_PKTS")
        lines.append("100")
        lines.append("SAI_PORT_STAT_IF_OUT_UCAST_PKTS")
        lines.append("200")
        lines.append("SAI_PORT_STAT_IF_IN_DISCARDS")
        lines.append("0")
        lines.append("SAI_PORT_STAT_IF_IN_ERRORS")
        lines.append("0")
        lines.append("SAI_PORT_STAT_IF_OUT_DISCARDS")
        lines.append("0")
        lines.append("SAI_PORT_STAT_IF_OUT_ERRORS")
        lines.append("0")
        for p in range(8):
            lines.append(f"SAI_PORT_STAT_PFC_{p}_RX_PKTS")
            lines.append("0")
            lines.append(f"SAI_PORT_STAT_PFC_{p}_TX_PKTS")
            lines.append("0")
    for alias, qoid in queue_oids.items():
        lines.append(f"---OID:{qoid}---")
        lines.append("SAI_QUEUE_STAT_PACKETS")
        lines.append("50")
        lines.append("SAI_QUEUE_STAT_BYTES")
        lines.append("5000")
        lines.append("SAI_QUEUE_STAT_DROPPED_PACKETS")
        lines.append("0")
        lines.append("SAI_QUEUE_STAT_DROPPED_BYTES")
        lines.append("0")
    for alias, poid in pg_oids.items():
        lines.append(f"---OID:{poid}---")
        lines.append("SAI_INGRESS_PRIORITY_GROUP_STAT_XOFF_ROOM_WATERMARK_BYTES")
        lines.append("0")
    lines.append("===OIDS_END===")
    return "\n".join(lines) + "\n"


@pytest.fixture
def real_client() -> ContainerlabClient:
    return ContainerlabClient(topology_path=Path(hash_polarization.topology_path()))


@pytest.fixture
def inspect_stdout() -> str:
    return (REPO_ROOT / "scout-outputs" / "inspect.json").read_text(encoding="utf-8")


@pytest.fixture
def patched_fabric(real_client: ContainerlabClient, inspect_stdout: str):
    """Hermetic harness: inspect() returns the scout fixture; one
    exec_on_node call per switch returns the matching synthetic dump."""
    def exec_on_node_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        prefix = "clab-hash-polarization-"
        assert container_name.startswith(prefix), container_name
        short = container_name[len(prefix):]
        assert short in SWITCHES, (
            f"counters should only exec on switches; got {short!r}"
        )
        # The orchestrator's dump script is multi-line; verify a marker
        # echo is present rather than pinning every line.
        assert "===PORT_MAP===" in cmd
        return _synthetic_dump_for_switch(short)

    with patch.object(
        real_client, "inspect",
        return_value=json.loads(inspect_stdout),
    ), patch.object(
        real_client, "exec_on_node",
        side_effect=exec_on_node_side_effect,
    ) as exec_mock:
        env = get_fabric_counters(real_client)
        yield env, exec_mock


def test_fabric_envelope_has_required_fields(patched_fabric):
    env, _ = patched_fabric
    for key in ("data", "observed_at_ns", "source", "confidence", "staleness_class"):
        assert key in env


def test_fabric_lab_name_appears_in_source_not_data(patched_fabric):
    """§6.5: lab/scenario name in source field only, not in records."""
    env, _ = patched_fabric
    assert "hash-polarization" in env["source"]
    blob = json.dumps(env["data"])
    assert "hash-polarization" not in blob


def test_fabric_records_only_for_switches(patched_fabric):
    """Hosts are not surfaced by get_fabric_counters — that's the
    get_host_counters tool's job."""
    env, _ = patched_fabric
    switches_seen = {r["switch"] for r in env["data"]}
    assert switches_seen == {"leaf1", "leaf2", "spine1"}


def test_fabric_port_distribution_matches_topology(patched_fabric):
    """spine1 has 2 ports; leaf1/leaf2 have 3 each — 8 records total."""
    env, _ = patched_fabric
    by_switch: dict[str, list[str]] = {}
    for record in env["data"]:
        by_switch.setdefault(record["switch"], []).append(record["port"])
    for sw in by_switch:
        by_switch[sw].sort()
    assert by_switch["spine1"] == ["Ethernet0", "Ethernet4"]
    assert by_switch["leaf1"] == ["Ethernet0", "Ethernet4", "Ethernet8"]
    assert by_switch["leaf2"] == ["Ethernet0", "Ethernet4", "Ethernet8"]
    assert len(env["data"]) == 8


def test_fabric_record_carries_all_nested_sections(patched_fabric):
    env, _ = patched_fabric
    record = env["data"][0]
    for key in ("switch", "port", "state", "rx", "tx",
                "queues", "pfc_rx", "pfc_tx", "pg_watermark_headroom"):
        assert key in record, f"record missing {key!r}"
    assert isinstance(record["pfc_rx"], list) and len(record["pfc_rx"]) == 8
    assert isinstance(record["pfc_tx"], list) and len(record["pfc_tx"]) == 8
    assert isinstance(record["queues"], list)


def test_fabric_populated_values_surface_through(patched_fabric):
    """The synthetic dumps carry real numbers; the orchestrator
    surfaces them through the envelope unchanged."""
    env, _ = patched_fabric
    record = env["data"][0]
    assert record["rx"]["ok"] == 100
    assert record["tx"]["ok"] == 200
    assert record["state"] == "up"
    assert all(v == 0 for v in record["pfc_rx"])
    assert all(v == 0 for v in record["pfc_tx"])
    # Two queues per port in the synthetic fixture
    assert len(record["queues"]) == 2
    assert record["queues"][0]["txq"] == 0
    assert record["queues"][0]["pkts"] == 50


def test_fabric_pg_watermark_headroom_is_list_or_none(patched_fabric):
    """The PG section either surfaces a list (length 8, unset indices
    None) or None if no PG entries map to this port."""
    env, _ = patched_fabric
    record = env["data"][0]
    pg = record["pg_watermark_headroom"]
    assert pg is None or (isinstance(pg, list) and len(pg) == 8)
    if pg is not None:
        # Fixture populated PGs 0 and 1 → indices 0/1 are 0 (int), rest None
        assert pg[0] == 0
        assert pg[1] == 0
        assert all(v is None for v in pg[2:])


def test_fabric_exec_called_once_per_switch(patched_fabric):
    """Three switches × one redis-dump call = 3 exec_on_node calls.
    (v0.1 made four ``show`` calls per switch; the redesign batches.)"""
    _, exec_mock = patched_fabric
    assert exec_mock.call_count == 3


def test_fabric_no_inspect_raises(real_client: ContainerlabClient):
    """If no lab is deployed, fail loud — never return an empty
    counters payload that would look like 'fabric is silent'."""
    with patch.object(real_client, "inspect", return_value={}):
        with pytest.raises(ContainerlabError, match="no deployed labs"):
            get_fabric_counters(real_client)


def test_fabric_short_names_no_clab_prefix(patched_fabric):
    env, _ = patched_fabric
    for record in env["data"]:
        assert not record["switch"].startswith("clab-")


def test_fabric_empty_dump_yields_no_port_records_for_that_switch(
    real_client: ContainerlabClient, inspect_stdout: str,
):
    """A switch whose dump contains only section markers (no PORT_MAP
    entries) should contribute zero records — not a phantom row, not
    a crash. The other switches still produce records normally."""
    empty_dump = (
        "===PORT_MAP===\n"
        "===QUEUE_MAP===\n"
        "===PG_MAP===\n"
        "===PORT_OPER===\n"
        "===OIDS_BEGIN===\n"
        "===OIDS_END===\n"
    )

    def exec_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        if container_name.endswith("-spine1"):
            return empty_dump
        short = container_name.rsplit("-", 1)[-1]
        return _synthetic_dump_for_switch(short)

    with patch.object(real_client, "inspect", return_value=json.loads(inspect_stdout)), \
         patch.object(real_client, "exec_on_node", side_effect=exec_side_effect):
        env = get_fabric_counters(real_client)

    by_switch: dict[str, int] = {}
    for r in env["data"]:
        by_switch[r["switch"]] = by_switch.get(r["switch"], 0) + 1
    assert by_switch.get("spine1", 0) == 0  # empty dump → no records
    assert by_switch["leaf1"] == 3
    assert by_switch["leaf2"] == 3


# ============================================================
# ContainerlabClient.exec_on_node — transport branching (unchanged)
# ============================================================

@pytest.fixture
def fake_topology(tmp_path: Path) -> Path:
    p = tmp_path / "test.clab.yaml"
    p.write_text("name: test\ntopology:\n  nodes: {}\n", encoding="utf-8")
    return p


def test_exec_on_node_returns_stdout(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="hello\n")):
        out = client.exec_on_node("clab-test-node1", "echo hello")
    assert out == "hello\n"


def test_exec_on_node_invokes_docker_exec_bash_lc(fake_topology: Path):
    """The bash -lc wrapper is load-bearing — SONiC's ``show`` aliases
    only resolve under a login shell. Even though the redesign no
    longer calls ``show``, the wrapper stays uniform across both
    transport branches so future host-side commands behave consistently."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="x")) as runner:
        client.exec_on_node("clab-test-node1", "redis-cli ping")
    args = runner.call_args.args[0]
    assert args[0] == "docker"
    assert args[1] == "exec"
    assert args[2] == "clab-test-node1"
    assert args[3] == "bash"
    assert args[4] == "-lc"
    assert args[5] == "redis-cli ping"


def test_exec_on_node_raises_on_nonzero_exit(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(
        stdout="", stderr="container not found", returncode=1,
    )):
        with pytest.raises(ContainerlabError) as excinfo:
            client.exec_on_node("clab-test-missing", "redis-cli ping")
    assert excinfo.value.returncode == 1
    assert "container not found" in excinfo.value.stderr


def test_exec_on_node_raises_on_missing_docker_binary(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", side_effect=FileNotFoundError("docker")):
        with pytest.raises(ContainerlabError, match="docker binary not found"):
            client.exec_on_node("clab-test-node1", "echo hello")


def test_exec_on_node_sonic_vm_dispatches_to_sshpass_ssh(fake_topology: Path):
    """kind=sonic-vm routes through sshpass + ssh with admin@<mgmt_ip>."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="x")) as runner:
        client.exec_on_node(
            "clab-vrspike-sw1", "redis-cli -n 2 hgetall COUNTERS_PORT_NAME_MAP",
            kind="sonic-vm", mgmt_ip="172.101.101.2",
        )
    args = runner.call_args.args[0]
    assert args[0] == "sshpass"
    assert args[1] == "-p"
    assert args[2] == "admin"
    assert args[3] == "ssh"
    assert "admin@172.101.101.2" in args
    assert args[-1].startswith("bash -lc ")
    assert "redis-cli" in args[-1]


def test_exec_on_node_sonic_vm_without_mgmt_ip_raises(fake_topology: Path):
    """sonic-vm without mgmt_ip can't be reached. Fail loud at call site."""
    client = ContainerlabClient(topology_path=fake_topology)
    with pytest.raises(ContainerlabError, match="requires mgmt_ip"):
        client.exec_on_node(
            "clab-vrspike-sw1", "redis-cli ping",
            kind="sonic-vm", mgmt_ip=None,
        )


def test_exec_on_node_linux_kind_explicit_uses_docker_exec(fake_topology: Path):
    """kind=linux (or any non-sonic-vm value) routes to docker exec ...
    bash -lc, preserving the legacy behavior."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="x")) as runner:
        client.exec_on_node(
            "clab-vrspike-host1", "ethtool -S eth1",
            kind="linux", mgmt_ip="172.101.101.3",
        )
    args = runner.call_args.args[0]
    assert args[0] == "docker"
    assert args[1] == "exec"
    assert args[2] == "clab-vrspike-host1"
    assert args[3] == "bash"
    assert args[4] == "-lc"


def test_exec_on_node_sonic_vm_ssh_failure_raises(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(
        stdout="", stderr="Permission denied", returncode=255,
    )):
        with pytest.raises(ContainerlabError) as excinfo:
            client.exec_on_node(
                "clab-vrspike-sw1", "redis-cli ping",
                kind="sonic-vm", mgmt_ip="172.101.101.2",
            )
    assert excinfo.value.returncode == 255
    assert "ssh on" in str(excinfo.value)


def test_exec_on_node_sonic_vm_missing_sshpass_raises(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", side_effect=FileNotFoundError("sshpass")):
        with pytest.raises(ContainerlabError, match="sshpass binary not found"):
            client.exec_on_node(
                "clab-vrspike-sw1", "redis-cli ping",
                kind="sonic-vm", mgmt_ip="172.101.101.2",
            )


# ============================================================
# parse_ethtool_S (unchanged)
# ============================================================

class TestParseEthtoolS:
    def test_scout_capture_yields_expected_fields(self):
        text = (REPO_ROOT / "scout-outputs" / "host1-ethtool_S_eth1.txt").read_text(encoding="utf-8")
        stats = parse_ethtool_S(text)
        assert len(stats) == 21
        assert stats["peer_ifindex"] == 69
        assert stats["rx_queue_0_drops"] == 0  # load-bearing field for §4.5
        assert stats["rx_queue_0_xdp_packets"] == 0
        assert stats["tx_queue_0_xdp_xmit"] == 0

    def test_empty_string_yields_empty_dict(self):
        assert parse_ethtool_S("") == {}

    def test_header_only_yields_empty_dict(self):
        assert parse_ethtool_S("NIC statistics:\n") == {}

    def test_non_integer_value_round_trips_as_string(self):
        text = "NIC statistics:\n     link_state: up\n     speed_mbps: 10000\n"
        stats = parse_ethtool_S(text)
        assert stats["link_state"] == "up"
        assert stats["speed_mbps"] == 10000


# ============================================================
# get_host_counters (unchanged)
# ============================================================

ETHTOOL_FIXTURE = (REPO_ROOT / "scout-outputs" / "host1-ethtool_S_eth1.txt").read_text(encoding="utf-8")
HASH_POL_HOSTS = ("host1", "host2", "host3", "host4")


@pytest.fixture
def patched_hosts(real_client: ContainerlabClient, inspect_stdout: str):
    def exec_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        assert cmd == "ethtool -S eth1", f"unexpected cmd: {cmd!r}"
        prefix = "clab-hash-polarization-"
        assert container_name.startswith(prefix)
        return ETHTOOL_FIXTURE

    with patch.object(
        real_client, "inspect",
        return_value=json.loads(inspect_stdout),
    ), patch.object(
        real_client, "exec_on_node",
        side_effect=exec_side_effect,
    ) as exec_mock:
        env = get_host_counters(real_client)
        yield env, exec_mock


def test_host_envelope_has_required_fields(patched_hosts):
    env, _ = patched_hosts
    for key in ("data", "observed_at_ns", "source", "confidence", "staleness_class"):
        assert key in env


def test_host_records_only_for_hosts(patched_hosts):
    env, _ = patched_hosts
    hosts_seen = {r["host"] for r in env["data"]}
    assert hosts_seen == set(HASH_POL_HOSTS)


def test_host_records_count_matches_topology(patched_hosts):
    env, _ = patched_hosts
    assert len(env["data"]) == 4


def test_host_each_record_has_stats_dict(patched_hosts):
    env, _ = patched_hosts
    for record in env["data"]:
        assert "host" in record
        assert "interface" in record and record["interface"] == "eth1"
        assert isinstance(record["stats"], dict)
        assert "rx_queue_0_drops" in record["stats"]


def test_host_lab_name_in_source_not_data(patched_hosts):
    env, _ = patched_hosts
    assert "hash-polarization" in env["source"]
    assert "hash-polarization" not in json.dumps(env["data"])


def test_host_short_names_no_clab_prefix(patched_hosts):
    env, _ = patched_hosts
    for record in env["data"]:
        assert not record["host"].startswith("clab-")


def test_host_exec_called_once_per_host(patched_hosts):
    """Four hosts → four ethtool calls. No switches queried."""
    _, exec_mock = patched_hosts
    assert exec_mock.call_count == 4


def test_host_no_inspect_raises(real_client: ContainerlabClient):
    with patch.object(real_client, "inspect", return_value={}):
        with pytest.raises(ContainerlabError, match="no deployed labs"):
            get_host_counters(real_client)
