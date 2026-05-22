"""Shared node-identification helpers used by topology + counters.

Kept module-private (underscore-prefixed) because the contract is
between driver tools, not part of the public surface. If a third tool
ever needs richer identity logic, promote to a real ``node.py`` module.
"""

from __future__ import annotations


ROLE_PREFIXES = ("leaf", "spine", "host")
# "switch" is the generic fallback role that classify_role assigns to any
# non-host kind whose name doesn't carry a leaf/spine prefix (e.g. "sw1"
# in the sonic-substrate-recipe vrspike-1port reference). Fabric tools
# include it so single-switch debug topologies get picked up.
SWITCH_ROLES = ("leaf", "spine", "switch")


def classify_role(name: str, kind: str) -> str:
    """Map a node name + containerlab kind onto a fabric role.

    Name-prefix is primary (our scenario YAMLs name nodes
    ``leafN``/``spineN``/``hostN``). Kind is the fallback: ``linux``
    collapses to host; anything else collapses to ``switch``.
    """
    lower = name.lower()
    for prefix in ROLE_PREFIXES:
        if lower.startswith(prefix):
            return prefix
    return "host" if kind == "linux" else "switch"


def strip_lab_prefix(container_name: str, lab_name: str) -> str:
    """``clab-hash-polarization-leaf1`` -> ``leaf1``.

    Containerlab prefixes every container name with ``clab-<lab>-``.
    The prefix carries the scenario identifier, which §6.5 says must
    not appear in per-record data.
    """
    prefix = f"clab-{lab_name}-"
    if container_name.startswith(prefix):
        return container_name[len(prefix):]
    return container_name
