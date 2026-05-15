"""hash-polarization scenario for containerlab + SONiC.

MVP scenario for the substrate-substitution validation work. Pairs
with the Doppelgänger hash-polarization scenario; the agent's
behavior on both should be compared under Calibrated Commitment v0.3
to test substrate-overfit.

Topology spec: :mod:`containerlab_adapter.topologies.hash_polarization`
(``hash_polarization.clab.yaml``).

Scenario shape:

- Topology: 4 leaves × 2 spines × multiple hosts per leaf (final
  dimensions confirmed by Stage A Scout — needs to fit in laptop RAM;
  SONiC VS containers are ~500 MB each).
- Initial config: factory SONiC, standard ECMP, no QoS tuning, BGP
  for leaf-spine adjacency.
- Traffic pattern: many parallel iperf flows with constrained
  source-port range to induce ECMP hash collision onto a subset of
  spine uplinks.
- Declared symptom: bimodal flow completion times, ~30% of flows
  ~2× slower, no obvious endpoint correlation.
- Ground truth: ECMP hash polarization on the constrained 5-tuple
  set; one spine carries disproportionate load.
"""

from __future__ import annotations

from importlib import resources


SYMPTOM = (
    "About 30% of training-step flows take ~2× as long as the rest. "
    "It started a couple of weeks ago, no firmware change, no topology "
    "change. Other flows on the same hosts are fine."
)


# Ground-truth root cause — used by the eval runner for scoring only.
# MUST NEVER appear in any agent-facing context (system prompt, tool
# response, error message).
GROUND_TRUTH_ROOT_CAUSE = (
    "ECMP hash polarization — flows with constrained source-port "
    "entropy hash onto a subset of spine uplinks, producing per-uplink "
    "load imbalance and per-flow bimodal slowdown."
)


def topology_path() -> str:
    """Return the absolute path to this scenario's containerlab YAML."""
    return str(
        resources.files("containerlab_adapter.topologies")
        / "hash_polarization.clab.yaml"
    )


def build_traffic_plan() -> dict:
    """Return the traffic-generator plan for this scenario.

    Pending Stage A Scout. Likely an iperf-based plan executed on the
    host containers with constrained source-port range; exact
    invocation depends on the host image's iperf availability.
    """
    raise NotImplementedError(
        "hash_polarization.build_traffic_plan pending Stage A Scout"
    )
