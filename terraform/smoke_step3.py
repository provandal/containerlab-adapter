"""Stage B steps 3+4 live smoke — Driver end-to-end against AWS substrate.

Deploys hash-polarization via run_scenario, snapshots fabric counters
via get_fabric_counters AND host counters via get_host_counters, tears
down. Validates that the parsers built against scout-captured text
correctly handle truly-live SONiC + ethtool output.

Run from the EC2 host as the ubuntu user, with the .venv active::

    cd /opt/harnessit/containerlab-adapter
    source .venv/bin/activate
    python terraform/smoke_step3.py 2>&1 | tee /tmp/smoke_step3.log

The script destroys any prior deployment before deploying fresh and
always tears down on the way out (success or failure).
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from containerlab_adapter.driver.client import ContainerlabClient, ContainerlabError
from containerlab_adapter.driver.counters import (
    get_fabric_counters,
    get_host_counters,
)
from containerlab_adapter.driver.scenarios import run_scenario
from containerlab_adapter.scenarios import hash_polarization


SCENARIO = "hash-polarization"
EXPECTED_SWITCHES = {"leaf1", "leaf2", "spine1"}
EXPECTED_HOSTS = {"host1", "host2", "host3", "host4"}
EXPECTED_HOST_PORT_TOTAL = 8  # spine1 (2) + leaf1 (3) + leaf2 (3)


def banner(msg: str) -> None:
    print(f"\n=== {msg} ===", flush=True)


def main() -> int:
    topology_path = Path(hash_polarization.topology_path())
    print(f"topology: {topology_path}", flush=True)
    client = ContainerlabClient(topology_path=topology_path)

    # Clean slate — silently ignore destroy errors on first run.
    banner("clean any prior deployment")
    try:
        client.destroy(cleanup=True)
        print("prior lab destroyed")
    except ContainerlabError as exc:
        print(f"no prior lab to destroy (ok): {exc}")

    exit_code = 0
    try:
        banner("run_scenario")
        t0 = time.time()
        run_env = run_scenario(client, SCENARIO)
        print(json.dumps(run_env, indent=2), flush=True)
        print(f"run_scenario wall-clock: {time.time() - t0:.1f}s", flush=True)

        # SONiC services need time after containers reach state=running
        # before `show` aliases work cleanly. Stage A waited 60s; mirror
        # that here so we are comparing apples to apples.
        banner("waiting 60s for SONiC bootstrap")
        time.sleep(60)

        banner("get_fabric_counters")
        t0 = time.time()
        counters_env = get_fabric_counters(client)
        print(f"get_fabric_counters wall-clock: {time.time() - t0:.1f}s", flush=True)

        records = counters_env["data"]
        print(f"record count: {len(records)}", flush=True)
        print(f"source: {counters_env['source']}", flush=True)
        print(f"observed_at_ns: {counters_env['observed_at_ns']}", flush=True)
        print(f"confidence: {counters_env['confidence']}", flush=True)

        banner("validation")
        switches_seen = {r["switch"] for r in records}
        ports_seen = [(r["switch"], r["port"]) for r in records]
        print(f"switches seen: {sorted(switches_seen)}")
        print(f"ports seen: {ports_seen}")

        assertions = []

        def check(name: str, condition: bool, detail: str = "") -> None:
            mark = "PASS" if condition else "FAIL"
            assertions.append((name, condition))
            print(f"[{mark}] {name}{(': ' + detail) if detail else ''}", flush=True)

        check(
            "switches == leaf1/leaf2/spine1",
            switches_seen == EXPECTED_SWITCHES,
            f"got {sorted(switches_seen)}",
        )
        check(
            f"record count == {EXPECTED_HOST_PORT_TOTAL}",
            len(records) == EXPECTED_HOST_PORT_TOTAL,
            f"got {len(records)}",
        )
        check(
            f"{SCENARIO!r} absent from data payload (leak rule)",
            SCENARIO not in json.dumps(records),
        )
        check(
            f"{SCENARIO!r} present in source",
            SCENARIO in counters_env["source"],
        )
        for record in records[:1]:
            check(
                "first record has nested rx/tx/queues/pfc_rx/pfc_tx",
                all(k in record for k in ("rx", "tx", "queues", "pfc_rx", "pfc_tx")),
                f"keys={sorted(record.keys())}",
            )

        banner("first record (sample)")
        if records:
            print(json.dumps(records[0], indent=2), flush=True)

        banner("get_host_counters")
        t0 = time.time()
        host_env = get_host_counters(client)
        print(f"get_host_counters wall-clock: {time.time() - t0:.1f}s", flush=True)
        host_records = host_env["data"]
        print(f"host record count: {len(host_records)}", flush=True)
        print(f"source: {host_env['source']}", flush=True)

        hosts_seen = {r["host"] for r in host_records}
        check(
            "host set == host1..host4",
            hosts_seen == EXPECTED_HOSTS,
            f"got {sorted(hosts_seen)}",
        )
        check(
            "every host carries rx_queue_0_drops",
            all("rx_queue_0_drops" in r["stats"] for r in host_records),
        )
        check(
            f"{SCENARIO!r} absent from host_counters data payload",
            SCENARIO not in json.dumps(host_records),
        )
        check(
            f"{SCENARIO!r} present in host_counters source",
            SCENARIO in host_env["source"],
        )

        banner("first host record (sample)")
        if host_records:
            sample = host_records[0].copy()
            # Stats can be ~20 fields; trim to first 5 for readability.
            sample["stats"] = dict(list(sample["stats"].items())[:5]) | {"...": "(truncated)"}
            print(json.dumps(sample, indent=2), flush=True)

        failed = [name for name, ok in assertions if not ok]
        if failed:
            print(f"\nFAILED CHECKS: {failed}", flush=True)
            exit_code = 1
        else:
            print(f"\nALL {len(assertions)} CHECKS PASSED", flush=True)

    except ContainerlabError as exc:
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}", flush=True)
        # ContainerlabError carries the subprocess stderr; surface it
        # so deploy/destroy failures are diagnosable in one round-trip
        # rather than requiring a manual containerlab invocation.
        if exc.stderr:
            print("--- containerlab stderr ---", flush=True)
            print(exc.stderr, flush=True)
            print("---", flush=True)
        traceback.print_exc()
        exit_code = 2
    except Exception as exc:
        print(f"SMOKE FAILED: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        exit_code = 2
    finally:
        banner("teardown")
        try:
            client.destroy(cleanup=True)
            print("teardown complete", flush=True)
        except ContainerlabError as exc:
            print(f"teardown error (ignored): {exc}", flush=True)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
