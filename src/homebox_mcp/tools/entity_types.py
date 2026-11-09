"""Entity type tools (e.g. 'Item', 'Location', or custom types such as 'Box')."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homebox_mcp.client import HomeBoxClient, HomeBoxError
from homebox_mcp.tools._common import DELETE, READ, UPDATE, WRITE, drop_empty

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.config import Settings


def _summary(t: dict[str, Any]) -> dict[str, Any]:
    return drop_empty(
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "is_location": bool(t.get("isLocation")),
            "icon": t.get("icon"),
            "description": t.get("description"),
            "default_template_id": t.get("defaultTemplateId"),
        }
    ) | {"is_location": bool(t.get("isLocation"))}


def register(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    @mcp.tool(annotations=READ)
    async def list_entity_types() -> list[dict[str, Any]]:
        """List entity types. Types with is_location=true are locations (containers);
        the others are items."""
        return [_summary(t) for t in await hb.entity_types()]

    if settings.mcp_read_only:
        return

    @mcp.tool(annotations=WRITE)
    async def create_entity_type(
        name: str, is_location: bool = False, icon: str | None = None
    ) -> dict[str, Any]:
        """Create a new entity type."""
        body = {"name": name, "isLocation": is_location, "icon": icon}
        created = await hb.post(
            "/entity-types", json={k: v for k, v in body.items() if v is not None}
        )
        return _summary(created)

    @mcp.tool(annotations=UPDATE)
    async def update_entity_type(
        entity_type_id: str,
        name: str | None = None,
        is_location: bool | None = None,
        icon: str | None = None,
    ) -> dict[str, Any]:
        """Update an entity type. Unspecified fields are preserved."""
        current = next((t for t in await hb.entity_types() if t.get("id") == entity_type_id), None)
        if current is None:
            raise HomeBoxError(404, f"entity type {entity_type_id} not found")
        body = {
            "id": entity_type_id,
            "name": name if name is not None else current.get("name"),
            "isLocation": is_location if is_location is not None else current.get("isLocation"),
            "icon": icon if icon is not None else current.get("icon"),
            "defaultTemplateId": current.get("defaultTemplateId") or None,
        }
        return _summary(await hb.put(f"/entity-types/{entity_type_id}", json=body))

    @mcp.tool(annotations=DELETE)
    async def delete_entity_type(entity_type_id: str) -> dict[str, Any]:
        """Delete an entity type. HomeBox refuses if entities still use it."""
        await hb.delete(f"/entity-types/{entity_type_id}")
        return {"deleted": entity_type_id}
