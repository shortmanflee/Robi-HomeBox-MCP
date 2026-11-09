"""Tag tools (HomeBox v0.27 renamed 'labels' to 'tags')."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field

from homebox_mcp.client import HomeBoxClient
from homebox_mcp.tools._common import DELETE, READ, UPDATE, WRITE, drop_empty, real_id

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.config import Settings

Color = Annotated[str | None, Field(description="Hex colour, e.g. #ff8800")]


def _tag(t: dict[str, Any]) -> dict[str, Any]:
    return drop_empty(
        {
            "id": t.get("id"),
            "name": t.get("name"),
            "description": t.get("description"),
            "color": t.get("color"),
            "icon": t.get("icon"),
            "parent_id": t.get("parentId"),
        }
    )


def register(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    @mcp.tool(annotations=READ)
    async def list_tags() -> list[dict[str, Any]]:
        """List all tags."""
        return [_tag(t) for t in await hb.get("/tags") or []]

    @mcp.tool(annotations=READ)
    async def get_tag(tag_id: str) -> dict[str, Any]:
        """Get a single tag."""
        return _tag(await hb.get(f"/tags/{tag_id}"))

    if settings.mcp_read_only:
        return

    @mcp.tool(annotations=WRITE)
    async def create_tag(
        name: str,
        description: str | None = None,
        color: Color = None,
        icon: str | None = None,
        parent_id: Annotated[str | None, Field(description="Parent tag id (nesting)")] = None,
    ) -> dict[str, Any]:
        """Create a tag."""
        body = {
            "name": name,
            "description": description,
            "color": color,
            "icon": icon,
            "parentId": parent_id,
        }
        return _tag(await hb.post("/tags", json={k: v for k, v in body.items() if v is not None}))

    @mcp.tool(annotations=UPDATE)
    async def update_tag(
        tag_id: str,
        name: str | None = None,
        description: str | None = None,
        color: Color = None,
        icon: str | None = None,
        parent_id: Annotated[str | None, Field(description="'' to unset")] = None,
    ) -> dict[str, Any]:
        """Update a tag. Unspecified fields are preserved."""
        cur = await hb.get(f"/tags/{tag_id}")
        body = {
            "id": tag_id,
            "name": cur.get("name"),
            "description": cur.get("description"),
            "color": cur.get("color"),
            "icon": cur.get("icon"),
            "parentId": real_id(cur.get("parentId")),
        }
        changes = {"name": name, "description": description, "color": color, "icon": icon}
        body.update({k: v for k, v in changes.items() if v is not None})
        if parent_id is not None:
            body["parentId"] = parent_id or None
        return _tag(await hb.put(f"/tags/{tag_id}", json=body))

    @mcp.tool(annotations=DELETE)
    async def delete_tag(tag_id: str) -> dict[str, Any]:
        """Delete a tag (items keep existing but lose the tag)."""
        await hb.delete(f"/tags/{tag_id}")
        return {"deleted": tag_id}
