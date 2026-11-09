"""Read-only reporting tools: statistics, CSV export, server status."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from homebox_mcp.client import HomeBoxClient
from homebox_mcp.tools._common import READ, drop_empty

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.config import Settings

Date = Annotated[str | None, Field(description="Date as YYYY-MM-DD")]


def register(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    @mcp.tool(annotations=READ)
    async def get_statistics() -> dict[str, Any]:
        """Inventory statistics: totals, value, and per-location / per-tag breakdowns."""
        overview, by_location, by_tag = await asyncio.gather(
            hb.get("/groups/statistics"),
            hb.get("/groups/statistics/locations"),
            hb.get("/groups/statistics/tags"),
        )
        return {
            "overview": overview,
            "by_location": by_location or [],
            "by_tag": by_tag or [],
        }

    @mcp.tool(annotations=READ)
    async def get_purchase_value_over_time(start: Date = None, end: Date = None) -> Any:
        """Cumulative purchase value of the inventory over a date range."""
        return await hb.get(
            "/groups/statistics/purchase-price", params={"start": start, "end": end}
        )

    @mcp.tool(annotations=READ)
    async def export_inventory_csv(
        max_chars: Annotated[int, Field(ge=1000, le=500_000)] = 50_000,
    ) -> dict[str, Any]:
        """Export the whole inventory as CSV text (HomeBox import format)."""
        csv_text: str = await hb.get("/entities/export", expect="text") or ""
        truncated = len(csv_text) > max_chars
        return {
            "rows": max(csv_text.count("\n"), 0),
            "truncated": truncated,
            "csv": csv_text[:max_chars],
        }

    @mcp.tool(annotations=READ)
    async def get_server_status() -> dict[str, Any]:
        """HomeBox server version and health."""
        s = await hb.status()
        build = s.get("build") or {}
        return drop_empty(
            {
                "healthy": s.get("health"),
                "title": s.get("title"),
                "version": build.get("version"),
                "commit": build.get("commit"),
                "latest_version": (s.get("latest") or {}).get("version"),
                "message": s.get("message"),
                "mcp_read_only": settings.mcp_read_only,
                "homebox_url": hb.base_url,
            }
        )
