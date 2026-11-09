from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
import respx
from fastmcp import Client, FastMCP
from httpx import Response

from homebox_mcp.client import HomeBoxClient
from homebox_mcp.config import Settings
from homebox_mcp.server import create_server

HB_URL = "http://homebox.test"
API = f"{HB_URL}/api/v1"
TOKEN = "t" * 32

ITEM_TYPE = {"id": "type-item", "name": "Item", "isLocation": False}
LOC_TYPE = {"id": "type-loc", "name": "Location", "isLocation": True}


def make_settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "homebox_url": HB_URL,
        "homebox_username": "svc",
        "homebox_password": "pw",
        "mcp_transport": "http",
        "mcp_auth_tokens": TOKEN,
        "_env_file": None,
    }
    return Settings(**(base | overrides))


def login_response(expires: str = "2099-01-01T00:00:00.123456789Z") -> Response:
    return Response(
        200, json={"token": "Bearer session-1", "expiresAt": expires, "attachmentToken": "a"}
    )


@pytest.fixture
def hb_mock() -> respx.MockRouter:
    with respx.mock(base_url=API, assert_all_called=False) as router:
        router.post("/users/login", name="login").mock(return_value=login_response())
        router.get("/entity-types", name="entity_types").mock(
            return_value=Response(200, json=[ITEM_TYPE, LOC_TYPE])
        )
        router.get("/status", name="status").mock(
            return_value=Response(200, json={"health": True, "build": {"version": "v0.27.1"}})
        )
        yield router


@pytest.fixture
def server_factory(hb_mock: respx.MockRouter) -> Callable[..., FastMCP]:
    def factory(**overrides: Any) -> FastMCP:
        settings = make_settings(**overrides)
        hb = HomeBoxClient(HB_URL, username="svc", password="pw")
        return create_server(settings, hb)

    return factory


@pytest.fixture
async def mcp_client(server_factory) -> AsyncIterator[Client]:
    async with Client(server_factory()) as client:
        yield client


def entity(**kw: Any) -> dict[str, Any]:
    """A realistic EntityOut payload."""
    base = {
        "id": "e1",
        "name": "Drill",
        "description": "Cordless",
        "entityType": ITEM_TYPE,
        "parent": {"id": "loc1", "name": "Garage"},
        "locationId": "loc1",
        "quantity": 1,
        "tags": [{"id": "tag1", "name": "Tools"}],
        "fields": [{"id": "f1", "name": "Voltage", "type": "text", "textValue": "18V"}],
        "attachments": [
            {"id": "att1", "title": "photo.jpg", "type": "photo", "mimeType": "image/jpeg"}
        ],
        "manufacturer": "Makita",
        "serialNumber": "SN1",
        "purchaseDate": "0001-01-01T00:00:00Z",
        "insured": False,
        "archived": False,
    }
    return base | kw
