"""Response envelope helper — mirrors Doppelgänger v0.3 §2.3.

Every Driver tool wraps its payload in the standard envelope HarnessIT
expects. Centralizing it here keeps the shape identical across tools and
the leak-prevention rules (§6.5) auditable from one place.

For live substrates the semantics differ slightly from the simulated
case Doppelgänger documents:

- ``observed_at_ns`` is wall-clock (``time.time_ns()``) rather than
  simulation time. Agents reasoning about temporal relationships across
  multiple tool calls compare these directly.
- ``confidence`` is still ``"high"``: containerlab CLI output is the
  authoritative source for fabric structure. There is no measurement
  uncertainty at the substrate edge.
"""

from __future__ import annotations

from typing import Any


def envelope(
    data: Any,
    *,
    source: str,
    observed_at_ns: int | None = None,
    staleness_class: str = "fresh",
    confidence: str = "high",
) -> dict[str, Any]:
    return {
        "data": data,
        "observed_at_ns": observed_at_ns,
        "source": source,
        "confidence": confidence,
        "staleness_class": staleness_class,
    }
