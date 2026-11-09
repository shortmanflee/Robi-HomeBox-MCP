"""FastMCP application factory."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.transforms.search import BM25SearchTransform, RegexSearchTransform
from starlette.requests import Request
from starlette.responses import JSONResponse

from homebox_mcp import __version__
from homebox_mcp.auth import StaticBearerVerifier
from homebox_mcp.client import HomeBoxClient, HomeBoxError
from homebox_mcp.config import Settings
from homebox_mcp.tools import register_all

log = logging.getLogger(__name__)

INSTRUCTIONS = """\
Tools for a HomeBox home-inventory server.

In HomeBox, items and locations are both "entities"; an entity whose type has
is_location=true is a location (room, shelf, box) that can contain other entities.
Labels are called "tags".

Typical flow: search_entities / get_location_tree to find ids, then get_entity for
details. Use create_location / create_item to add things, update_entity to change or
move them (set parent_id), and the tag / maintenance / attachment tools for the rest.
Deleting is permanent - confirm with the user first.
"""

HEALTH_CACHE_SECONDS = 30

# Tools that stay listed when server-side tool search is enabled: the entry points
# for almost every conversation. Everything else is found via search_tools.
ALWAYS_VISIBLE = ("search_entities", "get_entity")


def build_client(settings: Settings) -> HomeBoxClient:
    return HomeBoxClient(
        settings.homebox_url,
        username=settings.homebox_username,
        password=settings.homebox_password.get_secret_value()
        if settings.homebox_password
        else None,
        api_key=settings.homebox_api_key.get_secret_value() if settings.homebox_api_key else None,
        verify_ssl=settings.homebox_verify_ssl,
        timeout=settings.homebox_timeout,
    )


def create_server(settings: Settings, hb: HomeBoxClient | None = None) -> FastMCP:
    hb = hb or build_client(settings)

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict[str, Any]]:
        try:
            yield {"homebox": hb}
        finally:
            await hb.aclose()

    auth = StaticBearerVerifier(settings.auth_tokens) if settings.mcp_transport == "http" else None
    mcp = FastMCP(
        name="HomeBox",
        instructions=INSTRUCTIONS,
        version=__version__,
        auth=auth,
        lifespan=lifespan,
        mask_error_details=True,
    )
    register_all(mcp, hb, settings)
    if settings.mcp_tool_search != "off":
        transform_cls = (
            BM25SearchTransform if settings.mcp_tool_search == "bm25" else RegexSearchTransform
        )
        mcp.add_transform(
            transform_cls(
                max_results=settings.mcp_tool_search_max_results,
                always_visible=list(ALWAYS_VISIBLE),
            )
        )

    @mcp.resource("homebox://entity-types", mime_type="application/json")
    async def entity_types_resource() -> list[dict[str, Any]]:
        """Entity types (ids, names, and whether they are locations)."""
        return [
            {"id": t.get("id"), "name": t.get("name"), "is_location": bool(t.get("isLocation"))}
            for t in await hb.entity_types()
        ]

    @mcp.resource("homebox://tags", mime_type="application/json")
    async def tags_resource() -> list[dict[str, Any]]:
        """All tags (ids and names)."""
        return [{"id": t.get("id"), "name": t.get("name")} for t in await hb.get("/tags") or []]

    health_cache: dict[str, Any] = {"at": 0.0, "ok": False, "detail": None}

    @mcp.custom_route("/health", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> JSONResponse:
        """Unauthenticated liveness probe. Reports HomeBox reachability (cached)."""
        now = time.monotonic()
        if now - health_cache["at"] > HEALTH_CACHE_SECONDS:
            try:
                status = await hb.status()
                health_cache.update(ok=bool(status.get("health", True)), detail=None)
            except HomeBoxError as exc:
                health_cache.update(ok=False, detail=exc.message)
            health_cache["at"] = now
        body = {"status": "ok", "homebox": "ok" if health_cache["ok"] else "unreachable"}
        if health_cache["detail"]:
            body["detail"] = health_cache["detail"]
        # MCP itself is alive either way; HomeBox being down shouldn't restart this container.
        return JSONResponse(body)

    return mcp
