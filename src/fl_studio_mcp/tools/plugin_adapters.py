"""Named plugin adapters backed by exact layouts and guarded parameter writes."""

from typing import Annotated

from pydantic import Field


def register_plugin_adapter_tools(mcp):
    @mcp.tool()
    def fl_plugin_adapter_catalog(query: str = "") -> dict:
        """List packaged plugin adapter evidence, exact layouts and named control coverage.

        Read-only and offline. Query matches adapter/plugin/control names. Numeric
        controls without calibrated points/analytic curves say calibration_required:
        physical units cannot be written. Numeric mappings require unit/tolerance;
        display_unit may differ from semantic unit. Enum labels may declare an
        exact display string, including whitespace. Conditions reference enum labels.
        An entry does not establish that this plugin/version is currently loaded.
        """
        from fl_studio_mcp.utils.plugin_adapters import catalog_adapters

        return catalog_adapters(query)

    @mcp.tool()
    def fl_plugin_controls_inspect(
        index: Annotated[int, Field(ge=0, le=100000, strict=True)],
        slot_index: Annotated[int, Field(ge=-1, le=9, strict=True)] = -1,
        query: str = "",
        offset: Annotated[int, Field(ge=0, le=10000, strict=True)] = 0,
        limit: Annotated[int, Field(ge=1, le=500, strict=True)] = 100,
    ) -> dict:
        """Inspect named controls only after exact plugin, full-layout and parameter-name binding.

        index is a global channel for slot_index=-1, otherwise a mixer track and
        effect slot 0..9. Collects a complete revision-consistent named catalog
        before filtering/pagination; refuses unknown or changed layouts. Returns
        native normalized values, literal displays, calibration metadata and a
        fresh complete-state revision and current condition availability. Host
        caches may be stale after preset loads.
        Identical names/layouts cannot prove a binary/build version; review evidence.
        No parameter sweeps, probing or writes; no sound/DSP guarantee.
        """
        from fl_studio_mcp.utils.plugin_adapters import inspect_controls

        return inspect_controls(index, slot_index, query, offset, limit)

    @mcp.tool()
    def fl_plugin_controls_set(
        index: Annotated[int, Field(ge=0, le=100000, strict=True)],
        expected_plugin: str,
        expected_revision: str,
        changes: list[dict],
        slot_index: Annotated[int, Field(ge=-1, le=9, strict=True)] = -1,
        apply: Annotated[bool, Field(strict=True)] = False,
        receipt_file: str | None = None,
    ) -> dict:
        """Preflight/apply named plugin controls through the guarded plugin batch workflow.

        Each distinct change is {control:key,value:physical_number_or_exact_enum_label}
        or {control:key,normalized:0..1}. Physical values require monotonic calibrated
        points or analytic curves plus unit/tolerance and numeric display checks;
        no extrapolation. The current display must be finite and unit-compatible;
        transition from Auto/INF using explicit normalized control, then inspect again.
        Enum labels map to their declared exact literal display.
        Explicit normalized writes do not claim physical-unit verification.
        Conditions require matching fresh enum displays, including for normalized
        writes. Change a condition selector separately, then inspect again before
        changing its dependent controls; the same batch cannot contain both.

        Requires fresh controls inspection and exact native expected_plugin. The
        complete layout/parameter mapping must still match. Preview by default;
        apply requires stopped transport and a NEW absolute JSON receipt. All
        changes preflight before one batch and two later readbacks. No automatic
        retries/rollback, calibration sweeps, or sonic/DSP-state guarantee.
        Name/layout matching cannot distinguish different builds with identical controls.
        """
        from fl_studio_mcp.utils.plugin_adapters import set_controls

        return set_controls(
            index, expected_plugin, expected_revision, changes, slot_index, apply, receipt_file,
        )
