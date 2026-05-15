# Stage A Scout — containerlab + SONiC

Updated 2026-05-15.

## What we know

**containerlab** is an open-source CLI (BSD-3-Clause, srl-labs / Nokia SR Linux team) that declaratively deploys multi-node network topologies as Docker containers. First-class support for `sonic-vs` (containerized SONiC).

**SONiC** is the open-source NOS run in production by Azure, Meta, Alibaba, and many AI fabric operators. The Container distribution (`sonic-vs`) is a stripped-down image of the full SONiC switch stack runnable as a Linux container — bgp, swss, syncd, and the standard `show ...` CLI all present.

**Important environment note**: NVIDIA discontinued Cumulus VX in 2024. The containerlab `cvx` kind still works against legacy `networkop/cx` community-maintained images but is end-of-life. SONiC is the actively-maintained alternative this adapter targets.

**Local environment confirmed (workspace machine)**: Docker Desktop 28.4 + WSL2 2.6.1, containerlab installed inside WSL2, RAM 31.2 GB. SONiC image not yet loaded.

## What this Scout will confirm

1. **SONiC VS image loaded into Docker** via `docker load -i docker-sonic-vs.gz`. Source: SONiC's Azure build pipeline (community master or a release branch artifact). Image lands as something like `docker-sonic-vs:latest`.
2. **Minimum 2-leaf 1-spine 4-host topology deploys** from `src/containerlab_adapter/topologies/hash_polarization.clab.yaml`.
3. **SSH into a SONiC node works**. Default creds for community sonic-vs are `admin / YourPaSsWoRd` (case-sensitive); container shell access is also available via `docker exec`.
4. **Telemetry commands return parseable output**:
   - `show interfaces counters` (basic)
   - `show interfaces counters detailed Ethernet0` (per-port detail)
   - `show queue counters Ethernet0` (per-queue)
   - `show pfc counters` (PFC pause counters)
   - `show priority-group watermark shared`, `... headroom` (PG watermarks)
   - `show priority-group persistent-watermark`
5. **gNMI as a JSON path** if the `show` commands prove text-only or fragile. SONiC supports gNMI for structured telemetry.
6. **Teardown is clean** (`containerlab destroy --cleanup`).

Outputs saved to `scout-outputs/` (gitignored) for parser design in `src/containerlab_adapter/driver/`.

## SONiC image-load procedure

The `sonic-vs` containerlab kind expects an image already in the local Docker store; it does NOT pull from a public registry. Two paths:

**Option A — community master build (recommended for initial Scout):**
1. Visit [SONiC's Azure pipeline](https://sonic-build.azurewebsites.net/ui/sonic/pipelines).
2. Navigate to the `vs` pipeline → latest successful master build → Artifacts.
3. Download `target/docker-sonic-vs.gz`.
4. `docker load -i docker-sonic-vs.gz`.
5. `docker images | grep sonic` to confirm the image landed; note its tag.
6. Update `hash_polarization.clab.yaml` and `scripts/check_setup.py` to reference the actual loaded image name.

**Option B — release branch build:** Same procedure, different pipeline. Pin to a release branch (e.g., `202311`, `202405`) for stability; master for the latest features.

**Option C — vrnetlab + sonic-vm:** Use the VM kind instead of the container kind. Heavier (full QEMU), more realistic, slower spin-up. Defer unless `sonic-vs` proves insufficient.

## Predicted blockers

Lower-risk than AIR Scout — no external auth, no enrollment gates. Plausible friction:

- **Azure pipeline artifact access** may require navigating their UI; one-time download (~600 MB) so not a recurring cost.
- **WSL2 + Docker Desktop integration**. Well-trodden, but containerlab needs to talk to Docker; if WSL2 integration isn't enabled in Docker Desktop's settings, deploy stalls.
- **Resource pressure**. 2-leaf 1-spine 4-host sonic-vs topology: estimate ~3 GB RAM (each SONiC container ~500 MB; hosts ~50 MB each). 4-leaf 2-spine 8-host: ~7 GB. Within the 31.2 GB the workspace machine has.
- **Cold-start BGP convergence**. SONiC's default config may need BGP up before per-port counters are meaningful for an ECMP polarization scenario. Wait-for-ready signal needs empirical confirmation.

## What was scaffolded before the SONiC image is in hand

Driver module shape, Adapter shell stub, hermetic tests using `subprocess.run` mocks. `ContainerlabClient` deploy/destroy/inspect are concretely implemented (subprocess wrappers, honest error handling). Higher-level tool methods raise `NotImplementedError("pending Stage A Scout observations")` until live SONiC telemetry shapes the parsers.

Topology specs under `src/containerlab_adapter/topologies/`. First example: `hash_polarization.clab.yaml` (placeholder dimensions, `sonic-vs` kind, image name to be updated post-load).
