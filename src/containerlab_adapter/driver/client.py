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
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CONTAINERLAB_BIN = "containerlab"
DOCKER_BIN = "docker"


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

    def exec_on_node(self, container_name: str, cmd: str) -> str:
        """Run a shell command inside a running container; return stdout.

        Uses ``docker exec ... bash -lc`` to match the scout-side
        capture pattern (the netreplica SONiC image expects a login
        shell for the ``show`` aliases to resolve). Stderr is captured
        separately and surfaced on :class:`ContainerlabError`; some
        SONiC ``show`` commands emit a benign ``/bin/sh: sudo: not
        found`` warning to stderr that callers should ignore.

        ``container_name`` is the full ``clab-<lab>-<node>`` identifier
        (the same name ``containerlab inspect`` returns).
        """
        argv = [DOCKER_BIN, "exec", container_name, "bash", "-lc", cmd]
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError as exc:
            raise ContainerlabError(
                f"docker binary not found in PATH ({exc})",
                cmd=argv,
                returncode=-1,
            ) from exc

        if result.returncode != 0:
            raise ContainerlabError(
                f"docker exec on {container_name!r} failed "
                f"(exit code {result.returncode}): {cmd!r}",
                cmd=argv,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result.stdout
