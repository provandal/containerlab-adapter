"""containerlab topology YAML files.

Each ``.clab.yaml`` file is a complete declarative spec for a
multi-node containerlab deployment: nodes (Cumulus VX, Linux hosts),
links (port-to-port adjacency), and per-node startup configs.

Scenario modules reference these files by path; the
:class:`ContainerlabClient` deploys them.
"""
