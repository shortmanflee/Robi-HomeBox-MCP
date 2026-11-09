"""Maintenance log tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from homebox_mcp.client import HomeBoxClient, HomeBoxError
from homebox_mcp.tools._common import DELETE, READ, UPDATE, WRITE, drop_empty

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.config import Settings

Date = Annotated[str | None, Field(description="Date as YYYY-MM-DD")]
Status = Literal["scheduled", "completed", "both"]


def _entry(m: dict[str, Any]) -> dict[str, Any]:
    return drop_empty(
        {
            "id": m.get("id"),
            "name": m.get("name"),
            "description": m.get("description"),
            "scheduled_date": m.get("scheduledDate"),
            "completed_date": m.get("completedDate"),
            "cost": m.get("cost") if m.get("cost") not in ("0", 0) else None,
            "entity_id": m.get("itemID"),
            "entity_name": m.get("itemName"),
        }
    )


def _cost(value: float | str | None) -> str | None:
    return None if value is None else str(value)


def register(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    @mcp.tool(annotations=READ)
    async def list_maintenance(
        entity_id: Annotated[
            str | None, Field(description="Only entries for this entity; omit for all")
        ] = None,
        status: Status = "both",
    ) -> list[dict[str, Any]]:
        """List maintenance entries, either for one item or across the whole inventory."""
        path = f"/entities/{entity_id}/maintenance" if entity_id else "/maintenance"
        return [_entry(m) for m in await hb.get(path, params={"status": status}) or []]

    if settings.mcp_read_only:
        return

    @mcp.tool(annotations=WRITE)
    async def create_maintenance(
        entity_id: str,
        name: str,
        description: str | None = None,
        scheduled_date: Date = None,
        completed_date: Date = None,
        cost: float | None = None,
    ) -> dict[str, Any]:
        """Log a maintenance entry for an item (scheduled and/or completed)."""
        body = {
            "name": name,
            "description": description,
            "scheduledDate": scheduled_date,
            "completedDate": completed_date,
            "cost": _cost(cost),
        }
        created = await hb.post(
            f"/entities/{entity_id}/maintenance",
            json={k: v for k, v in body.items() if v is not None},
        )
        return _entry(created) | {"entity_id": entity_id}

    @mcp.tool(annotations=UPDATE)
    async def update_maintenance(
        maintenance_id: str,
        name: str | None = None,
        description: str | None = None,
        scheduled_date: Date = None,
        completed_date: Date = None,
        cost: float | None = None,
    ) -> dict[str, Any]:
        """Update a maintenance entry. Unspecified fields are preserved."""
        entries = await hb.get("/maintenance", params={"status": "both"}) or []
        cur = next((m for m in entries if m.get("id") == maintenance_id), None)
        if cur is None:
            raise HomeBoxError(404, f"maintenance entry {maintenance_id} not found")
        body = {
            "name": cur.get("name"),
            "description": cur.get("description"),
            "scheduledDate": cur.get("scheduledDate"),
            "completedDate": cur.get("completedDate"),
            "cost": cur.get("cost"),
        }
        changes = {
            "name": name,
            "description": description,
            "scheduledDate": scheduled_date,
            "completedDate": completed_date,
            "cost": _cost(cost),
        }
        body.update({k: v for k, v in changes.items() if v is not None})
        return _entry(await hb.put(f"/maintenance/{maintenance_id}", json=body))

    @mcp.tool(annotations=DELETE)
    async def delete_maintenance(maintenance_id: str) -> dict[str, Any]:
        """Delete a maintenance entry."""
        await hb.delete(f"/maintenance/{maintenance_id}")
        return {"deleted": maintenance_id}
