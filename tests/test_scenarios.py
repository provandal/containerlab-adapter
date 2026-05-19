"""Hermetic tests for ``driver/scenarios.py`` — list + run.

``subprocess.run`` is patched so containerlab does not need to be
installed. The scenario registry walk is exercised against the real
``containerlab_adapter.scenarios`` package.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.scenarios import list_scenarios, run_scenario
from containerlab_adapter.scenarios import hash_polarization


SCENARIO_NAME = "hash-polarization"


def _fake_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0):
    r = MagicMock(spec=subprocess.CompletedProcess)
    r.stdout = stdout
    r.stderr = stderr
    r.returncode = returncode
    return r


@pytest.fixture
def real_client() -> ContainerlabClient:
    return ContainerlabClient(topology_path=Path(hash_polarization.topology_path()))


def _all_running_inspect(lab_name: str = SCENARIO_NAME, node_count: int = 7) -> dict:
    """Build a minimal containerlab-inspect-shaped JSON dict with all
    nodes in state=running."""
    return {
        lab_name: [
            {"name": f"clab-{lab_name}-node{i}", "state": "running"}
            for i in range(node_count)
        ]
    }


# ---------- list_scenarios ----------

def test_list_scenarios_returns_envelope():
    env = list_scenarios()
    for key in ("data", "observed_at_ns", "source", "confidence", "staleness_class"):
        assert key in env


def test_list_scenarios_contains_hash_polarization():
    env = list_scenarios()
    names = [item["name"] for item in env["data"]]
    assert SCENARIO_NAME in names


def test_list_scenarios_each_entry_has_required_fields():
    env = list_scenarios()
    for item in env["data"]:
        assert "name" in item and item["name"]
        assert "intended_symptom" in item and item["intended_symptom"]
        assert "difficulty" in item and item["difficulty"]


def test_list_scenarios_does_not_expose_ground_truth():
    """Ground-truth root cause is runner-side only. It must never
    appear on the agent-visible response — that would leak the answer
    key directly."""
    env = list_scenarios()
    blob = json.dumps(env["data"])
    assert "ECMP" not in blob, "ground-truth keyword leaked into list_scenarios output"
    assert "polarization" not in blob.lower() or "hash-polarization" in blob, (
        "ground-truth 'polarization' leaked outside the scenario name"
    )


def test_list_scenarios_accepts_optional_client(real_client: ContainerlabClient):
    """Signature symmetry — list_scenarios takes an optional client
    for API parity with run_scenario, but does not use it."""
    env_with = list_scenarios(real_client)
    env_without = list_scenarios()
    assert [i["name"] for i in env_with["data"]] == [i["name"] for i in env_without["data"]]


# ---------- run_scenario: success path ----------

def test_run_scenario_returns_envelope_with_required_payload(real_client: ContainerlabClient):
    deploy_output = _all_running_inspect()
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(deploy_output))):
        env = run_scenario(real_client, SCENARIO_NAME)
    data = env["data"]
    assert data["ready"] is True
    assert data["node_count"] == 7
    assert data["trace_dir"] is None
    assert isinstance(data["wall_clock_seconds"], float)
    assert data["wall_clock_seconds"] >= 0
    assert "run_id" in data and data["run_id"]


def test_run_scenario_run_id_is_opaque_hex(real_client: ContainerlabClient):
    """run_id must be UUID-derived hex with no scenario-name component
    — §6.4 leak rule. Pattern: lowercase hex, exactly 12 chars."""
    deploy_output = _all_running_inspect()
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(deploy_output))):
        env = run_scenario(real_client, SCENARIO_NAME)
    run_id = env["data"]["run_id"]
    assert re.fullmatch(r"[0-9a-f]{12}", run_id), f"run_id {run_id!r} is not opaque hex"


def test_run_scenario_scenario_name_does_not_leak_into_data(real_client: ContainerlabClient):
    """Scenario name belongs in the envelope's source, never inside
    data records. Otherwise the agent reads its own answer key out of
    the response."""
    deploy_output = _all_running_inspect()
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(deploy_output))):
        env = run_scenario(real_client, SCENARIO_NAME)
    data_blob = json.dumps(env["data"])
    assert SCENARIO_NAME not in data_blob


def test_run_scenario_scenario_name_appears_in_source(real_client: ContainerlabClient):
    """Source field IS the operator-visible metadata channel — name
    appears here (mirrors Doppelgänger v0.3 §6.5)."""
    deploy_output = _all_running_inspect()
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(deploy_output))):
        env = run_scenario(real_client, SCENARIO_NAME)
    assert SCENARIO_NAME in env["source"]


def test_run_scenario_caller_supplied_run_id_honored(real_client: ContainerlabClient):
    """An explicit run_id parameter overrides the auto-generated UUID.
    Caller is responsible for §6.4 compliance on the custom value."""
    deploy_output = _all_running_inspect()
    custom = "custom-run-abc123"
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(deploy_output))):
        env = run_scenario(real_client, SCENARIO_NAME, run_id=custom)
    assert env["data"]["run_id"] == custom


def test_run_scenario_each_call_generates_a_fresh_run_id(real_client: ContainerlabClient):
    """No idempotency in v0.1 — repeated calls return distinct run_ids."""
    deploy_output = _all_running_inspect()
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(deploy_output))):
        env_a = run_scenario(real_client, SCENARIO_NAME)
        env_b = run_scenario(real_client, SCENARIO_NAME)
    assert env_a["data"]["run_id"] != env_b["data"]["run_id"]


# ---------- run_scenario: failure paths ----------

def test_run_scenario_unknown_name_raises(real_client: ContainerlabClient):
    with pytest.raises(ValueError, match="Unknown scenario"):
        run_scenario(real_client, "not-a-real-scenario")


def test_run_scenario_unknown_name_does_not_invoke_containerlab(real_client: ContainerlabClient):
    """Validation happens before deploy — never invoke containerlab
    with an unknown name."""
    with patch("subprocess.run") as runner:
        with pytest.raises(ValueError):
            run_scenario(real_client, "not-a-real-scenario")
        runner.assert_not_called()


def test_run_scenario_raises_when_container_not_running(real_client: ContainerlabClient):
    """If any container is not state=running after deploy, fail loud
    rather than returning a 'ready: True' lie."""
    partial = {
        SCENARIO_NAME: [
            {"name": f"clab-{SCENARIO_NAME}-spine1", "state": "running"},
            {"name": f"clab-{SCENARIO_NAME}-leaf1", "state": "exited"},
        ]
    }
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(partial))):
        with pytest.raises(ContainerlabError, match="not running"):
            run_scenario(real_client, SCENARIO_NAME)


def test_run_scenario_failure_message_names_the_bad_containers(real_client: ContainerlabClient):
    partial = {
        SCENARIO_NAME: [
            {"name": f"clab-{SCENARIO_NAME}-spine1", "state": "running"},
            {"name": f"clab-{SCENARIO_NAME}-leaf1", "state": "exited"},
        ]
    }
    with patch("subprocess.run", return_value=_fake_subprocess_result(stdout=json.dumps(partial))):
        with pytest.raises(ContainerlabError) as excinfo:
            run_scenario(real_client, SCENARIO_NAME)
    assert "leaf1" in str(excinfo.value)
    assert "spine1" not in str(excinfo.value)
