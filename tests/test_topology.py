"""Hermetic tests for ``driver/topology.py``.

The fixtures are the actual Stage A Scout capture (``scout-outputs/
inspect.json``) and the actual packaged scenario YAML
(``hash_polarization.clab.yaml``). ``subprocess.run`` is patched so
containerlab does not need to be installed; the parse paths run
against real-substrate-shaped data.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.topology import get_topology
from containerlab_adapter.scenarios import hash_polarization


REPO_ROOT = Path(__file__).resolve().parents[1]
SCOUT_INSPECT = REPO_ROOT / "scout-outputs" / "inspect.json"


def _fake_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


@pytest.fixture
def real_client() -> ContainerlabClient:
    """ContainerlabClient pointing at the real packaged hash-polarization YAML."""
    return ContainerlabClient(topology_path=Path(hash_polarization.topology_path()))


@pytest.fixture
def inspect_stdout() -> str:
    """Stage A Scout's captured inspect.json, returned as the
    canonical stdout string the subprocess wrapper would see."""
    assert SCOUT_INSPECT.exists(), f"scout fixture missing: {SCOUT_INSPECT}"
    return SCOUT_INSPECT.read_text(encoding="utf-8")


@pytest.fixture
def topology_envelope(real_client: ContainerlabClient, inspect_stdout: str) -> dict:
    """Call get_topology against the real YAML + captured inspect output."""
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=inspect_stdout)):
        return get_topology(real_client)


# ---------- envelope shape ----------

def test_envelope_has_all_required_fields(topology_envelope: dict):
    """Envelope mirrors Doppelgänger v0.3 §2.3."""
    for key in ("data", "observed_at_ns", "source", "confidence", "staleness_class"):
        assert key in topology_envelope, f"envelope missing field: {key}"


def test_envelope_confidence_high_for_live_cli_output(topology_envelope: dict):
    """containerlab inspect is authoritative for fabric structure — high confidence."""
    assert topology_envelope["confidence"] == "high"


def test_envelope_staleness_fresh(topology_envelope: dict):
    """We just ran inspect — payload is fresh."""
    assert topology_envelope["staleness_class"] == "fresh"


def test_envelope_observed_at_is_wallclock_ns(topology_envelope: dict):
    """Live substrate uses wall-clock nanoseconds, not simulation time."""
    ts = topology_envelope["observed_at_ns"]
    assert isinstance(ts, int)
    # Sanity: ns timestamp for 2026 is ~1.7e18.
    assert ts > 1_700_000_000_000_000_000


# ---------- leak prevention (§6.5) ----------

def test_lab_name_appears_in_source(topology_envelope: dict):
    """Lab name belongs in operator-visible metadata, not per-record data."""
    assert "hash-polarization" in topology_envelope["source"]


def test_lab_name_does_not_leak_into_data_payload(topology_envelope: dict):
    """No node or link record should echo the lab/scenario name —
    that would reproduce the Stage 5a leak class."""
    data = topology_envelope["data"]
    blob = json.dumps(data)
    assert "hash-polarization" not in blob, (
        "lab name leaked into data payload; §6.5 prohibits scenario "
        "identifiers in per-record fields"
    )


def test_node_names_have_clab_prefix_stripped(topology_envelope: dict):
    """Containerlab's clab-<lab>- prefix carries the scenario id;
    short names are what the agent should see."""
    names = [n["name"] for n in topology_envelope["data"]["nodes"]]
    for name in names:
        assert not name.startswith("clab-"), f"unstripped prefix on {name!r}"
    assert {"spine1", "leaf1", "leaf2", "host1", "host2", "host3", "host4"} <= set(names)


# ---------- structural correctness ----------

def test_shape_is_leaf_spine(topology_envelope: dict):
    assert topology_envelope["data"]["shape"] == "leaf-spine"


def test_counts_match_hash_polarization_scenario(topology_envelope: dict):
    """hash-polarization is 1 spine + 2 leaves + 4 hosts."""
    counts = topology_envelope["data"]["counts"]
    assert counts == {"leaf": 2, "spine": 1, "host": 4}


def test_role_classification_by_name(topology_envelope: dict):
    """leaf*/spine*/host* prefixes drive role; sonic-vs kind is the switch fallback."""
    by_name = {n["name"]: n for n in topology_envelope["data"]["nodes"]}
    assert by_name["leaf1"]["role"] == "leaf"
    assert by_name["spine1"]["role"] == "spine"
    assert by_name["host1"]["role"] == "host"


def test_nodes_carry_diagnostic_fields(topology_envelope: dict):
    """Each node surfaces the fields an SRE would use to correlate
    against container logs / mgmt-network probes."""
    leaf1 = next(n for n in topology_envelope["data"]["nodes"] if n["name"] == "leaf1")
    assert leaf1["kind"] == "sonic-vs"
    assert leaf1["image"].startswith("netreplica/docker-sonic-vs")
    assert leaf1["mgmt_ipv4"] == "172.100.100.2"
    assert leaf1["state"] == "running"
    assert leaf1["container_id"]  # non-empty


def test_links_parsed_from_yaml(topology_envelope: dict):
    """Links come from the YAML (inspect does not surface adjacency).
    hash-polarization declares 6 links: 2 spine-leaf + 4 leaf-host."""
    links = topology_envelope["data"]["links"]
    assert len(links) == 6
    # Every link endpoint has node + port.
    for link in links:
        assert len(link["endpoints"]) == 2
        for ep in link["endpoints"]:
            assert "node" in ep and "port" in ep
            assert ep["port"].startswith("eth")


def test_specific_spine_leaf_link_present(topology_envelope: dict):
    """spine1:eth1 <-> leaf1:eth1 is the declared fabric uplink."""
    links = topology_envelope["data"]["links"]
    pairs = {
        frozenset((f"{ep['node']}:{ep['port']}" for ep in link["endpoints"]))
        for link in links
    }
    assert frozenset({"spine1:eth1", "leaf1:eth1"}) in pairs


# ---------- role classification ----------

def test_classify_role_handles_generic_switch_name_and_sonic_vm_kind():
    """The sonic-substrate-recipe vrspike-1port reference names the
    switch ``sw1`` (no leaf/spine prefix) with kind ``sonic-vm``. The
    name has no role prefix, the kind isn't ``linux``, so classify_role
    falls through to ``switch`` — which SWITCH_ROLES now recognizes, so
    fabric counters target this node correctly."""
    from containerlab_adapter.driver._node_utils import (
        SWITCH_ROLES,
        classify_role,
    )

    assert classify_role("sw1", "sonic-vm") == "switch"
    assert "switch" in SWITCH_ROLES
    # Existing role mappings still resolve via name prefix even when
    # the kind is sonic-vm (the new substrate).
    assert classify_role("leaf1", "sonic-vm") == "leaf"
    assert classify_role("spine1", "sonic-vm") == "spine"
    assert classify_role("host1", "linux") == "host"


# ---------- error handling ----------

def test_get_topology_raises_when_no_lab_deployed(real_client: ContainerlabClient):
    """Empty inspect output (no labs deployed) must fail loud, not
    return an empty topology."""
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="{}")):
        with pytest.raises(ContainerlabError, match="no deployed labs"):
            get_topology(real_client)
