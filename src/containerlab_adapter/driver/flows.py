"""``get_flow_records`` — per-flow records with FCT and completion status.

Maps onto Doppelgänger v0.3 §4.2. Doppelgänger derives flow records
from substrate ``fct.txt`` / ``intended.txt`` cross-reference. Real
SONiC has no equivalent first-class concept — flows on a running
fabric emerge from packet captures, sFlow, gNMI flow telemetry, or
application-level reporting.

Per the AIR Adoption Plan v0.1 (which applies equally to
containerlab), this tool is **deferred to Stage C** if Stage B's
hash-polarization MVP doesn't need per-flow data (it likely doesn't —
ECMP polarization manifests in per-uplink counters more than in
FCT distributions).
"""

from __future__ import annotations

from typing import Any

from containerlab_adapter.driver.client import ContainerlabClient


def get_flow_records(client: ContainerlabClient) -> dict[str, Any]:
    """Per-flow records for a containerlab deployment.

    Pending Stage A/C Scout. Candidate implementations: (a) sFlow
    or gNMI flow telemetry if SONiC exposes them on the container
    interfaces, (b) tcpdump-based capture on hosts with offline FCT
    analysis, (c) application-level reporting from the traffic
    generator (iperf JSON output is workable).
    """
    raise NotImplementedError(
        "get_flow_records pending Stage A/C Scout — flow telemetry approach undecided"
    )
