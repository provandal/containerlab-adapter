"""ContainerlabClient — subprocess wrapper around the ``containerlab`` CLI.

The three operations the higher-level tools need are deploy, destroy,
and inspect. Each is a thin shell over ``containerlab <cmd> -t <file>``
that captures stdout (JSON where supported) and surfaces non-zero
exit codes as :class:`ContainerlabError`.

This class is implemented concretely (not a stub) because it's an
honest CLI wrapper — the contract is well-defined by containerlab's
own docs. The Stage A Scout work it depends on is just "confirm
containerlab is installed and works"; the wrapper itself doesn't
change shape based on Scout observations.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTAINERLAB_BIN = "containerlab"
DOCKER_BIN = "docker"
SSH_BIN = "ssh"
SSHPASS_BIN = "sshpass"

# Kinds whose `containerlab inspect` payload represents a Linux container
# the driver can exec into directly via `docker exec`. Anything else
# (currently only "sonic-vm") goes through the SSH-to-mgmt-IP path
# because the container is a vrnetlab QEMU host with the real SONiC OS
# running inside.
_DOCKER_EXEC_KINDS = frozenset({"linux", "sonic-vs"})
_SSH_EXEC_KINDS = frozenset({"sonic-vm"})

# SSH options for talking to a vrnetlab/sonic_sonic-vs VM. Password auth
# against the canonical admin/admin built into the upstream sonic-vs.img;
# StrictHostKeyChecking off because the mgmt IP rotates per deploy.
_SONIC_VM_SSH_USER = "admin"
_SONIC_VM_SSH_PASSWORD = "admin"
_SSH_OPTS = (
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "ConnectTimeout=15",
    "-o", "PreferredAuthentications=password",
    "-o", "PubkeyAuthentication=no",
    "-o", "LogLevel=ERROR",
)


class ContainerlabError(RuntimeError):
    """Raised when a containerlab subprocess call fails or its output
    can't be parsed.

    Carries the command, exit code, and stderr text so callers can
    diagnose without re-running.
    """

    def __init__(self, message: str, *, cmd: list[str], returncode: int, stderr: str = ""):
        super().__init__(message)
        self.cmd = cmd
        self.returncode = returncode
        self.stderr = stderr


@dataclass
class ContainerlabClient:
    """Wraps a single containerlab topology file.

    Each instance is bound to one ``topology_path`` (a ``.clab.yaml``
    file). Deploy/destroy/inspect operate on that topology.
    """

    topology_path: Path

    def __post_init__(self) -> None:
        self.topology_path = Path(self.topology_path).resolve()
        if not self.topology_path.exists():
            raise FileNotFoundError(
                f"containerlab topology file not found: {self.topology_path}"
            )

    # ---------- subprocess wrappers ----------

    def _run(self, args: list[str], *, parse_json: bool = False) -> Any:
        """Run ``containerlab`` with the given args. Raise on non-zero exit."""
        cmd = [CONTAINERLAB_BIN, *args]
        try:
            result = subprocess.run(
                cmd,
                check=False,  # we want to raise our own error type
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ContainerlabError(
                f"containerlab binary not found in PATH. Install it on this host "
                f"or in the relevant WSL2 distro. ({exc})",
                cmd=cmd,
                returncode=-1,
            ) from exc

        if result.returncode != 0:
            raise ContainerlabError(
                f"containerlab {args[0]} failed with exit code {result.returncode}",
                cmd=cmd,
                returncode=result.returncode,
                stderr=result.stderr,
            )

        if not parse_json:
            return result.stdout

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ContainerlabError(
                f"containerlab {args[0]} returned non-JSON output: {exc}",
                cmd=cmd,
                returncode=result.returncode,
                stderr=result.stderr,
            ) from exc

    def deploy(self) -> dict:
        """Deploy the topology. Returns the parsed inspect output."""
        return self._run(
            ["deploy", "-t", str(self.topology_path), "--format", "json"],
            parse_json=True,
        )

    def destroy(self, *, cleanup: bool = True) -> None:
        """Tear down the topology.

        When ``cleanup=True`` (the default), containerlab also removes
        the auto-generated directory of per-node state. Always call
        with cleanup at session end to keep the workspace clean.
        """
        args = ["destroy", "-t", str(self.topology_path)]
        if cleanup:
            args.append("--cleanup")
        self._run(args)

    def inspect(self) -> dict:
        """Return the current state of the topology (running nodes,
        IPs, MAC addresses, container ids)."""
        return self._run(
            ["inspect", "-t", str(self.topology_path), "--format", "json"],
            parse_json=True,
        )

    def exec_on_node(
        self,
        container_name: str,
        cmd: str,
        *,
        kind: str | None = None,
        mgmt_ip: str | None = None,
    ) -> str:
        """Run a shell command on a running node; return stdout.

        The transport branches on ``kind`` — the kind value containerlab's
        inspect output reports for the node. Linux-shaped containers
        (``linux``, legacy ``sonic-vs``) get ``docker exec ... bash -lc``;
        vrnetlab-style QEMU-backed containers (``sonic-vm``) get
        ``sshpass ssh admin@<mgmt_ip> bash -lc``. The login-shell wrapper
        is preserved on both branches so SONiC's ``show`` aliases resolve.

        When ``kind`` is ``None`` (legacy callers that haven't been
        updated), defaults to the docker-exec path. Callers that already
        have inspect data should pass ``kind`` + ``mgmt_ip`` for explicit
        routing.

        ``container_name`` is the full ``clab-<lab>-<node>`` identifier
        (the same name ``containerlab inspect`` returns).

        Stderr is captured separately and surfaced on
        :class:`ContainerlabError`; some SONiC ``show`` commands emit a
        benign ``/bin/sh: sudo: not found`` warning to stderr that
        callers should ignore.
        """
        if kind in _SSH_EXEC_KINDS:
            if not mgmt_ip:
                raise ContainerlabError(
                    f"exec_on_node({container_name!r}) for kind={kind!r} "
                    f"requires mgmt_ip — inspect's ipv4_address field "
                    f"must be threaded through from the caller",
                    cmd=[SSH_BIN, f"admin@?"],
                    returncode=-1,
                )
            wrapped = f"bash -lc {shlex.quote(cmd)}"
            argv = [
                SSHPASS_BIN, "-p", _SONIC_VM_SSH_PASSWORD,
                SSH_BIN, *_SSH_OPTS,
                f"{_SONIC_VM_SSH_USER}@{mgmt_ip}",
                wrapped,
            ]
            missing_binary_hint = "sshpass"
        else:
            argv = [DOCKER_BIN, "exec", container_name, "bash", "-lc", cmd]
            missing_binary_hint = "docker"

        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ContainerlabError(
                f"{missing_binary_hint} binary not found in PATH ({exc})",
                cmd=argv,
                returncode=-1,
            ) from exc

        if result.returncode != 0:
            transport = "ssh" if kind in _SSH_EXEC_KINDS else "docker exec"
            raise ContainerlabError(
                f"{transport} on {container_name!r} failed "
                f"(exit code {result.returncode}): {cmd!r}",
                cmd=argv,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result.stdout
