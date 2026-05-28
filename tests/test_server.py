"""Hermetic tests for the containerlab-adapter MCP server.

The server is intentionally thin — each tool delegates to a Driver
function and returns the envelope the Driver already builds. These
tests cover the wiring surface only:

* ``build_server`` returns a FastMCP instance with the expected tools.
* The session model lazily binds a client on ``run_scenario`` and
  enforces single-topology semantics.
* ``get_*`` tools raise :class:`NoActiveDeploymentError` before any
  ``run_scenario`` call.
* Each tool routes to the corresponding Driver function and returns
  its envelope unchanged.

No live containerlab interaction. Driver functions are exercised with
a fake :class:`ContainerlabClient` whose ``deploy`` / ``inspect`` /
``exec_on_node`` are mocked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from containerlab_adapter.adapter import (
    NoActiveDeploymentError,
    build_server,
)
from containerlab_adapter.driver.client import ContainerlabClient
from containerlab_adapter.scenarios import hash_polarization


# ---------- helpers ----------


def _fake_client(tmp_path: Path) -> ContainerlabClient:
    """Construct a ContainerlabClient bound to a stub topology file.

    The file exists (so ``__post_init__`` passes) but subprocess calls
    are mocked by individual tests; the client never actually shells
    out.
    """
    yaml_path = tmp_path / "test.clab.yaml"
    yaml_path.write_text(
        "name: test\n"
        "topology:\n"
        "  nodes:\n"
        "    leaf1: {kind: sonic-vs}\n"
        "  links: []\n",
        encoding="utf-8",
    )
    return ContainerlabClient(topology_path=yaml_path)


def _tool_names(server: Any) -> set[str]:
    """Pull registered tool names off a FastMCP instance.

    FastMCP exposes registered tools via async ``list_tools``; the
    tool-manager dict is the sync path Doppelgänger's tests also use.
    """
    return set(server._tool_manager._tools.keys())  # type: ignore[attr-defined]


def _call_tool(server: Any, tool_name: str, **kwargs: Any) -> Any:
    """Invoke a registered tool by name and return its raw Python result.

    Uses the FastMCP tool-manager's ``fn`` attribute, which is the
    undecorated Python callable. Bypasses MCP serialization to keep
    these tests focused on the wiring contract. The helper parameter is
    named ``tool_name`` rather than ``name`` so it doesn't collide with
    tools that take a ``name`` argument (e.g. ``run_scenario``).
    """
    tool = server._tool_manager._tools[tool_name]  # type: ignore[attr-defined]
    return tool.fn(**kwargs)


# ---------- server construction ----------


def test_build_server_returns_fastmcp_instance():
    server = build_server()
    assert hasattr(server, "run")
    assert hasattr(server, "tool")


def test_build_server_default_name():
    server = build_server()
    assert server.name == "containerlab-substrate-adapter"


def test_build_server_takes_custom_name():
    server = build_server(server_name="custom-test-name")
    assert server.name == "custom-test-name"


def test_server_registers_expected_tools(tmp_path: Path):
    """v0.2 surface — six tools. The five schema-defined tools per
    substrate-schema v1.1 §3, plus the legacy ``list_scenarios``
    substrate-specific extra."""
    server = build_server(client=_fake_client(tmp_path))
    assert _tool_names(server) == {
        "list_scenarios",
        "run_scenario",
        "get_topology",
        "get_fabric_counters",
        "get_flow_records",
        "get_host_counters",
    }


def test_server_does_not_register_compare_runs(tmp_path: Path):
    """compare_runs stays absent — containerlab has no flow-trace corpus
    to compare. get_flow_records IS registered (schema v1.1 §3.3
    requires it) but returns staleness_class='unsupported' with empty
    flows."""
    server = build_server(client=_fake_client(tmp_path))
    names = _tool_names(server)
    assert "compare_runs" not in names
    assert "get_flow_records" in names


# ---------- session model ----------


def test_get_topology_without_deployment_raises(tmp_path: Path):
    """get_* tools must fail loud, not return an empty envelope."""
    server = build_server()  # no preset client
    with pytest.raises(NoActiveDeploymentError, match="No active deployment"):
        _call_tool(server, "get_topology")


def test_get_fabric_counters_without_deployment_raises():
    server = build_server()
    with pytest.raises(NoActiveDeploymentError):
        _call_tool(server, "get_fabric_counters")


def test_get_host_counters_without_deployment_raises():
    server = build_server()
    with pytest.raises(NoActiveDeploymentError):
        _call_tool(server, "get_host_counters")


def test_list_scenarios_works_without_deployment():
    """list_scenarios is a pure registry walk and must not require a client."""
    server = build_server()
    envelope = _call_tool(server, "list_scenarios")
    assert envelope["confidence"] == "high"
    names = {item["name"] for item in envelope["data"]}
    assert "hash-polarization" in names


def test_run_scenario_rejects_unknown_name():
    server = build_server()
    with pytest.raises(ValueError, match="Unknown scenario"):
        _call_tool(server, "run_scenario", scenario_name="no-such-scenario")


def test_run_scenario_lazily_binds_client_from_scenario_topology():
    """When build_server is called with client=None, the first
    run_scenario(name) must construct a ContainerlabClient bound to
    that scenario's packaged topology YAML — no caller plumbing."""
    server = build_server()

    fake_deploy = {"hash-polarization": [
        {"name": "clab-hash-polarization-leaf1", "state": "running"},
    ]}
    # Patch ContainerlabClient.deploy at the server-module import site so
    # the lazily-constructed client doesn't actually shell out.
    with patch(
        "containerlab_adapter.driver.client.ContainerlabClient.deploy",
        return_value=fake_deploy,
    ):
        run_response = _call_tool(server, "run_scenario", scenario_name="hash-polarization")

    # Schema v1.1 run_scenario response carries envelope + run with status
    assert run_response["run"]["status"] == "completed"
    assert run_response["envelope"]["substrate_kind"] == "containerized"
    # And subsequent get_* calls must now have a bound client (no
    # NoActiveDeploymentError); we verify by patching inspect to a
    # minimal deployed lab and re-invoking get_topology.
    with patch(
        "containerlab_adapter.driver.client.ContainerlabClient.inspect",
        return_value={"hash-polarization": [
            {
                "name": "clab-hash-polarization-leaf1",
                "kind": "sonic-vs",
                "state": "running",
            },
        ]},
    ):
        top_response = _call_tool(server, "get_topology")
    # Schema v1.1 get_topology returns envelope + topology with nodes + links
    leaf_nodes = [n for n in top_response["topology"]["nodes"] if n["role"] == "leaf"]
    assert len(leaf_nodes) == 1


def test_run_scenario_with_preset_client_uses_that_client():
    """When a client is preset on build_server matching the scenario's
    own topology path, run_scenario must reuse it rather than
    constructing a fresh one."""
    preset = ContainerlabClient(
        topology_path=Path(hash_polarization.topology_path())
    )
    server = build_server(client=preset)

    fake_deploy = {"hash-polarization": [
        {"name": "clab-hash-polarization-leaf1", "state": "running"},
    ]}
    with patch.object(
        ContainerlabClient, "deploy", return_value=fake_deploy
    ) as deploy_mock:
        _call_tool(server, "run_scenario", scenario_name="hash-polarization")

    assert deploy_mock.call_count == 1


def test_run_scenario_rejects_topology_switch(tmp_path: Path):
    """v0.1 server runs one lab per session. Calling run_scenario with a
    scenario whose topology differs from the preset client's must fail
    rather than silently swap topologies."""
    preset = _fake_client(tmp_path)  # bound to tmp_path/test.clab.yaml
    server = build_server(client=preset)

    # hash-polarization's topology_path is the packaged YAML, not
    # tmp_path/test.clab.yaml — server should reject.
    with pytest.raises(ValueError, match="bound to"):
        _call_tool(server, "run_scenario", scenario_name="hash-polarization")


# ---------- tool delegation ----------


def test_list_scenarios_envelope_matches_driver_output():
    """list_scenarios is a thin pass-through; envelope contents come
    directly from the Driver's registry walk."""
    server = build_server()
    envelope = _call_tool(server, "list_scenarios")
    assert set(envelope) == {
        "data", "source", "observed_at_ns", "confidence", "staleness_class",
    }
    assert envelope["source"] == "adapter.scenario_registry"
    for item in envelope["data"]:
        assert {"name", "intended_symptom", "difficulty"} <= set(item)


def test_get_topology_delegates_to_driver(tmp_path: Path):
    """The server's get_topology tool must call the driver's get_topology
    on the bound client and return its envelope unchanged."""
    client = _fake_client(tmp_path)
    server = build_server(client=client)

    with patch.object(
        ContainerlabClient,
        "inspect",
        return_value={"test": [
            {
                "name": "clab-test-leaf1",
                "kind": "sonic-vs",
                "state": "running",
            },
            {
                "name": "clab-test-spine1",
                "kind": "sonic-vs",
                "state": "running",
            },
            {
                "name": "clab-test-host1",
                "kind": "linux",
                "state": "running",
            },
        ]},
    ):
        response = _call_tool(server, "get_topology")

    # Schema v1.1: TopologyResponse carries envelope + topology with
    # nodes and links. Verify role counts via the nodes list directly.
    nodes = response["topology"]["nodes"]
    role_counts: dict[str, int] = {}
    for node in nodes:
        role_counts[node["role"]] = role_counts.get(node["role"], 0) + 1
    assert role_counts == {"leaf": 1, "spine": 1, "host": 1}


def test_run_scenario_envelope_does_not_leak_scenario_name():
    """The §6.4 leak rule applies through the MCP surface too: the
    scenario name must NOT appear in the run_scenario data payload (it
    is allowed in ``source`` only)."""
    server = build_server()
    with patch.object(
        ContainerlabClient, "deploy", return_value={"hash-polarization": [
            {"name": "clab-hash-polarization-leaf1", "state": "running"},
        ]},
    ):
        response = _call_tool(server, "run_scenario", scenario_name="hash-polarization")

    # Schema v1.1: scenario_name IS in response["run"]["scenario_name"]
    # by schema design (agent provided it, echo is not a new leak). But
    # it must NOT leak into other top-level fields. The envelope's
    # source field is allowed to carry the lab name (operator-side
    # metadata, not the substrate scenario tag).
    run_payload = response["run"]
    assert run_payload["scenario_name"] == "hash-polarization"
    # run_id must not embed the scenario name
    assert "hash-polarization" not in run_payload["run_id"]
    assert "hash" not in run_payload["run_id"]
    # Envelope source CAN reference the lab_name (which equals the
    # scenario_name here, but that's the deployment's name, not the
    # leak vector — operator-side trace metadata).
    assert "hash-polarization" in response["envelope"]["source"]


def test_run_scenario_run_id_is_opaque_hex_by_default(tmp_path: Path):
    """run_id must default to 12-char hex with no scenario suffix."""
    server = build_server()
    with patch.object(
        ContainerlabClient, "deploy", return_value={"hash-polarization": [
            {"name": "clab-hash-polarization-leaf1", "state": "running"},
        ]},
    ):
        response = _call_tool(server, "run_scenario", scenario_name="hash-polarization")

    run_id = response["run"]["run_id"]
    assert len(run_id) == 12
    assert all(c in "0123456789abcdef" for c in run_id)
    assert "hash" not in run_id
    assert "polarization" not in run_id
