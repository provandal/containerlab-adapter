"""Scenario management — list and run named scenarios on containerlab.

Two MCP tools per Doppelgänger v0.3 §2.2:

- ``list_scenarios`` — enumerate scenarios the adapter knows how to run.
  Pure registry walk, no containerlab call.
- ``run_scenario`` — deploy the scenario's topology and return a run_id
  once all containers are running.

v0.1 deliberately does *not* include traffic generation, BGP
convergence waits, or session-level idempotency. The ready signal is
"every container reports ``state: running``" — sufficient for fabric-
structure tools (``get_topology``, ``get_fabric_counters`` against an
empty fabric). Richer ready signals (BGP Established, ping-reachability)
are added when traffic generation lands and counters depend on them.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from containerlab_adapter import scenarios as _scenarios
from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.envelope import envelope


def list_scenarios(client: ContainerlabClient | None = None) -> dict[str, Any]:
    """Return the envelope-wrapped list of scenarios this adapter exposes.

    Each entry carries ``name``, ``intended_symptom`` (the agent-facing
    one-paragraph statement of the user's complaint), and ``difficulty``.
    Ground-truth root cause is deliberately *not* exposed here — that
    field belongs to the runner-side eval loop, not the agent surface.

    The ``client`` parameter is accepted for signature symmetry with the
    other Driver tools but is unused; the registry lives in Python.
    """
    items = [
        {
            "name": name,
            "intended_symptom": module.SYMPTOM,
            "difficulty": module.DIFFICULTY,
        }
        for name, module in _scenarios.REGISTRY.items()
    ]
    return envelope(
        items,
        source="adapter.scenario_registry",
        observed_at_ns=time.time_ns(),
    )


def run_scenario(
    client: ContainerlabClient,
    scenario_name: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Deploy the scenario's topology and return a run_id.

    The ``run_id`` is an opaque 12-char hex (no scenario-name suffix) so
    it does not echo the scenario identifier back into agent-visible
    state — Doppelgänger §6.4 leak rule.

    Raises :class:`ValueError` for an unknown scenario name and
    :class:`ContainerlabError` if any container is not ``running`` after
    deploy returns.
    """
    if scenario_name not in _scenarios.REGISTRY:
        known = sorted(_scenarios.REGISTRY)
        raise ValueError(
            f"Unknown scenario {scenario_name!r}. "
            f"Known scenarios: {known}"
        )

    if run_id is None:
        run_id = uuid.uuid4().hex[:12]

    started_ns = time.time_ns()
    inspect_after_deploy = client.deploy()
    wall_clock_seconds = (time.time_ns() - started_ns) / 1e9

    # containerlab deploy --format json returns inspect-shaped output:
    # {lab_name: [<node_records>]}. Validate every node is running.
    lab_records = next(iter(inspect_after_deploy.values()), [])
    not_running = [
        record.get("name", "<unnamed>")
        for record in lab_records
        if record.get("state") != "running"
    ]
    if not_running:
        raise ContainerlabError(
            f"After deploy, containers not running: {not_running}",
            cmd=["containerlab", "deploy"],
            returncode=0,
        )

    payload = {
        "run_id": run_id,
        "trace_dir": None,
        "node_count": len(lab_records),
        "wall_clock_seconds": wall_clock_seconds,
        "ready": True,
    }
    return envelope(
        payload,
        source=f"driver.run_scenario({scenario_name!r})",
        observed_at_ns=time.time_ns(),
    )
