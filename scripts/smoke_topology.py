"""Stage A Scout — deploy the minimum topology, inspect, capture, destroy.

Smoke test that exercises the full containerlab deploy → inspect →
destroy path. Output (raw ``inspect`` JSON + per-node SSH probes) is
saved to ``scout-outputs/`` (gitignored) for parser design.

**Requires containerlab + Docker installed.** Run after
``scripts/check_setup.py`` reports all green.

Usage::

    python scripts/smoke_topology.py
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.scenarios.hash_polarization import topology_path


REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = REPO_ROOT / "scout-outputs"
OUTPUT_DIR.mkdir(exist_ok=True)


def main() -> int:
    topo = Path(topology_path())
    print(f"# containerlab smoke — {datetime.utcnow().isoformat()}Z")
    print(f"  topology: {topo}")
    print(f"  output:   {OUTPUT_DIR.relative_to(REPO_ROOT)}/\n")

    try:
        client = ContainerlabClient(topology_path=topo)
    except FileNotFoundError as exc:
        print(f"FATAL: {exc}")
        return 2

    # Deploy
    print("[deploy] starting …")
    try:
        deploy_out = client.deploy()
    except ContainerlabError as exc:
        print(f"[deploy] FAIL: {exc}")
        print(f"  full stderr:\n{exc.stderr}")
        return 3
    deploy_path = OUTPUT_DIR / "deploy.json"
    deploy_path.write_text(json.dumps(deploy_out, indent=2), encoding="utf-8")
    print(f"[deploy] OK — saved to {deploy_path.relative_to(REPO_ROOT)}")

    # Inspect
    print("[inspect] starting …")
    try:
        inspect_out = client.inspect()
    except ContainerlabError as exc:
        print(f"[inspect] FAIL: {exc}")
        # Still try to teardown
        try:
            client.destroy()
        except ContainerlabError:
            pass
        return 4
    inspect_path = OUTPUT_DIR / "inspect.json"
    inspect_path.write_text(json.dumps(inspect_out, indent=2), encoding="utf-8")
    print(f"[inspect] OK — saved to {inspect_path.relative_to(REPO_ROOT)}")
    print(f"  nodes returned: {len(inspect_out.get('containers', inspect_out) or [])}")

    # Teardown — always, even on failure of the SSH probes that would
    # follow this in a fuller scout.
    print("[destroy] tearing down …")
    try:
        client.destroy(cleanup=True)
    except ContainerlabError as exc:
        print(f"[destroy] FAIL: {exc}")
        return 5
    print("[destroy] OK")

    print("\nSmoke test complete. Outputs available in scout-outputs/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
