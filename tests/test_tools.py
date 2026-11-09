from __future__ import annotations

import base64
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from httpx import Response

from homebox_mcp.tools.entities import merge_custom_fields

from .conftest import entity


def body(route, idx: int = -1) -> dict:
    return json.loads(route.calls[idx].request.content)


async def test_read_only_mode_hides_write_tools(server_factory):
    async with Client(server_factory(mcp_read_only=True)) as c:
        names = {t.name for t in await c.list_tools()}
    assert "search_entities" in names
    assert not {n for n in names if n.startswith(("create_", "update_", "delete_", "upload_"))}


async def test_destructive_annotation(mcp_client):
    tools = {t.name: t for t in await mcp_client.list_tools()}
    assert tools["delete_entity"].annotations.destructive_hint is True
    assert tools["get_entity"].annotations.read_only_hint is True


async def test_search_locations_resolves_type_ids(mcp_client, hb_mock):
    route = hb_mock.get("/entities").mock(
        return_value=Response(
            200,
            json={
                "items": [entity(id="loc1", name="Garage", entityType={"id": "type-loc",
                                 "name": "Location", "isLocation": True}, parent=None)],
                "total": 1, "page": 1, "pageSize": 25,
            },
        )
    )  # fmt: skip
    res = await mcp_client.call_tool("search_entities", {"kind": "locations", "query": "gar"})
    q = route.calls[0].request.url.params
    assert q.get_list("entityTypeIds") == ["type-loc"]
    assert q["q"] == "gar"
    assert res.data["total"] == 1
    assert res.data["results"][0] == {
        "id": "loc1", "name": "Garage", "type": "Location", "is_location": True,
        "quantity": 1, "tags": ["Tools"],
    }  # fmt: skip


async def test_get_entity_drops_noise(mcp_client, hb_mock):
    hb_mock.get("/entities/e1").mock(return_value=Response(200, json=entity()))
    res = (await mcp_client.call_tool("get_entity", {"entity_id": "e1"})).data
    assert res["custom_fields"] == {"Voltage": "18V"}
    assert "purchase_date" not in res  # Go zero date stripped
    assert "insured" not in res  # false booleans omitted
    assert res["is_location"] is False
    assert res["attachments"][0]["id"] == "att1"


async def test_create_item_uses_default_item_type(mcp_client, hb_mock):
    route = hb_mock.post("/entities").mock(return_value=Response(201, json=entity()))
    await mcp_client.call_tool("create_item", {"name": "Drill", "location_id": "loc1"})
    assert body(route) == {"name": "Drill", "entityTypeId": "type-item", "parentId": "loc1"}


async def test_create_location(mcp_client, hb_mock):
    route = hb_mock.post("/entities").mock(return_value=Response(201, json=entity()))
    await mcp_client.call_tool("create_location", {"name": "Shed"})
    assert body(route) == {"name": "Shed", "entityTypeId": "type-loc"}


async def test_update_entity_preserves_unspecified_fields(mcp_client, hb_mock):
    hb_mock.get("/entities/e1").mock(return_value=Response(200, json=entity()))
    put = hb_mock.put("/entities/e1").mock(return_value=Response(200, json=entity()))
    await mcp_client.call_tool(
        "update_entity",
        {"entity_id": "e1", "quantity": 3, "custom_fields": {"Colour": "teal", "Voltage": None}},
    )
    sent = body(put)
    assert sent["quantity"] == 3
    assert sent["name"] == "Drill" and sent["manufacturer"] == "Makita"
    assert sent["tagIds"] == ["tag1"]
    assert sent["parentId"] == "loc1" and sent["locationId"] == "loc1"
    assert sent["entityTypeId"] == "type-item"
    assert sent["fields"] == [{"name": "Colour", "type": "text", "textValue": "teal"}]


async def test_update_entity_move_and_unset_parent(mcp_client, hb_mock):
    hb_mock.get("/entities/e1").mock(return_value=Response(200, json=entity()))
    put = hb_mock.put("/entities/e1").mock(return_value=Response(200, json=entity()))
    await mcp_client.call_tool("update_entity", {"entity_id": "e1", "parent_id": "loc2"})
    assert body(put)["parentId"] == "loc2"
    assert body(put)["locationId"] is None
    await mcp_client.call_tool("update_entity", {"entity_id": "e1", "parent_id": ""})
    assert body(put)["parentId"] is None


def test_merge_custom_fields_types():
    existing = [{"id": "n", "name": "Watts", "type": "number", "numberValue": 1}]
    merged = merge_custom_fields(existing, {"Watts": 500, "Cordless": True})
    assert merged[0]["numberValue"] == 500
    assert merged[1] == {"name": "Cordless", "type": "boolean", "booleanValue": True}


async def test_homebox_errors_surface_as_tool_errors(mcp_client, hb_mock):
    hb_mock.get("/entities/missing").mock(return_value=Response(404, json={"error": "not found"}))
    with pytest.raises(ToolError, match="HomeBox error 404: not found"):
        await mcp_client.call_tool("get_entity", {"entity_id": "missing"})


async def test_delete_entity(mcp_client, hb_mock):
    route = hb_mock.delete("/entities/e1").mock(return_value=Response(204))
    res = await mcp_client.call_tool("delete_entity", {"entity_id": "e1"})
    assert route.called and res.data == {"deleted": "e1"}


async def test_update_tag_merges(mcp_client, hb_mock):
    hb_mock.get("/tags/t1").mock(
        return_value=Response(200, json={"id": "t1", "name": "Old", "color": "#fff"})
    )
    put = hb_mock.put("/tags/t1").mock(
        return_value=Response(200, json={"id": "t1", "name": "New", "color": "#fff"})
    )
    await mcp_client.call_tool("update_tag", {"tag_id": "t1", "name": "New"})
    assert body(put)["name"] == "New" and body(put)["color"] == "#fff"


async def test_maintenance_create_and_update(mcp_client, hb_mock):
    post = hb_mock.post("/entities/e1/maintenance").mock(
        return_value=Response(201, json={"id": "m1", "name": "Oil", "cost": "12.5"})
    )
    await mcp_client.call_tool(
        "create_maintenance", {"entity_id": "e1", "name": "Oil", "cost": 12.5}
    )
    assert body(post) == {"name": "Oil", "cost": "12.5"}

    hb_mock.get("/maintenance").mock(
        return_value=Response(
            200, json=[{"id": "m1", "name": "Oil", "description": "d", "cost": "12.5"}]
        )
    )
    put = hb_mock.put("/maintenance/m1").mock(
        return_value=Response(200, json={"id": "m1", "name": "Oil"})
    )
    await mcp_client.call_tool(
        "update_maintenance", {"maintenance_id": "m1", "completed_date": "2026-11-01"}
    )
    assert body(put)["description"] == "d"
    assert body(put)["completedDate"] == "2026-11-01"


async def test_upload_attachment(mcp_client, hb_mock):
    route = hb_mock.post("/entities/e1/attachments").mock(return_value=Response(201, json=entity()))
    content = base64.b64encode(b"hello").decode()
    await mcp_client.call_tool(
        "upload_attachment",
        {
            "entity_id": "e1",
            "filename": "a.txt",
            "content_base64": content,
            "attachment_type": "manual",
        },
    )
    raw = route.calls[0].request.content
    assert b'name="file"; filename="a.txt"' in raw and b"hello" in raw
    assert b'name="type"\r\n\r\nmanual' in raw


async def test_upload_attachment_size_limit(server_factory, hb_mock):
    async with Client(server_factory(mcp_max_attachment_mb=0.000001)) as c:
        with pytest.raises(ToolError, match="limit"):
            await c.call_tool(
                "upload_attachment",
                {"entity_id": "e1", "filename": "a.bin",
                 "content_base64": base64.b64encode(b"x" * 100).decode()},
            )  # fmt: skip


async def test_get_attachment_image(mcp_client, hb_mock):
    hb_mock.get("/entities/e1/attachments/att1").mock(
        return_value=Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
    )
    res = await mcp_client.call_tool("get_attachment", {"entity_id": "e1", "attachment_id": "att1"})
    assert res.content[0].type == "image"
    assert res.content[0].mime_type == "image/png"


async def test_add_link_attachment(mcp_client, hb_mock):
    route = hb_mock.post("/entities/e1/attachments/external").mock(
        return_value=Response(201, json=entity())
    )
    await mcp_client.call_tool(
        "add_link_attachment", {"entity_id": "e1", "url": "https://example.com/manual.pdf"}
    )
    assert body(route)["source_type"] == "link"


async def test_statistics(mcp_client, hb_mock):
    hb_mock.get("/groups/statistics").mock(return_value=Response(200, json={"totalItems": 3}))
    hb_mock.get("/groups/statistics/locations").mock(return_value=Response(200, json=[]))
    hb_mock.get("/groups/statistics/tags").mock(return_value=Response(200, json=[]))
    res = await mcp_client.call_tool("get_statistics", {})
    assert res.data["overview"]["totalItems"] == 3


async def test_export_truncates(mcp_client, hb_mock):
    hb_mock.get("/entities/export").mock(return_value=Response(200, text="a,b\n" * 1000))
    res = (await mcp_client.call_tool("export_inventory_csv", {"max_chars": 1000})).data
    assert res["truncated"] is True and len(res["csv"]) == 1000 and res["rows"] == 1000


async def test_tool_search_collapses_catalog(server_factory, hb_mock):
    hb_mock.get("/tags").mock(return_value=Response(200, json=[{"id": "t1", "name": "Tools"}]))
    async with Client(server_factory(mcp_tool_search="bm25")) as c:
        names = {t.name for t in await c.list_tools()}
        assert names == {"search_entities", "get_entity", "search_tools", "call_tool"}

        found = await c.call_tool("search_tools", {"query": "maintenance log entry"})
        assert "create_maintenance" in str(found.content)

        via_proxy = await c.call_tool("call_tool", {"name": "list_tags", "arguments": {}})
        assert "Tools" in str(via_proxy.content)
        direct = await c.call_tool("list_tags", {})  # hidden tools stay callable by name
        assert direct.data[0]["name"] == "Tools"


async def test_nil_uuid_treated_as_unset(mcp_client, hb_mock):
    nil = "00000000-0000-0000-0000-000000000000"
    hb_mock.get("/tags/t1").mock(
        return_value=Response(200, json={"id": "t1", "name": "A", "parentId": nil})
    )
    put = hb_mock.put("/tags/t1").mock(
        return_value=Response(200, json={"id": "t1", "name": "B", "parentId": nil})
    )
    res = await mcp_client.call_tool("update_tag", {"tag_id": "t1", "name": "B"})
    assert body(put)["parentId"] is None
    assert "parent_id" not in res.data
