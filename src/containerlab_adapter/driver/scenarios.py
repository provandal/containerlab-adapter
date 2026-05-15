"""Scenario management — list and run named scenarios on containerlab.

Two MCP tools per Doppelgänger v0.3 §2.2:

- ``list_scenarios`` — enumerate available scenarios with declared
  symptom, ground-truth root cause, and difficulty class.
- ``run_scenario`` — deploy the scenario's topology + apply Cumulus
  configs + run the traffic generator for the scenario's duration +
  snapshot telemetry + tear down.

The Doppelgänger semantic ("run a simulation to completion") shifts
for containerlab in the same way it shifts for AIR: containers run
in real-time, so a "run" maps onto deploy → configure → traffic →
snapshot → optionally tear down.
"""

from __future__ import annotations

from typing import Any

from containerlab_adapter.driver.client import ContainerlabClient


def list_scenarios(client: ContainerlabClient | None = None) -> dict[str, Any]:
    """Enumerate scenarios this adapter knows how to run.

    Returns a manifest of scenario name → metadata (symptom statement,
    ground-truth root cause for runner-side bookkeeping, difficulty
    class). Reads the local scenario registry — no containerlab call.
    """
    raise NotImplementedError(
        "list_scenarios pending — scenarios registry needs first concrete entry"
    )


def run_scenario(
    client: ContainerlabClient,
    scenario_name: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run a named scenario on containerlab.

    Pending Stage A Scout. The deploy → configure → traffic → snapshot
    → teardown sequence needs real timings + telemetry shapes to pin.
    Cumulus VX initial config (BGP convergence, port admin-up) takes
    seconds-to-minutes; the right "wait for ready" signal needs
    empirical confirmation.
    """
    raise NotImplementedError(
        "run_scenario pending Stage A Scout + scenario authoring decisions"
    )
