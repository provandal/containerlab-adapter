# Stage A Scout — containerlab + SONiC

Updated 2026-05-18 (Stage A closed).

## Outcome

**Stage A partial — local stability is resource-limited; migrating to AWS.**

Working pieces:
- wsl-containerlab WSL2 distro (Debian 12, native Docker 27.5.1, containerlab 0.75.0 pre-installed)
- `netreplica/docker-sonic-vs:latest` image (community build for containerlab)
- 7-node placeholder topology (3 SONiC + 4 hosts) deploys cleanly
- `containerlab inspect --format json` returns canonical topology shape

Local death window pattern across iterations:

| Image / workaround | Stability before all-SONiC-die |
|---|---|
| Azure `docker-sonic-vs`, no workarounds | 2-3 min |
| Azure + `nohup supervisord -n` (SIGHUP mask) | 4-5 min |
| Azure + `setsid -w supervisord -n` (new session) | 6 min (some nodes) |
| `netreplica/docker-sonic-vs`, no workarounds | ~10-12 min |

Each fix moved the failure window but didn't eliminate it. All three SONiC nodes die simultaneously → correlated cause, not per-container config. Combined with the host (Windows + WSL2 + Docker + other concurrent experiments + IDE) at ~31 GB total RAM with visible typing lag, the most plausible read is **cgroup-level memory pressure** taking down the heaviest containers periodically.

**Decision (2026-05-18)**: migrate to AWS for substrate work. The skill design validation goal doesn't depend on running SONiC locally — it needs a stable Linux host with enough RAM. AWS gets us isolation + headroom without continued WSL2/Docker-Desktop debugging. See `AWS_SETUP.md` for the provisioning steps.

The original Azure-pipeline `docker-sonic-vs.gz` image (built for sonic-mgmt's testing harness, not containerlab) required multiple workarounds (cmd override, `setsid -w`, port-conflict race) and is **not** what we'll ship with. `netreplica/docker-sonic-vs` is the canonical containerlab-ready build per the netreplica/templates repository.

## Investigation trail (kept for the diagnosis record)

The full diagnostic journey is in `journal.md`. Highlights:

1. **wsl-containerlab + native Docker** — required because Docker Desktop's WSL2 integration puts the Docker daemon in a separate network namespace from containerlab; netlink lookups fail. Fixed by installing the srl-labs/wsl-containerlab WSL2 distro which ships native Docker.

2. **Subnet collision** — containerlab's default `172.20.20.0/24` collided with an existing Docker network. Pinned `172.100.100.0/24` in the YAML's `mgmt:` block.

3. **`docker-sonic-vs:latest` (Azure-pipeline) container instability** — surfaced three distinct issues stacked on each other:
   - Containerlab's `sonic-vs` kind hard-codes `Entrypoint=/bin/bash` with `Tty=true OpenStdin=true`. After deploy detaches, the controlling PTY drops and SIGHUP eventually kills PID 1. (Worked around with `cmd: "-c 'exec setsid -w /usr/local/bin/supervisord -n'"`).
   - SONiC's `start.sh` exits 1 at the `supervisorctl start rebootbackend` line because `rebootbackend` isn't a registered service in this image build. Doesn't kill the container directly, but is cosmetic noise.
   - On parallel multi-node deploy, supervisord port-conflict (`Another program is already listening on a port`) crashes some containers but not others — a race we couldn't fully diagnose in 90 minutes of debugging.

4. **Pivot to `netreplica/docker-sonic-vs`** — community build, no workarounds needed. Bare YAML, default containerlab kind config, 8+ minutes stable on multi-node deploy. This is the canonical recommended path per the netreplica/templates repository.

## Working YAML pattern

```yaml
name: hash-polarization

mgmt:
  network: clab-hash-polarization
  ipv4-subnet: 172.100.100.0/24
  ipv6-subnet: 3fff:172:100:100::/64

topology:
  kinds:
    sonic-vs:
      image: netreplica/docker-sonic-vs:latest
  nodes:
    spine1:
      kind: sonic-vs
    leaf1:
      kind: sonic-vs
    # ... etc
```

## What's in scout-outputs/

Captured from a running netreplica/docker-sonic-vs leaf:

- `inspect.json` — `containerlab inspect --format json` output (canonical topology shape the get_topology parser will target)
- `leaf1-supervisorctl.txt` — running SONiC services (bgpd, swss, syncd, etc.)
- `leaf1-show_interfaces_status.txt` — port enumeration + admin/oper state
- `leaf1-show_interfaces_counters.txt` — basic per-interface counters
- `leaf1-show_queue_counters.txt` — per-queue counters (the SONiC-shape rollup Doppelgänger v0.3 §4.1 was modeled on)
- `leaf1-show_pfc_counters.txt` — PFC pause-frame counters
- `leaf1-show_ip_bgp_summary.txt` — BGP neighbor state
- `leaf1-ip_link.txt` — Linux interface state including the containerlab-wired veths

These shapes drive the `get_topology`, `get_fabric_counters`, `get_host_counters` parser implementations in `src/containerlab_adapter/driver/`.



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
