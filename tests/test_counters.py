"""Hermetic tests for ``driver/counters.py``.

Per-table parsers are exercised against the raw scout-output text
files (all empty/N/A — the smoke capture had no traffic). The
orchestrator ``get_fabric_counters`` is exercised by patching
``ContainerlabClient.exec_on_node`` + ``ContainerlabClient.inspect`` to
return scout-derived strings so the full call chain runs without
docker or containerlab installed.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.counters import (
    get_fabric_counters,
    get_host_counters,
    parse_ethtool_S,
    parse_interfaces_counters,
    parse_pfc_counters,
    parse_pg_watermark_headroom,
    parse_queue_counters,
)
from containerlab_adapter.scenarios import hash_polarization


REPO_ROOT = Path(__file__).resolve().parents[1]
SCOUT = REPO_ROOT / "scout-outputs"
SWITCHES = ("leaf1", "leaf2", "spine1")


def _scout(node: str, command_filename: str) -> str:
    """Read a scout fixture file."""
    return (SCOUT / f"{node}-{command_filename}").read_text(encoding="utf-8")


# ============================================================
# parse_interfaces_counters
# ============================================================

class TestParseInterfacesCounters:
    def test_empty_table_yields_three_ports(self):
        """hash-polarization uses eth1-eth3 on each switch — SONiC
        reports those as Ethernet0/4/8 in the counters table."""
        rows = parse_interfaces_counters(_scout("leaf1", "show_interfaces_counters.txt"))
        ports = [r["port"] for r in rows]
        assert ports == ["Ethernet0", "Ethernet4", "Ethernet8"]

    def test_empty_values_are_none_not_zero(self):
        """N/A means 'no measurement' — distinct from a measured 0.
        Conflating the two would let an agent reason about absent data
        as if it were observed silence."""
        rows = parse_interfaces_counters(_scout("leaf1", "show_interfaces_counters.txt"))
        assert rows[0]["state"] is None
        for direction in ("rx", "tx"):
            for field in ("ok", "bps", "util", "err", "drp", "ovr"):
                assert rows[0][direction][field] is None, (
                    f"{direction}.{field} should be None for empty fixture, "
                    f"got {rows[0][direction][field]!r}"
                )

    def test_sudo_warning_line_ignored(self):
        """The /bin/sh: 1: sudo: not found warning is benign noise the
        netreplica image emits; it must never become a parsed row."""
        text = "/bin/sh: 1: sudo: not found\n" + _scout("leaf1", "show_interfaces_counters.txt")
        rows = parse_interfaces_counters(text)
        assert len(rows) == 3  # no extra ghost row from the warning

    def test_populated_value_round_trips_as_int(self):
        """Synthesized populated row (since scout-outputs are empty):
        comma-separated integers parse to int."""
        synthetic = (
            "    IFACE    STATE    RX_OK    RX_BPS    RX_UTIL    RX_ERR    RX_DRP    RX_OVR    TX_OK    TX_BPS    TX_UTIL    TX_ERR    TX_DRP    TX_OVR\n"
            "---------  -------  -------  --------  ---------  --------  --------  --------  -------  --------  ---------  --------  --------  --------\n"
            "Ethernet0      U      1234         0          0         0         0         0     5678         0          0         0         0         0\n"
        )
        rows = parse_interfaces_counters(synthetic)
        assert rows[0]["state"] == "U"
        assert rows[0]["rx"]["ok"] == 1234
        assert rows[0]["tx"]["ok"] == 5678
        assert rows[0]["rx"]["err"] == 0


# ============================================================
# parse_queue_counters
# ============================================================

class TestParseQueueCounters:
    def test_empty_tables_yield_no_rows(self):
        """Triple-empty-table format produces no records — the agent
        sees an empty list, not phantom rows."""
        rows = parse_queue_counters(_scout("leaf1", "show_queue_counters.txt"))
        assert rows == []

    def test_synthetic_populated_row_parses(self):
        synthetic = (
            "  Port    TxQ    Counter/pkts    Counter/bytes    Drop/pkts    Drop/bytes\n"
            "------  -----  --------------  ---------------  -----------  ------------\n"
            "Ethernet0   3            1234            56789           7           890\n"
        )
        rows = parse_queue_counters(synthetic)
        assert len(rows) == 1
        assert rows[0] == {
            "port": "Ethernet0",
            "txq": 3,
            "pkts": 1234,
            "bytes": 56789,
            "drop_pkts": 7,
            "drop_bytes": 890,
        }


# ============================================================
# parse_pfc_counters
# ============================================================

class TestParsePfcCounters:
    def test_empty_per_port_dict_has_rx_and_tx_lists(self):
        """Both Rx and Tx tables are present in scout output; each
        port gets both direction vectors even when all N/A."""
        by_port = parse_pfc_counters(_scout("leaf1", "show_pfc_counters.txt"))
        assert set(by_port.keys()) == {"Ethernet0", "Ethernet4", "Ethernet8"}
        for port, slots in by_port.items():
            assert "rx" in slots and "tx" in slots
            assert len(slots["rx"]) == 8 and len(slots["tx"]) == 8
            assert all(v is None for v in slots["rx"])
            assert all(v is None for v in slots["tx"])

    def test_synthetic_populated_pfc_separates_rx_and_tx(self):
        synthetic = (
            "  Port Rx    PFC0    PFC1    PFC2    PFC3    PFC4    PFC5    PFC6    PFC7\n"
            "---------  ------  ------  ------  ------  ------  ------  ------  ------\n"
            "Ethernet0       1       2       3       4       5       6       7       8\n"
            "\n"
            "  Port Tx    PFC0    PFC1    PFC2    PFC3    PFC4    PFC5    PFC6    PFC7\n"
            "---------  ------  ------  ------  ------  ------  ------  ------  ------\n"
            "Ethernet0     100     200     300     400     500     600     700     800\n"
        )
        by_port = parse_pfc_counters(synthetic)
        assert by_port["Ethernet0"]["rx"] == [1, 2, 3, 4, 5, 6, 7, 8]
        assert by_port["Ethernet0"]["tx"] == [100, 200, 300, 400, 500, 600, 700, 800]


# ============================================================
# parse_pg_watermark_headroom
# ============================================================

class TestParsePgWatermarkHeadroom:
    def test_degraded_scout_shape_returns_port_keys_with_none(self):
        """Scout captured only the port column (PG data columns
        absent). Parser surfaces the port keys with None values rather
        than dropping the ports silently."""
        by_port = parse_pg_watermark_headroom(_scout("leaf1", "show_priority-group_watermark_headroom.txt"))
        assert set(by_port.keys()) == {"Ethernet0", "Ethernet4", "Ethernet8"}
        for port, value in by_port.items():
            assert value is None


# ============================================================
# get_fabric_counters orchestrator
# ============================================================

def _fake_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


@pytest.fixture
def real_client() -> ContainerlabClient:
    return ContainerlabClient(topology_path=Path(hash_polarization.topology_path()))


@pytest.fixture
def inspect_stdout() -> str:
    return (REPO_ROOT / "scout-outputs" / "inspect.json").read_text(encoding="utf-8")


def _scout_show_router(node_short: str):
    """Return a function mimicking exec_on_node — routes commands to
    the matching scout fixture for the given short node name."""
    mapping = {
        "show interfaces counters": _scout(node_short, "show_interfaces_counters.txt"),
        "show queue counters": _scout(node_short, "show_queue_counters.txt"),
        "show pfc counters": _scout(node_short, "show_pfc_counters.txt"),
        "show priority-group watermark headroom": _scout(
            node_short, "show_priority-group_watermark_headroom.txt"
        ),
    }
    def _router(cmd: str) -> str:
        if cmd not in mapping:
            raise AssertionError(f"unexpected exec command: {cmd!r}")
        return mapping[cmd]
    return _router


@pytest.fixture
def patched_fabric(real_client: ContainerlabClient, inspect_stdout: str):
    """Set up the full hermetic harness:
    - inspect() returns the scout inspect.json
    - exec_on_node(container, cmd) routes by short-node-name to scout fixtures
    """
    def exec_on_node_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        # counters now passes kind + mgmt_ip kwargs through to exec_on_node so
        # the SSH branch can fire for kind=sonic-vm. The hermetic test doesn't
        # exercise the transport layer (we patch exec_on_node itself), so the
        # kwargs are accepted and ignored — but the side_effect must accept
        # them or MagicMock raises TypeError.
        prefix = "clab-hash-polarization-"
        assert container_name.startswith(prefix), container_name
        short = container_name[len(prefix):]
        if short not in SWITCHES:
            raise AssertionError(f"counters should only exec on switches; got {short!r}")
        return _scout_show_router(short)(cmd)

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
    get_host_counters tool's job. Records are switch-keyed only."""
    env, _ = patched_fabric
    switches_seen = {r["switch"] for r in env["data"]}
    assert switches_seen == {"leaf1", "leaf2", "spine1"}


def test_fabric_port_distribution_matches_topology(patched_fabric):
    """spine1 has 2 ports (uplinks to leaf1/leaf2); each leaf has 3
    (spine + 2 hosts). Counts come from the SONiC interface tables,
    not a uniform per-switch assumption — total 8 records."""
    env, _ = patched_fabric
    by_switch: dict[str, list[str]] = {}
    for record in env["data"]:
        by_switch.setdefault(record["switch"], []).append(record["port"])
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


def test_fabric_empty_values_surface_as_none(patched_fabric):
    """Empty-table fixtures → None throughout, not 0 — distinguishes
    'no measurement' from 'measured zero'."""
    env, _ = patched_fabric
    for record in env["data"]:
        assert record["state"] is None
        for field in record["rx"].values():
            assert field is None
        for field in record["tx"].values():
            assert field is None
        assert all(v is None for v in record["pfc_rx"])
        assert all(v is None for v in record["pfc_tx"])


def test_fabric_exec_called_four_times_per_switch(patched_fabric):
    """Three switches × four show commands = 12 exec_on_node calls."""
    _, exec_mock = patched_fabric
    assert exec_mock.call_count == 12


def test_fabric_no_inspect_raises(real_client: ContainerlabClient):
    """If no lab is deployed, fail loud — never return an empty
    counters payload that would look like 'fabric is silent'."""
    with patch.object(real_client, "inspect", return_value={}):
        with pytest.raises(ContainerlabError, match="no deployed labs"):
            get_fabric_counters(real_client)


def test_fabric_short_names_no_clab_prefix(patched_fabric):
    """Record 'switch' field is the short name (leaf1, not
    clab-hash-polarization-leaf1) — §6.5 prefix strip."""
    env, _ = patched_fabric
    for record in env["data"]:
        assert not record["switch"].startswith("clab-")


# ============================================================
# ContainerlabClient.exec_on_node
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
    """The bash -lc wrapper is load-bearing — SONiC's 'show' aliases
    only resolve under a login shell. If this regresses, scout-style
    captures stop working live."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="x")) as runner:
        client.exec_on_node("clab-test-node1", "show pfc counters")
    args = runner.call_args.args[0]
    assert args[0] == "docker"
    assert args[1] == "exec"
    assert args[2] == "clab-test-node1"
    assert args[3] == "bash"
    assert args[4] == "-lc"
    assert args[5] == "show pfc counters"


def test_exec_on_node_raises_on_nonzero_exit(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(
        stdout="", stderr="container not found", returncode=1,
    )):
        with pytest.raises(ContainerlabError) as excinfo:
            client.exec_on_node("clab-test-missing", "show interfaces counters")
    assert excinfo.value.returncode == 1
    assert "container not found" in excinfo.value.stderr


def test_exec_on_node_raises_on_missing_docker_binary(fake_topology: Path):
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", side_effect=FileNotFoundError("docker")):
        with pytest.raises(ContainerlabError, match="docker binary not found"):
            client.exec_on_node("clab-test-node1", "echo hello")


def test_exec_on_node_sonic_vm_dispatches_to_sshpass_ssh(fake_topology: Path):
    """kind=sonic-vm routes through sshpass + ssh with admin@<mgmt_ip>.
    The cmd is wrapped in bash -lc so SONiC show-aliases resolve, matching
    the docker-exec path's login-shell behavior."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="x")) as runner:
        client.exec_on_node(
            "clab-vrspike-sw1", "show interfaces counters",
            kind="sonic-vm", mgmt_ip="172.101.101.2",
        )
    args = runner.call_args.args[0]
    assert args[0] == "sshpass"
    assert args[1] == "-p"
    assert args[2] == "admin"
    assert args[3] == "ssh"
    assert "admin@172.101.101.2" in args
    # the wrapped command should be the last positional arg and reference bash -lc
    assert args[-1].startswith("bash -lc ")
    assert "show interfaces counters" in args[-1]


def test_exec_on_node_sonic_vm_without_mgmt_ip_raises(fake_topology: Path):
    """sonic-vm without mgmt_ip can't be reached. Fail loud at call site
    rather than letting ssh fail with a confusing 'no host' error."""
    client = ContainerlabClient(topology_path=fake_topology)
    with pytest.raises(ContainerlabError, match="requires mgmt_ip"):
        client.exec_on_node(
            "clab-vrspike-sw1", "show interfaces counters",
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
    """Non-zero exit from ssh surfaces as ContainerlabError with the
    transport labeled correctly ('ssh' not 'docker exec') so the agent
    sees which transport failed."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(
        stdout="", stderr="Permission denied", returncode=255,
    )):
        with pytest.raises(ContainerlabError) as excinfo:
            client.exec_on_node(
                "clab-vrspike-sw1", "show interfaces counters",
                kind="sonic-vm", mgmt_ip="172.101.101.2",
            )
    assert excinfo.value.returncode == 255
    assert "ssh on" in str(excinfo.value)


def test_pg_watermark_empty_object_map_swallowed_as_empty(real_client, inspect_stdout):
    """The vrnetlab/sonic_sonic-vs substrate's minimal config_db.json has
    no BUFFER_POOL_TABLE, so `show priority-group watermark headroom`
    exits non-zero with 'Object map is empty!' on stderr. The fabric
    counters tool must treat that as 'no PG data' (None per port), not a
    fatal failure — other counters from the same switch are still useful."""
    from containerlab_adapter.driver.client import ContainerlabError

    populated_iface = (
        "    IFACE    STATE    RX_OK    RX_BPS    RX_UTIL    RX_ERR    RX_DRP    RX_OVR    TX_OK    TX_BPS    TX_UTIL    TX_ERR    TX_DRP    TX_OVR\n"
        "---------  -------  -------  --------  ---------  --------  --------  --------  -------  --------  ---------  --------  --------  --------\n"
        "Ethernet0      U        50         0          0         0         0         0      100         0          0         0         0         0\n"
    )

    def exec_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        if cmd == "show priority-group watermark headroom":
            raise ContainerlabError(
                "ssh on 'clab-x-leaf1' failed (exit code 1)",
                cmd=["ssh", "admin@x", "bash -lc ..."],
                returncode=1,
                stderr="Object map is empty!\n",
            )
        if cmd == "show interfaces counters":
            return populated_iface
        return ""  # empty queue + pfc

    with patch.object(real_client, "inspect", return_value=json.loads(inspect_stdout)), \
         patch.object(real_client, "exec_on_node", side_effect=exec_side_effect):
        env = get_fabric_counters(real_client)

    # Per-switch records still populate; PG watermark column is None.
    assert env["data"]
    for record in env["data"]:
        assert record["pg_watermark_headroom"] is None
        assert record["rx"]["ok"] == 50  # interfaces counters made it through


def test_pg_watermark_unknown_failure_propagates(real_client, inspect_stdout):
    """Only the known-benign 'Object map is empty' shape is swallowed.
    Other ssh / transport failures must still fail loud — otherwise we'd
    silently mask real broken-switch conditions."""
    from containerlab_adapter.driver.client import ContainerlabError

    def exec_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        if cmd == "show priority-group watermark headroom":
            raise ContainerlabError(
                "ssh failed", cmd=[], returncode=255,
                stderr="Permission denied (publickey,password).\n",
            )
        return ""

    with patch.object(real_client, "inspect", return_value=json.loads(inspect_stdout)), \
         patch.object(real_client, "exec_on_node", side_effect=exec_side_effect):
        with pytest.raises(ContainerlabError) as excinfo:
            get_fabric_counters(real_client)
    assert excinfo.value.returncode == 255
    assert "Permission denied" in excinfo.value.stderr


def test_exec_on_node_sonic_vm_missing_sshpass_raises(fake_topology: Path):
    """If sshpass isn't installed, surface that explicitly rather than
    falling back silently or emitting a cryptic stack."""
    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", side_effect=FileNotFoundError("sshpass")):
        with pytest.raises(ContainerlabError, match="sshpass binary not found"):
            client.exec_on_node(
                "clab-vrspike-sw1", "show interfaces counters",
                kind="sonic-vm", mgmt_ip="172.101.101.2",
            )


# ============================================================
# parse_ethtool_S
# ============================================================

class TestParseEthtoolS:
    def test_scout_capture_yields_expected_fields(self):
        """The captured veth ethtool output has peer_ifindex + 20
        zero-valued counters. All fields parse cleanly."""
        text = (REPO_ROOT / "scout-outputs" / "host1-ethtool_S_eth1.txt").read_text(encoding="utf-8")
        stats = parse_ethtool_S(text)
        assert len(stats) == 21
        assert stats["peer_ifindex"] == 69
        assert stats["rx_queue_0_drops"] == 0  # the load-bearing field for §4.5
        assert stats["rx_queue_0_xdp_packets"] == 0
        assert stats["tx_queue_0_xdp_xmit"] == 0

    def test_empty_string_yields_empty_dict(self):
        assert parse_ethtool_S("") == {}

    def test_header_only_yields_empty_dict(self):
        """Some kernels emit only the 'NIC statistics:' header with no
        counters. Surface that as empty (not synthesized zeros)."""
        assert parse_ethtool_S("NIC statistics:\n") == {}

    def test_non_integer_value_round_trips_as_string(self):
        """ethtool emits non-numeric stats on some drivers (e.g.
        operstate names); the parser preserves them as strings."""
        text = "NIC statistics:\n     link_state: up\n     speed_mbps: 10000\n"
        stats = parse_ethtool_S(text)
        assert stats["link_state"] == "up"
        assert stats["speed_mbps"] == 10000


# ============================================================
# get_host_counters orchestrator
# ============================================================

ETHTOOL_FIXTURE = (REPO_ROOT / "scout-outputs" / "host1-ethtool_S_eth1.txt").read_text(encoding="utf-8")
HASH_POL_HOSTS = ("host1", "host2", "host3", "host4")


@pytest.fixture
def patched_hosts(real_client: ContainerlabClient, inspect_stdout: str):
    """Hermetic harness for get_host_counters: inspect returns the
    scout JSON; exec_on_node returns the captured ethtool fixture for
    every host (real shape, just the same per-host)."""
    def exec_side_effect(container_name: str, cmd: str, **_kwargs) -> str:
        # Accept (and ignore) kind/mgmt_ip kwargs threaded through from
        # get_host_counters — see the corresponding comment on the
        # fabric counters fixture above.
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
    """Switches are excluded — that's get_fabric_counters' job."""
    env, _ = patched_hosts
    hosts_seen = {r["host"] for r in env["data"]}
    assert hosts_seen == set(HASH_POL_HOSTS)


def test_host_records_count_matches_topology(patched_hosts):
    """hash-polarization has 4 hosts; one record per host."""
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
    """§6.5: scenario name only in source."""
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
