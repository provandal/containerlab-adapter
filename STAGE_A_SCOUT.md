# Stage A Scout — containerlab + Cumulus VX

Updated 2026-05-15.

## What we know

**containerlab** is an open-source CLI tool (BSD-3-Clause, originally Nokia SR Linux team, now community-maintained) that declaratively deploys multi-node network topologies as Docker containers. Native support for Cumulus VX, SONiC, FRR, Nokia SR Linux, Arista cEOS, Juniper cRPD, and others.

**Cumulus VX** is NVIDIA's free Cumulus Linux virtual appliance (subject to the [Cumulus VX EULA](https://www.nvidia.com/en-us/networking/ethernet-switching/cumulus-vx/), free for non-production use). It runs Cumulus Linux unmodified — the same NOS image enterprises run in production on Mellanox Spectrum hardware. Telemetry surface: `nv show ... --json`, `cl-counters`, `ethtool -S`, `nv show qos buffer-pool --json`.

**Local environment confirmed**: Docker Desktop 28.4 + WSL2 2.6.1 are installed on this workspace machine. containerlab itself is **not yet installed** — that's a WSL2-side install (`bash -c "$(curl -sL https://get.containerlab.dev)"`).

## What we expect

The substrate-substitution claim should hold cleanly: the Driver wraps `containerlab deploy/destroy/inspect` plus SSH-into-each-node for telemetry, then maps the responses onto the same MCP envelope shape Doppelgänger uses. The agent should see no difference in tool-call interface.

## What this Scout will confirm

1. **containerlab install lands clean** on WSL2 + Docker Desktop integration.
2. **Cumulus VX image pulls** (`docker pull networkop/cx:5.0`, ~700 MB).
3. **A minimum 2-leaf 1-spine 4-host topology deploys** from a `.clab.yaml` spec in seconds.
4. **SSH into a leaf works** (containerlab auto-distributes keys per-topology; default user `cumulus`).
5. **`nv show interface --json`** returns parseable output. Shape captured to `scout-outputs/` for parser design.
6. **`cl-counters`** and **`ethtool -S`** return parseable output.
7. **Teardown is clean** (`containerlab destroy --cleanup` removes containers + auto-generated directories).

Output saved to `scout-outputs/` (gitignored). Parser implementations in `src/containerlab_adapter/driver/` get filled in against the captured shapes.

## Predicted blockers

Lower risk than AIR Scout — no external auth, no enrollment gates. Plausible friction points:

- **WSL2 + Docker Desktop integration glitches.** Generally well-trodden, but containerlab needs to talk to Docker; if Docker Desktop's WSL2 integration isn't enabled, this stalls until it is.
- **Cumulus VX image pull rate-limiting** if the Docker Hub anonymous pull limit is exceeded. Unlikely for one image but possible.
- **Resource pressure on small topologies.** A 2-leaf 1-spine 4-host Cumulus VX topo uses ~3.5 GB RAM (each Cumulus VX container ~500 MB; hosts ~50 MB each). Should fit comfortably; a 4-leaf 2-spine 8-host topology would be ~8 GB. Within laptop limits.

## What was scaffolded before install

Driver module shape, Adapter shell stub, hermetic tests using `subprocess.run` mocks. The `ContainerlabClient` class implements `deploy/destroy/inspect` concretely since they're transparent CLI wrappers; the higher-level tool methods (`get_topology`, `get_fabric_counters`, etc.) raise `NotImplementedError("pending Stage A Scout observations")` until live Cumulus VX telemetry shapes the parsers.

Topology specs live under `src/containerlab_adapter/topologies/`. First example: `hash_polarization.clab.yaml` (placeholder for the MVP scenario).
