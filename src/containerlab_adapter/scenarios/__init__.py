"""Scenario definitions for the containerlab Adapter.

Each scenario declares its agent-facing symptom, ground-truth root
cause (held by the runner only), and a reference to the containerlab
topology YAML under :mod:`containerlab_adapter.topologies`.

The ``REGISTRY`` maps the public scenario name (the string a caller
passes to ``run_scenario``) onto the scenario module. List entries
explicitly rather than auto-discovering — explicit makes the
"shipped scenarios" surface obvious from one file. Add new scenarios
here.
"""

from containerlab_adapter.scenarios import hash_polarization


REGISTRY = {
    hash_polarization.NAME: hash_polarization,
}


__all__ = ["REGISTRY", "hash_polarization"]
