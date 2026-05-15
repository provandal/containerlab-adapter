# containerlab-adapter

HarnessIT's Substrate Adapter for **containerlab + SONiC**. Wraps the `containerlab` CLI plus SSH access to SONiC virtual switches and exposes the same MCP tool contract Doppelgänger does, so HarnessIT can run unchanged against real SONiC running locally in containers.

**Status: Stage A Scout.** Skeleton scaffolded; SONiC image-load + smoke test pending (`STAGE_A_SCOUT.md`). Driver subprocess wrappers (`deploy`/`destroy`/`inspect`) are implemented concretely — they're honest CLI calls. Higher-level tool methods (`get_topology`, `get_fabric_counters`, etc.) remain stubs until SONiC telemetry shapes are captured.

## How this fits

- **HarnessIT** (`provandal/harnessit`) — the agentic harness. Consumes Substrate Adapters via MCP.
- **Doppelgänger** (`provandal/doppelganger`) — the first Substrate Adapter. Wraps NS-3 for simulated leaf-spine fabrics.
- **air-adapter** (`provandal/air-adapter`) — planned NVIDIA DSX Air adapter. Currently blocked on NGC AIR service enrollment.
- **containerlab-adapter** (this repo) — the immediate second Substrate Adapter. Wraps containerlab + SONiC. Local, open-source, no external auth.

Per HarnessIT Architecture v0.6 §4.1, Substrate Adapters are plural by design. This repo unblocks the substrate-substitution validation work that motivated Stage 13 (originally targeted at AIR) without depending on NVIDIA's enrollment process.

## Why SONiC

SONiC is the open-source NOS (Linux Foundation project, originally Microsoft) that runs in production on most large AI/cloud fabrics — Azure, Meta, Alibaba, and many AI-training shops standardize on it. NVIDIA discontinued Cumulus VX in 2024 with the message "use AIR instead"; SONiC is the obvious open alternative and is arguably *more* relevant to HarnessIT's AI-fabric-operations target audience than Cumulus would have been.

The architectural claim — that the harness's investigation logic and skill design are substrate-agnostic — is validated as cleanly by SONiC as by Cumulus or AIR. The MCP contract doesn't care which NOS the substrate runs.

## Setup (WSL2 + Docker + containerlab + SONiC image)

```bash
# Inside WSL2:
bash -c "$(curl -sL https://get.containerlab.dev)"
containerlab version  # confirm

# Load SONiC VS image. The Container kind (sonic-vs) expects an image
# pre-loaded via `docker load`; the image comes from SONiC's Azure
# build pipeline (community master or one of the release branches).
# Procedure documented in STAGE_A_SCOUT.md.
docker images | grep sonic   # confirm after loading
```

This adapter's Python code runs in either Windows-side or WSL2-side Python; `ContainerlabClient` shells out to `containerlab` which must resolve in PATH. The smoke and scout scripts work end-to-end only from inside WSL2 (containerlab is Linux-first).

When the repo lives on a Windows host with WSL2 mounted at `/mnt/c/...`, Windows and Linux venvs cannot coexist in the same `.venv/` directory (different layouts). Use separate venv names:

```bash
# Windows-side (Git Bash / PowerShell) — for hermetic test runs:
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[dev]"
pytest

# WSL2-side — for smoke/scout/MVP work that hits containerlab:
python3 -m venv .venv-wsl
source .venv-wsl/bin/activate
pip install -e ".[dev]"
pytest                       # same 21 tests should pass
python scripts/check_setup.py
python scripts/smoke_topology.py
```

Both `.venv/` and `.venv-*/` are gitignored.

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

Apache-2.0. containerlab is BSD-3-Clause; SONiC is Apache-2.0 (Linux Foundation). This adapter's code carries no inherited license from either; the per-substrate license boundary is at runtime per Doppelgänger v0.3 §9.5.
