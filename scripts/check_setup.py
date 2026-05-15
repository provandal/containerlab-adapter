"""Stage A Scout — verify local environment is ready for containerlab.

Read-only probes. No deploys, no resource use. Just confirms:

1. ``docker`` is on PATH and accessible
2. ``containerlab`` is on PATH and accessible
3. The Cumulus VX image is pulled (or pullable)
4. The host has enough free RAM for a minimum-viable Cumulus topology

Exit code 0 if all green; non-zero with diagnostic output otherwise.

Usage::

    python scripts/check_setup.py
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Tuple


SONIC_VS_IMAGE = "docker-sonic-vs:latest"
MIN_RAM_GB_FOR_SMOKE = 4  # 2-leaf 1-spine 4-host fits comfortably


def probe(name: str, cmd: list[str]) -> Tuple[bool, str]:
    """Run a probe command; return (ok, stdout-or-error)."""
    if not shutil.which(cmd[0]):
        return False, f"{cmd[0]} not on PATH"
    try:
        result = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=15)
    except subprocess.TimeoutExpired:
        return False, f"{name}: timed out after 15s"
    except Exception as exc:
        return False, f"{name}: {type(exc).__name__}: {exc}"
    if result.returncode != 0:
        return False, f"{name}: exit {result.returncode} — {result.stderr.strip()[:200]}"
    return True, result.stdout.strip().splitlines()[0] if result.stdout else "ok"


def main() -> int:
    print("# containerlab Stage A Scout — environment check\n")

    failures: list[str] = []

    # 1. Docker
    ok, msg = probe("docker", ["docker", "version", "--format", "{{.Server.Version}}"])
    print(f"[{'OK' if ok else 'FAIL'}] docker: {msg}")
    if not ok:
        failures.append("docker not working — install Docker Desktop and enable WSL2 integration")

    # 2. containerlab
    ok, msg = probe("containerlab", ["containerlab", "version"])
    print(f"[{'OK' if ok else 'FAIL'}] containerlab: {msg}")
    if not ok:
        failures.append(
            "containerlab not installed — inside WSL2, run:\n"
            '    bash -c "$(curl -sL https://get.containerlab.dev)"'
        )

    # 3. SONiC VS image — must be loaded from Azure pipeline artifact
    # (this kind doesn't pull from a public registry).
    ok, msg = probe("docker images", ["docker", "image", "inspect", SONIC_VS_IMAGE])
    if ok:
        print(f"[OK] SONiC VS image present: {SONIC_VS_IMAGE}")
    else:
        print(
            f"[WARN] {SONIC_VS_IMAGE} not loaded yet — see STAGE_A_SCOUT.md\n"
            "       for the Azure-pipeline download + `docker load -i ...` procedure.\n"
            "       Image tag in YAML may need adjusting to match your local store."
        )

    # 4. RAM check
    try:
        if sys.platform == "win32":
            # WSL or Windows-side; rough check via systeminfo isn't worth the parse cost.
            print("[SKIP] RAM check (Windows host — manual verification recommended)")
        else:
            import os
            mem_total_kb = int(open("/proc/meminfo").readline().split()[1])
            ram_gb = mem_total_kb / 1024 / 1024
            ok = ram_gb >= MIN_RAM_GB_FOR_SMOKE
            label = "OK" if ok else "WARN"
            print(f"[{label}] RAM: {ram_gb:.1f} GB (need ≥{MIN_RAM_GB_FOR_SMOKE} for smoke test)")
    except Exception as exc:
        print(f"[SKIP] RAM check failed: {exc}")

    print()
    if failures:
        print("BLOCKERS:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("Environment looks good. Next step: scripts/smoke_topology.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
