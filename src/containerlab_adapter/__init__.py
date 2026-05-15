"""HarnessIT Substrate Adapter for containerlab + Cumulus VX.

Driver/Adapter split per Doppelgänger v0.3 §9.1:

- :mod:`containerlab_adapter.driver` — talks to the local
  ``containerlab`` CLI (via subprocess) and Cumulus VX nodes (via
  SSH). Reusable from a REPL or test without MCP scaffolding.
- :mod:`containerlab_adapter.adapter` — MCP server that imports
  Driver methods and exposes them as agent-facing tools.

The MCP contract HarnessIT consumes is identical to Doppelgänger's
(same tool names, same response envelope) — substrate substitution
is invisible to the harness.
"""

__version__ = "0.1.0a0"
