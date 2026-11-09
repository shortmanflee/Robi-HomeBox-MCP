"""Tool registration. Each module exposes `register(mcp, hb, settings)`."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homebox_mcp.tools import attachments, entities, entity_types, maintenance, reporting, tags

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.client import HomeBoxClient
    from homebox_mcp.config import Settings

MODULES = (entities, entity_types, tags, maintenance, attachments, reporting)


def register_all(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    for module in MODULES:
        module.register(mcp, hb, settings)
