"""Live smoke against a sonic-substrate-recipe vrspike-1port deployment.

Assumes the recipe's deploy.sh has already brought the lab up. Constructs
a ContainerlabClient against the recipe's topology YAML and exercises:

  - client.inspect()                — sanity check the lab is visible
  - get_topology(client)            — full topology envelope
  - get_fabric_counters(client)     — per-switch fabric show counters via
                                      SSH-to-mgmt-IP transport (new substrate)
  - get_host_counters(client)       — per-host ethtool -S via docker exec

Acceptance: counters should now have populated rows (not the empty/N/A
rows the netreplica image produced for Stage A scouts). For the 1-port
reference, the expected shape is exactly one switch (sw1) with one port
(Ethernet0) and one host (host1) on eth1.

Usage:
    python scripts/smoke_vrspike_1port.py [PATH_TO_RECIPE_TOPOLOGY_YAML]

If the path is omitted, defaults to the sibling sonic-substrate-recipe
repo location used on the canonical substrate host
(/opt/harnessit/sonic-substrate-recipe-test/).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.counters import get_fabric_counters, get_host_counters
from containerlab_adapter.driver.topology import get_topology


DEFAULT_TOPOLOGY = Path(
    "/opt/harnessit/sonic-substrate-recipe-test/topologies/vrspike-1port.clab.yaml"
)


def _show(label: str, payload: dict) -> None:
    print(f"\n{'=' * 8} {label} {'=' * 8}")
    print(json.dumps(payload, indent=2, default=str))


def main(argv: list[str]) -> int:
    topo = Path(argv[1]) if len(argv) > 1 else DEFAULT_TOPOLOGY
    if not topo.exists():
        print(f"FATAL: topology file not found: {topo}", file=sys.stderr)
        return 2

    print(f"# smoke_vrspike_1port — {datetime.utcnow().isoformat()}Z")
    print(f"  topology: {topo}")

    client = ContainerlabClient(topology_path=topo)

    try:
        inspect_data = client.inspect()
    except ContainerlabError as exc:
        print(f"FATAL: inspect failed: {exc}", file=sys.stderr)
        return 3

    if not inspect_data:
        print("FATAL: no deployed labs (run deploy.sh first)", file=sys.stderr)
        return 4

    _show("inspect", inspect_data)

    try:
        topology_env = get_topology(client)
    except ContainerlabError as exc:
        print(f"get_topology FAILED: {exc}", file=sys.stderr)
        return 5
    _show("get_topology", topology_env)

    try:
        fabric_env = get_fabric_counters(client)
    except ContainerlabError as exc:
        print(f"get_fabric_counters FAILED: {exc}\n  stderr: {exc.stderr}", file=sys.stderr)
        return 6
    _show("get_fabric_counters", fabric_env)

    try:
        host_env = get_host_counters(client)
    except ContainerlabError as exc:
        print(f"get_host_counters FAILED: {exc}\n  stderr: {exc.stderr}", file=sys.stderr)
        return 7
    _show("get_host_counters", host_env)

    # Acceptance summary
    fabric_records = fabric_env["data"]
    host_records = host_env["data"]
    populated_fabric = [
        r for r in fabric_records
        if (r["rx"]["ok"] is not None and r["rx"]["ok"] > 0)
        or (r["tx"]["ok"] is not None and r["tx"]["ok"] > 0)
    ]
    populated_hosts = [
        h for h in host_records if h["stats"]
    ]
    print(f"\n{'=' * 8} acceptance {'=' * 8}")
    print(f"  fabric records: {len(fabric_records)} ({len(populated_fabric)} with non-zero rx/tx)")
    print(f"  host records:   {len(host_records)} ({len(populated_hosts)} with stats)")
    print(f"  switches seen:  {sorted({r['switch'] for r in fabric_records})}")
    print(f"  hosts seen:     {sorted({h['host'] for h in host_records})}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
