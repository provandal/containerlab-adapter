# containerlab-adapter

HarnessIT's Substrate Adapter for **containerlab + Cumulus VX**. Wraps the `containerlab` CLI plus SSH access to Cumulus VX nodes and exposes the same MCP tool contract Doppelgänger does, so HarnessIT can run unchanged against real Cumulus Linux running locally in containers.

**Status: Stage A Scout.** Skeleton scaffolded; containerlab smoke-test pending (`STAGE_A_SCOUT.md`). Driver subprocess wrappers (`deploy`/`destroy`/`inspect`) are implemented concretely — they're honest CLI calls, not pending observations. Higher-level tool methods (`get_topology`, `get_fabric_counters`, etc.) remain stubs until Cumulus VX telemetry shapes are captured.

## How this fits

- **HarnessIT** (`provandal/harnessit`) — the agentic harness. Consumes Substrate Adapters via MCP.
- **Doppelgänger** (`provandal/doppelganger`) — the first Substrate Adapter. Wraps NS-3 for simulated leaf-spine fabrics.
- **air-adapter** (`provandal/air-adapter`) — planned NVIDIA DSX Air adapter. Currently blocked on NGC AIR service enrollment.
- **containerlab-adapter** (this repo) — the immediate second Substrate Adapter. Wraps containerlab + Cumulus VX. Local, open-source, no external auth.

Per HarnessIT Architecture v0.6 §4.1, Substrate Adapters are plural by design. This repo unblocks the substrate-substitution validation work that motivated Stage 13 (originally targeted at AIR) without depending on NVIDIA's enrollment process.

## Why containerlab over AIR

- **No external auth.** containerlab is open-source and runs locally; no NGC keys, no service-scope dependencies.
- **Same Cumulus behavior.** Cumulus VX is the same Cumulus Linux image AIR runs. Same `nv show --json`, same `cl-counters`, same `ethtool -S`.
- **Faster iteration.** Local containers spin up in seconds vs. AIR's minutes.
- **Reproducible.** Anyone can install containerlab + Docker and replay our topologies. The published-series payoff is stronger.

What containerlab does NOT give us that AIR would have: NVIDIA-vendor-blessed narrative, hosted convenience, fleet management. None of these are load-bearing for skill validation.

## Setup (WSL2 + Docker + containerlab)

On Windows, the Python harness runs in Git Bash but containerlab itself must run inside WSL2 (Linux-first tool). Docker Desktop with WSL2 integration provides the container runtime.

```bash
# Inside WSL2:
bash -c "$(curl -sL https://get.containerlab.dev)"
containerlab version  # confirm

# Pull Cumulus VX image (one-time, ~700 MB)
docker pull networkop/cx:5.0
```

This adapter's Python code itself can run in either Windows-side or WSL2-side Python; the `ContainerlabClient` shells out to `containerlab` which must resolve in PATH. The simplest setup is to run everything inside WSL2.

```bash
python -m venv .venv
.venv/bin/activate   # or .venv/Scripts/activate on Windows-side
pip install -e ".[dev]"
pytest                # 13+ hermetic tests should pass
```

## Layout

```
src/containerlab_adapter/
  driver/        # subprocess wrapper around `containerlab` CLI + SSH
  adapter/       # MCP server exposing Driver methods as tools
  scenarios/     # scenario definitions (symptom + ground truth + topo ref)
  topologies/    # .clab.yaml topology files for containerlab deploy
scripts/         # smoke + scout scripts
tests/           # hermetic tests (subprocess mocked)
```

## License

Apache-2.0. containerlab is BSD-3-Clause; Cumulus VX is governed by NVIDIA's [Cumulus VX EULA](https://www.nvidia.com/en-us/networking/ethernet-switching/cumulus-vx/) (free for non-production use). This adapter's code carries no inherited license from either.
