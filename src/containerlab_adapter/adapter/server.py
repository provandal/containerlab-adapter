"""MCP server — tool registration + Driver delegation, schema v1.1 conformant.

Five schema-defined tools (``get_topology`` / ``get_fabric_counters`` /
``get_flow_records`` / ``get_host_counters`` / ``run_scenario``) emit
substrate-schema v1.1 responses and self-validate via
``SubstrateAdapter.validate_response`` before returning. The legacy
``list_scenarios`` tool retains the Doppelgänger v0.3 §2.3 flat envelope
shape (substrate-specific extra, schema §2 principle 4).

containerlab-adapter's session model is stateful: a SONiC fabric takes
~60 s to bootstrap after deploy and we cannot re-deploy on every tool
call. The pattern remains:

1. Caller invokes ``run_scenario(scenario_name)``. The server lazily
   constructs a :class:`ContainerlabClient` bound to that scenario's
   topology YAML and stashes it on the server. Deploy runs once.
2. Subsequent ``get_*`` calls reuse the bound client.
3. ``list_scenarios`` is a pure registry walk and works regardless of
   whether a lab is deployed.

``get_flow_records`` is exposed for schema conformance but returns
``flows: []`` with ``envelope.staleness_class: "unsupported"`` and
``envelope.confidence: "low"`` (schema §3.3) — the containerlab
substrate has no flow trace today.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from substrate_schema import SubstrateAdapter

from containerlab_adapter import scenarios as _scenarios
from containerlab_adapter.adapter import schema_v1 as t
from containerlab_adapter.driver import (
    ContainerlabClient,
    get_fabric_counters as _driver_get_fabric_counters,
    get_host_counters as _driver_get_host_counters,
    get_topology as _driver_get_topology,
    list_scenarios as _driver_list_scenarios,
    run_scenario as _driver_run_scenario,
)

_DEFAULT_SERVER_NAME = "containerlab-substrate-adapter"


class NoActiveDeploymentError(RuntimeError):
    """Raised when a get_* tool is invoked before ``run_scenario``.

    The session model requires a deployed lab before counter/topology
    queries make sense. Fail loud rather than return an empty envelope
    that the agent would have to second-guess.
    """


def _extract_lab_name(legacy_envelope: dict[str, Any]) -> str:
    """Pull lab_name from a legacy-envelope source string.

    Source format the driver emits is consistently
    ``"containerlab.<tool>({lab_name})"`` (see ``driver/topology.py``,
    ``driver/counters.py``, ``driver/scenarios.py``). Returning the
    empty string when the source doesn't match is safe — the envelope
    builder accepts any string.
    """
    source = legacy_envelope.get("source", "")
    if "(" in source and ")" in source:
        return source.split("(", 1)[1].rsplit(")", 1)[0]
    return ""


def build_server(
    *,
    client: ContainerlabClient | None = None,
    server_name: str = _DEFAULT_SERVER_NAME,
) -> FastMCP:
    """Construct and return a FastMCP server with the Adapter tools.

    Parameters
    ----------
    client:
        Optional pre-bound :class:`ContainerlabClient`. Tests inject a
        fake client here to exercise tool wiring without touching the
        containerlab CLI. In production this is ``None`` — the server
        lazily binds a real client on the first ``run_scenario`` call.
    server_name:
        MCP server identity announced to clients.
    """
    session: dict[str, ContainerlabClient | None] = {"client": client}
    # Per-server cache of run_id → scenario_name so read tools called
    # with run_id only can resolve which scenario was last deployed.
    run_id_to_scenario_name: dict[str, str] = {}

    server = FastMCP(server_name)

    def _require_client() -> ContainerlabClient:
        bound = session["client"]
        if bound is None:
            raise NoActiveDeploymentError(
                "No active deployment — call run_scenario before "
                "get_topology / get_fabric_counters / get_host_counters / "
                "get_flow_records."
            )
        return bound

    def _resolve_read_args(
        scenario_name: str | None,
        run_id: str | None,
    ) -> str | None:
        """Resolve (scenario_name, run_id) → scenario_name or None.

        For containerlab-adapter the session model means there's only
        ever one active scenario at a time; ``scenario_name`` and
        ``run_id`` are accepted for schema-conformance but neither is
        required (the bound client knows what's running). The
        ``run_id`` lookup populates from ``run_scenario``.
        """
        if scenario_name is not None:
            return scenario_name
        if run_id is not None:
            return run_id_to_scenario_name.get(run_id)
        return None

    @server.tool()
    def list_scenarios() -> dict[str, Any]:
        """List the named scenarios this Adapter can run.

        Substrate-specific tool (not schema-conformant). Response uses
        the legacy Doppelgänger v0.3 §2.3 flat envelope shape.
        """
        return _driver_list_scenarios()

    @server.tool()
    def run_scenario(
        scenario_name: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Deploy the named scenario's topology and return a schema response.

        Schema-conformant per §3.5. The response carries ``envelope`` +
        ``run`` (scenario_name + run_id + status + timing). The agent
        provided ``scenario_name`` so echoing it back is not a leak.

        Lazily constructs the session client from the scenario's
        packaged YAML when no client has been bound yet. If a client is
        already bound to a different topology, raises ``ValueError`` —
        the v0.1 server runs one lab per session.
        """
        if scenario_name not in _scenarios.REGISTRY:
            raise ValueError(
                f"Unknown scenario {scenario_name!r}. "
                f"Known scenarios: {sorted(_scenarios.REGISTRY)}"
            )

        scenario_topology_path = Path(
            _scenarios.REGISTRY[scenario_name].topology_path()
        ).resolve()
        bound = session["client"]
        if bound is None:
            session["client"] = ContainerlabClient(
                topology_path=scenario_topology_path
            )
        elif bound.topology_path != scenario_topology_path:
            raise ValueError(
                f"Server is bound to {bound.topology_path}; cannot run "
                f"scenario {scenario_name!r} (topology "
                f"{scenario_topology_path}). Construct a new server per "
                f"scenario."
            )

        legacy = _driver_run_scenario(session["client"], scenario_name, run_id)
        data = legacy.get("data") or {}
        lab_name = _extract_lab_name(legacy)
        resolved_run_id = data.get("run_id") or (run_id or "")
        run_id_to_scenario_name[resolved_run_id] = scenario_name
        payload = t.translate_run_scenario(
            scenario_name=scenario_name,
            run_id=resolved_run_id,
            wall_clock_seconds=float(data.get("wall_clock_seconds") or 0.0),
            lab_name=lab_name,
            observed_at_ns=legacy.get("observed_at_ns"),
        )
        SubstrateAdapter.validate_response(payload, "run_scenario")
        return payload

    @server.tool()
    def get_topology(
        scenario_name: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return the fabric graph as a schema TopologyResponse.

        Cross-references ``containerlab inspect`` runtime state with
        the topology YAML's declared link adjacency. Schema-conformant
        per §3.1: ``{envelope, topology: {nodes, links}}``. Per §3.5.1
        the tool accepts ``scenario_name`` or ``run_id`` but neither is
        required — the bound client knows what's deployed.

        Raises :class:`NoActiveDeploymentError` if no scenario is running.
        """
        _ = _resolve_read_args(scenario_name, run_id)
        legacy = _driver_get_topology(_require_client())
        payload = t.translate_topology(
            legacy.get("data") or {},
            lab_name=_extract_lab_name(legacy),
            observed_at_ns=legacy.get("observed_at_ns"),
        )
        SubstrateAdapter.validate_response(payload, "get_topology")
        return payload

    @server.tool()
    def get_fabric_counters(
        scenario_name: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot per-switch per-port fabric counters as schema response.

        Schema-conformant per §3.2 v1.1. Native parallel ``pfc_rx[8]``
        / ``pfc_tx[8]`` / ``pg_watermark_headroom[8]`` arrays fold into
        the per-queue records inside each port's ``queues``. PFC count
        fields rename to ``pfc_pause_*_count`` at the boundary.

        Raises :class:`NoActiveDeploymentError` if no scenario is running.
        """
        _ = _resolve_read_args(scenario_name, run_id)
        legacy = _driver_get_fabric_counters(_require_client())
        payload = t.translate_fabric_counters(
            legacy.get("data") or [],
            lab_name=_extract_lab_name(legacy),
            observed_at_ns=legacy.get("observed_at_ns"),
        )
        SubstrateAdapter.validate_response(payload, "get_fabric_counters")
        return payload

    @server.tool()
    def get_flow_records(
        scenario_name: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Return an empty schema FlowRecordsResponse with 'unsupported' staleness.

        containerlab-adapter has no flow trace today (Stage C work).
        Per schema §3.3, substrates without flow records return
        ``flows: []`` with ``envelope.confidence: "low"`` and
        ``envelope.staleness_class: "unsupported"`` — honest about
        capability rather than synthesizing zeros.

        Raises :class:`NoActiveDeploymentError` if no scenario is running.
        """
        _ = _resolve_read_args(scenario_name, run_id)
        client = _require_client()
        inspect = client.inspect()
        lab_name = next(iter(inspect), "") if inspect else ""
        payload = t.empty_flow_records(lab_name=lab_name)
        SubstrateAdapter.validate_response(payload, "get_flow_records")
        return payload

    @server.tool()
    def get_host_counters(
        scenario_name: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Snapshot per-host ``ethtool -S`` stats as schema HostCountersResponse.

        Schema-conformant per §3.4. Native flat ``stats`` dict folds
        into ``rx_packets`` / ``tx_packets`` / ``rx_drops`` / ``tx_drops``
        where canonical names are present; substrate-specific stat keys
        (``rx_queue_N_*``) sum into ``rx_drops`` when no canonical
        aggregate is available.

        Raises :class:`NoActiveDeploymentError` if no scenario is running.
        """
        _ = _resolve_read_args(scenario_name, run_id)
        legacy = _driver_get_host_counters(_require_client())
        payload = t.translate_host_counters(
            legacy.get("data") or [],
            lab_name=_extract_lab_name(legacy),
            observed_at_ns=legacy.get("observed_at_ns"),
        )
        SubstrateAdapter.validate_response(payload, "get_host_counters")
        return payload

    return server
