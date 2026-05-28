"""Hermetic tests for the containerlab-adapter skeleton.

Subprocess calls to ``containerlab`` are mocked — these tests run
without containerlab or Docker installed. Live behavior is exercised
via the marked ``requires_containerlab`` tests, which are skipped by
default.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------- package shape ----------

def test_package_importable():
    import containerlab_adapter

    assert hasattr(containerlab_adapter, "__version__")
    assert containerlab_adapter.__version__.startswith("0.")


def test_driver_module_exposes_expected_callables():
    """Driver public surface must include all the MCP tools the
    harness expects. If any name changes or disappears, the contract
    with HarnessIT breaks — this test catches it."""
    from containerlab_adapter import driver

    expected = {
        "ContainerlabClient",
        "get_topology",
        "get_fabric_counters",
        "get_host_counters",
        "get_flow_records",
        "list_scenarios",
        "run_scenario",
    }
    missing = expected - set(dir(driver))
    assert not missing, f"Driver missing expected symbols: {missing}"


def test_adapter_module_exposes_build_server():
    from containerlab_adapter import adapter

    assert hasattr(adapter, "build_server")


# ---------- stub honesty ----------

@pytest.mark.parametrize("call", [
    lambda c: __import__("containerlab_adapter.driver.flows",
                         fromlist=["get_flow_records"]).get_flow_records(c),
])
def test_driver_tool_stubs_raise_notimplemented_with_pending_message(call):
    """All Driver tool stubs must raise NotImplementedError mentioning
    'pending'. The honesty contract — code says what it is."""
    fake_client = MagicMock()
    with pytest.raises(NotImplementedError) as excinfo:
        call(fake_client)
    assert "pending" in str(excinfo.value).lower()


# ---------- ContainerlabClient subprocess wrapper ----------

def _fake_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


@pytest.fixture
def fake_topology(tmp_path: Path) -> Path:
    """Create a minimal .clab.yaml so ContainerlabClient.__post_init__ doesn't fail."""
    p = tmp_path / "test.clab.yaml"
    p.write_text("name: test\ntopology:\n  nodes: {}\n", encoding="utf-8")
    return p


def test_client_requires_existing_topology_file(tmp_path: Path):
    """Fail loud if the topology path doesn't exist — never silently
    proceed with a stale or missing file."""
    from containerlab_adapter.driver.client import ContainerlabClient

    missing = tmp_path / "does_not_exist.clab.yaml"
    with pytest.raises(FileNotFoundError, match="topology file not found"):
        ContainerlabClient(topology_path=missing)


def test_client_deploy_parses_json(fake_topology: Path):
    from containerlab_adapter.driver.client import ContainerlabClient

    client = ContainerlabClient(topology_path=fake_topology)
    fake_json = {"name": "test", "nodes": []}
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(fake_json))):
        result = client.deploy()
    assert result == fake_json


def test_client_inspect_parses_json(fake_topology: Path):
    from containerlab_adapter.driver.client import ContainerlabClient

    client = ContainerlabClient(topology_path=fake_topology)
    fake_json = {"containers": [{"name": "spine1"}, {"name": "leaf1"}]}
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(fake_json))):
        result = client.inspect()
    assert result == fake_json


def test_client_destroy_passes_cleanup_flag(fake_topology: Path):
    from containerlab_adapter.driver.client import ContainerlabClient

    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result()) as runner:
        client.destroy(cleanup=True)
    args = runner.call_args.args[0]
    assert "--cleanup" in args
    assert "destroy" in args


def test_client_destroy_omits_cleanup_when_disabled(fake_topology: Path):
    from containerlab_adapter.driver.client import ContainerlabClient

    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result()) as runner:
        client.destroy(cleanup=False)
    args = runner.call_args.args[0]
    assert "--cleanup" not in args


def test_client_raises_on_nonzero_exit(fake_topology: Path):
    from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError

    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(
        stderr="something went wrong", returncode=2,
    )):
        with pytest.raises(ContainerlabError) as excinfo:
            client.deploy()
    assert excinfo.value.returncode == 2
    assert "something went wrong" in excinfo.value.stderr


def test_client_raises_on_missing_binary(fake_topology: Path):
    """If containerlab isn't on PATH, fail with an actionable error,
    not a cryptic stack trace."""
    from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError

    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", side_effect=FileNotFoundError("containerlab")):
        with pytest.raises(ContainerlabError, match="not found in PATH"):
            client.deploy()


def test_client_raises_on_non_json_response(fake_topology: Path):
    from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError

    client = ContainerlabClient(topology_path=fake_topology)
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout="not json")):
        with pytest.raises(ContainerlabError, match="non-JSON"):
            client.deploy()


# ---------- scenario registry ----------

def test_hash_polarization_scenario_has_symptom_and_ground_truth():
    """MVP scenario must declare both agent-facing symptom and
    eval-runner-side ground truth, with no overlap (no ground-truth
    keywords in symptom text)."""
    from containerlab_adapter.scenarios import hash_polarization

    assert hash_polarization.SYMPTOM
    assert hash_polarization.GROUND_TRUTH_ROOT_CAUSE
    assert "ecmp" not in hash_polarization.SYMPTOM.lower()
    assert "polarization" not in hash_polarization.SYMPTOM.lower()


def test_hash_polarization_topology_path_resolves():
    """The packaged topology YAML must be discoverable via importlib
    resources — otherwise smoke_topology.py can't find it."""
    from containerlab_adapter.scenarios import hash_polarization

    path = Path(hash_polarization.topology_path())
    assert path.exists(), f"packaged topology missing: {path}"
    assert path.suffix == ".yaml"


def test_hash_polarization_traffic_plan_pending():
    from containerlab_adapter.scenarios import hash_polarization

    with pytest.raises(NotImplementedError, match="pending Stage A Scout"):
        hash_polarization.build_traffic_plan()
