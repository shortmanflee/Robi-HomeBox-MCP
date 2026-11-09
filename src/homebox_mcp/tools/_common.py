"""Shared helpers for tool modules: annotations and response shaping."""

from __future__ import annotations

from typing import Any

from mcp.types import ToolAnnotations

READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, open_world_hint=False)
UPDATE = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False
)
DELETE = ToolAnnotations(
    read_only_hint=False, destructive_hint=True, idempotent_hint=True, open_world_hint=False
)

_ZERO_DATE_PREFIX = "0001-01-01"
NIL_UUID = "00000000-0000-0000-0000-000000000000"


def real_id(value: Any) -> str | None:
    """HomeBox uses the nil UUID for 'unset' references; map it (and '') to None."""
    return None if not value or value == NIL_UUID else str(value)


def drop_empty(d: dict[str, Any]) -> dict[str, Any]:
    """Remove None values, empty strings/lists and Go zero-dates to keep responses compact."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if v is None or v == "" or v == []:
            continue
        if isinstance(v, str) and (v.startswith(_ZERO_DATE_PREFIX) or v == NIL_UUID):
            continue
        out[k] = v
    return out


def ref(obj: dict[str, Any] | None) -> dict[str, Any] | None:
    if not obj or not real_id(obj.get("id")):
        return None
    return {"id": obj["id"], "name": obj.get("name")}


def summarize_entity(e: dict[str, Any]) -> dict[str, Any]:
    etype = e.get("entityType") or {}
    return drop_empty(
        {
            "id": e.get("id"),
            "name": e.get("name"),
            "type": etype.get("name"),
            "is_location": bool(etype.get("isLocation")),
            "parent": ref(e.get("parent")),
            "quantity": e.get("quantity"),
            "item_count": e.get("itemCount") if etype.get("isLocation") else None,
            "asset_id": e.get("assetId") if e.get("assetId") not in ("0", "000-000") else None,
            "tags": [t.get("name") for t in e.get("tags") or []],
            "archived": e.get("archived") or None,
            "purchase_price": e.get("purchasePrice") or None,
        }
    )


def summarize_attachment(a: dict[str, Any]) -> dict[str, Any]:
    return drop_empty(
        {
            "id": a.get("id"),
            "title": a.get("title"),
            "type": a.get("type"),
            "mime_type": a.get("mimeType"),
            "primary": a.get("primary") or None,
        }
    )


def entity_detail(e: dict[str, Any]) -> dict[str, Any]:
    """Full entity view minus noisy internals (image ids, raw thumbnails)."""
    etype = e.get("entityType") or {}
    detail = {
        "id": e.get("id"),
        "name": e.get("name"),
        "description": e.get("description"),
        "type": ref(etype),
        "is_location": bool(etype.get("isLocation")),
        "parent": ref(e.get("parent")),
        "location": ref(e.get("location")),
        "quantity": e.get("quantity"),
        "asset_id": e.get("assetId"),
        "archived": e.get("archived"),
        "insured": e.get("insured"),
        "tags": [ref(t) for t in e.get("tags") or []],
        "manufacturer": e.get("manufacturer"),
        "model_number": e.get("modelNumber"),
        "serial_number": e.get("serialNumber"),
        "notes": e.get("notes"),
        "purchase_date": e.get("purchaseDate"),
        "purchase_from": e.get("purchaseFrom"),
        "purchase_price": e.get("purchasePrice"),
        "total_price": e.get("totalPrice"),
        "warranty_expires": e.get("warrantyExpires"),
        "warranty_details": e.get("warrantyDetails"),
        "lifetime_warranty": e.get("lifetimeWarranty"),
        "sold_date": e.get("soldDate"),
        "sold_to": e.get("soldTo"),
        "sold_price": e.get("soldPrice"),
        "sold_notes": e.get("soldNotes"),
        "custom_fields": {
            f.get("name"): _field_value(f) for f in e.get("fields") or [] if f.get("name")
        },
        "attachments": [summarize_attachment(a) for a in e.get("attachments") or []],
        "children": [summarize_entity(c) for c in e.get("children") or []],
        "item_count": e.get("itemCount"),
        "created_at": e.get("createdAt"),
        "updated_at": e.get("updatedAt"),
    }
    # Omit false booleans (archived/insured/...) for brevity, but always keep is_location.
    keep = {k: v for k, v in detail.items() if v is not False or k == "is_location"}
    return drop_empty(keep)


def _field_value(f: dict[str, Any]) -> Any:
    t = f.get("type")
    if t == "number":
        return f.get("numberValue")
    if t == "boolean":
        return f.get("booleanValue")
    return f.get("textValue")
