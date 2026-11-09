"""Entity tools. In HomeBox v0.27+ both items and locations are 'entities'.

Whether an entity is a location is decided by its entity type (`isLocation`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import Field

from homebox_mcp.client import HomeBoxClient, HomeBoxError
from homebox_mcp.tools._common import (
    DELETE,
    READ,
    UPDATE,
    WRITE,
    entity_detail,
    real_id,
    summarize_entity,
)

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.config import Settings

Id = Annotated[str, Field(description="HomeBox entity UUID")]
Date = Annotated[str | None, Field(description="Date as YYYY-MM-DD")]
FieldValue = str | float | bool

# EntityOut keys that map 1:1 onto EntityUpdate keys.
_PASSTHROUGH = (
    "name", "description", "quantity", "insured", "archived", "assetId", "manufacturer",
    "modelNumber", "serialNumber", "notes", "lifetimeWarranty", "warrantyExpires",
    "warrantyDetails", "purchaseDate", "purchaseFrom", "purchasePrice", "soldDate", "soldTo",
    "soldPrice", "soldNotes", "syncChildEntityLocations",
)  # fmt: skip


def entity_update_payload(current: dict[str, Any]) -> dict[str, Any]:
    """Convert an EntityOut into a complete EntityUpdate body (PUT replaces all fields)."""
    payload: dict[str, Any] = {k: current.get(k) for k in _PASSTHROUGH if k in current}
    payload["id"] = current["id"]
    payload["entityTypeId"] = (current.get("entityType") or {}).get("id")
    payload["parentId"] = real_id((current.get("parent") or {}).get("id"))
    payload["locationId"] = real_id(current.get("locationId")) or real_id(
        (current.get("location") or {}).get("id")
    )
    payload["tagIds"] = [t["id"] for t in current.get("tags") or []]
    payload["fields"] = [dict(f) for f in current.get("fields") or []]
    return payload


def merge_custom_fields(
    existing: list[dict[str, Any]], updates: dict[str, FieldValue | None]
) -> list[dict[str, Any]]:
    """Merge {name: value} into HomeBox's field list. A None value removes the field."""
    by_name = {f.get("name"): f for f in existing}
    for name, value in updates.items():
        if value is None:
            by_name.pop(name, None)
            continue
        field = by_name.get(name) or {"name": name}
        if isinstance(value, bool):
            field.update(type="boolean", booleanValue=value)
        elif isinstance(value, int | float) and field.get("type") == "number":
            field.update(numberValue=int(value))
        else:
            field.update(type=field.get("type") or "text", textValue=str(value))
        by_name[name] = field
    return list(by_name.values())


async def _location_type_ids(hb: HomeBoxClient, *, location: bool) -> list[str]:
    return [t["id"] for t in await hb.entity_types() if bool(t.get("isLocation")) == location]


def register(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    # ------------------------------------------------------------------ read
    @mcp.tool(annotations=READ)
    async def search_entities(
        query: Annotated[
            str | None,
            Field(
                description="Free-text search over name, description, serial/model, "
                "manufacturer, notes, tags and custom fields. '#<assetId>' finds by asset id."
            ),
        ] = None,
        kind: Annotated[
            Literal["any", "items", "locations"],
            Field(description="Restrict to items or locations"),
        ] = "any",
        tag_ids: list[str] | None = None,
        match_all_tags: bool = False,
        parent_ids: Annotated[
            list[str] | None, Field(description="Only direct children of these entities")
        ] = None,
        entity_type_ids: list[str] | None = None,
        order_by: Literal["name", "createdAt", "updatedAt", "assetId"] | None = None,
        order_direction: Literal["asc", "desc"] | None = None,
        page: Annotated[int, Field(ge=1)] = 1,
        page_size: Annotated[int, Field(ge=1, le=100)] = 25,
    ) -> dict[str, Any]:
        """Search/list HomeBox items and locations. Returns compact summaries; use
        get_entity for full details."""
        type_ids = entity_type_ids
        if not type_ids and kind != "any":
            type_ids = await _location_type_ids(hb, location=kind == "locations")
        data = await hb.get(
            "/entities",
            params={
                "q": query,
                "tags": tag_ids,
                "matchAllTags": str(match_all_tags).lower() if tag_ids else None,
                "parentIds": parent_ids,
                "entityTypeIds": type_ids,
                "orderBy": order_by,
                "orderDirection": order_direction,
                "page": page,
                "pageSize": page_size,
            },
        )
        return {
            "total": data.get("total"),
            "page": data.get("page", page),
            "page_size": data.get("pageSize", page_size),
            "total_price": data.get("totalPrice"),
            "results": [summarize_entity(e) for e in data.get("items") or []],
        }

    @mcp.tool(annotations=READ)
    async def get_entity(entity_id: Id) -> dict[str, Any]:
        """Get full details of an item or location, including custom fields, attachment
        metadata, tags and children."""
        return entity_detail(await hb.get(f"/entities/{entity_id}"))

    @mcp.tool(annotations=READ)
    async def get_entity_path(entity_id: Id) -> list[dict[str, Any]]:
        """Get the breadcrumb path (root location -> ... -> entity) for an entity."""
        return await hb.get(f"/entities/{entity_id}/path") or []

    @mcp.tool(annotations=READ)
    async def get_location_tree(
        with_items: Annotated[bool, Field(description="Include items, not just locations")] = False,
    ) -> list[dict[str, Any]]:
        """Get the nested tree of locations (optionally with the items inside them)."""
        return await hb.get("/entities/tree", params={"withItems": str(with_items).lower()}) or []

    if settings.mcp_read_only:
        return

    # ----------------------------------------------------------------- write
    async def _create(body: dict[str, Any]) -> dict[str, Any]:
        body = {k: v for k, v in body.items() if v is not None}
        return entity_detail(await hb.post("/entities", json=body))

    @mcp.tool(annotations=WRITE)
    async def create_item(
        name: str,
        location_id: Annotated[
            str | None, Field(description="Parent location (or container entity) id")
        ] = None,
        description: str | None = None,
        quantity: float | None = None,
        manufacturer: str | None = None,
        model_number: str | None = None,
        tag_ids: list[str] | None = None,
        entity_type_id: Annotated[
            str | None, Field(description="Override the default 'Item' entity type")
        ] = None,
    ) -> dict[str, Any]:
        """Create an inventory item, optionally placed inside a location."""
        type_id = entity_type_id or (await hb.default_entity_type(location=False))["id"]
        return await _create(
            {
                "name": name,
                "entityTypeId": type_id,
                "parentId": location_id,
                "description": description,
                "quantity": quantity,
                "manufacturer": manufacturer,
                "modelNumber": model_number,
                "tagIds": tag_ids,
            }
        )

    @mcp.tool(annotations=WRITE)
    async def create_location(
        name: str,
        parent_id: Annotated[str | None, Field(description="Parent location id")] = None,
        description: str | None = None,
        entity_type_id: Annotated[
            str | None, Field(description="Override the default 'Location' entity type")
        ] = None,
    ) -> dict[str, Any]:
        """Create a location (room, shelf, box...), optionally nested in another location."""
        type_id = entity_type_id or (await hb.default_entity_type(location=True))["id"]
        return await _create(
            {
                "name": name,
                "entityTypeId": type_id,
                "parentId": parent_id,
                "description": description,
            }
        )

    @mcp.tool(annotations=UPDATE)
    async def update_entity(
        entity_id: Id,
        name: str | None = None,
        description: str | None = None,
        parent_id: Annotated[
            str | None, Field(description="Move under this location/entity; '' to unset")
        ] = None,
        entity_type_id: str | None = None,
        quantity: float | None = None,
        tag_ids: Annotated[
            list[str] | None, Field(description="Replaces the full tag list")
        ] = None,
        manufacturer: str | None = None,
        model_number: str | None = None,
        serial_number: str | None = None,
        asset_id: str | None = None,
        notes: str | None = None,
        insured: bool | None = None,
        archived: bool | None = None,
        purchase_date: Date = None,
        purchase_from: str | None = None,
        purchase_price: float | None = None,
        warranty_expires: Date = None,
        warranty_details: str | None = None,
        lifetime_warranty: bool | None = None,
        sold_date: Date = None,
        sold_to: str | None = None,
        sold_price: float | None = None,
        sold_notes: str | None = None,
        custom_fields: Annotated[
            dict[str, FieldValue | None] | None,
            Field(description="Custom fields to set by name; null value removes a field"),
        ] = None,
    ) -> dict[str, Any]:
        """Update an item or location. Only the arguments you pass are changed; everything
        else is preserved."""
        current = await hb.get(f"/entities/{entity_id}")
        payload = entity_update_payload(current)
        changes = {
            "name": name,
            "description": description,
            "entityTypeId": entity_type_id,
            "quantity": quantity,
            "tagIds": tag_ids,
            "manufacturer": manufacturer,
            "modelNumber": model_number,
            "serialNumber": serial_number,
            "assetId": asset_id,
            "notes": notes,
            "insured": insured,
            "archived": archived,
            "purchaseDate": purchase_date,
            "purchaseFrom": purchase_from,
            "purchasePrice": purchase_price,
            "warrantyExpires": warranty_expires,
            "warrantyDetails": warranty_details,
            "lifetimeWarranty": lifetime_warranty,
            "soldDate": sold_date,
            "soldTo": sold_to,
            "soldPrice": sold_price,
            "soldNotes": sold_notes,
        }
        payload.update({k: v for k, v in changes.items() if v is not None})
        if parent_id is not None:
            payload["parentId"] = parent_id or None
            # The stored location follows the new parent; let HomeBox recompute it.
            payload["locationId"] = None
        if custom_fields:
            payload["fields"] = merge_custom_fields(payload["fields"], custom_fields)
        return entity_detail(await hb.put(f"/entities/{entity_id}", json=payload))

    @mcp.tool(annotations=WRITE)
    async def duplicate_entity(
        entity_id: Id,
        copy_attachments: bool = True,
        copy_custom_fields: bool = True,
        copy_maintenance: bool = False,
        copy_prefix: str = "Copy of ",
    ) -> dict[str, Any]:
        """Duplicate an item or location."""
        return entity_detail(
            await hb.post(
                f"/entities/{entity_id}/duplicate",
                json={
                    "copyAttachments": copy_attachments,
                    "copyCustomFields": copy_custom_fields,
                    "copyMaintenance": copy_maintenance,
                    "copyPrefix": copy_prefix,
                },
            )
        )

    @mcp.tool(annotations=DELETE)
    async def delete_entity(entity_id: Id) -> dict[str, Any]:
        """Permanently delete an item or location. This cannot be undone."""
        try:
            await hb.delete(f"/entities/{entity_id}")
        except HomeBoxError as exc:
            if exc.status == 404:
                raise HomeBoxError(404, f"entity {entity_id} not found") from exc
            raise
        return {"deleted": entity_id}
