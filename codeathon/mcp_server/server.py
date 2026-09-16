"""Expose parsed Hypothesis2Omics GEO data through a read-only MCP server.

The server reads ``HYPOTHESIS2OMICS_GEO_PARSED_ROOT`` when set and otherwise
uses ``data/geo_cache/parsed``. It uses stdio by default and performs no network
requests or data writes.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

try:
    from mcp_server.geo_tools import DEFAULT_PARSED_ROOT, GeoDataStore
except ModuleNotFoundError as exc:
    if exc.name != "mcp_server":
        raise
    from geo_tools import DEFAULT_PARSED_ROOT, GeoDataStore  # type: ignore[no-redef]

PARSED_ROOT_ENV = "HYPOTHESIS2OMICS_GEO_PARSED_ROOT"


def create_server(parsed_root: str | Path | None = None) -> FastMCP:
    """Create a server bound to one parsed GEO output root."""
    configured_root = parsed_root or os.environ.get(PARSED_ROOT_ENV, DEFAULT_PARSED_ROOT)
    store = GeoDataStore(configured_root)
    server = FastMCP(
        name="hypothesis2omics",
        instructions=(
            "Read-only access to provenance-backed GEO analysis units. "
            "Use list_analysis_units first to discover stable unit IDs."
        ),
    )

    @server.tool
    def list_analysis_units() -> dict[str, Any]:
        """List every parsed GEO analysis unit and its sample/probe counts."""
        return store.list_analysis_units()

    @server.tool
    def get_analysis_unit(unit_id: str) -> dict[str, Any]:
        """Get one unit's provenance, output hashes, and available metadata fields."""
        return store.get_analysis_unit(unit_id)

    @server.tool
    def get_sample_metadata(
        unit_id: str,
        gsm_accessions: list[str] | None = None,
        attributes: list[str] | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Get a filtered, paginated page of linked ImmPort and GEO sample metadata."""
        return store.get_sample_metadata(
            unit_id,
            gsm_accessions=gsm_accessions,
            attributes=attributes,
            limit=limit,
            offset=offset,
        )

    @server.tool
    def get_expression_info(unit_id: str) -> dict[str, Any]:
        """Get expression dimensions, samples, path, and hash without matrix values."""
        return store.get_expression_info(unit_id)

    return server


mcp = create_server()


if __name__ == "__main__":
    mcp.run(transport="stdio")
