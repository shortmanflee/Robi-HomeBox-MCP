from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import ValidationError

from homebox_mcp.auth import StaticBearerVerifier
from homebox_mcp.config import load_settings

from .conftest import TOKEN, make_settings

INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


async def test_verifier_accepts_any_configured_token():
    v = StaticBearerVerifier(["a" * 20, "b" * 20])
    assert (await v.verify_token("b" * 20)).client_id == "lan-client-1"
    assert await v.verify_token("c" * 20) is None
    assert await v.verify_token("") is None


@asynccontextmanager
async def lan_http(server_factory):
    """HTTP client hitting the ASGI app as if from a LAN address (Host: 192.168.1.50)."""
    app = server_factory().http_app(path="/mcp")
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://192.168.1.50") as c:
            yield c


async def test_http_rejects_missing_or_wrong_token(server_factory):
    async with lan_http(server_factory) as http:
        r1 = await http.post("/mcp", json=INIT, headers=MCP_HEADERS)
        r2 = await http.post(
            "/mcp",
            json=INIT,
            headers=MCP_HEADERS | {"Authorization": "Bearer wrong-token-xxxxxxxx"},
        )
    assert r1.status_code == 401
    assert r2.status_code == 401


async def test_http_accepts_valid_token_from_lan_host(server_factory):
    async with lan_http(server_factory) as http:
        r = await http.post(
            "/mcp", json=INIT, headers=MCP_HEADERS | {"Authorization": f"Bearer {TOKEN}"}
        )
    assert r.status_code == 200
    assert "HomeBox" in r.text


async def test_health_is_public(server_factory):
    async with lan_http(server_factory) as http:
        r = await http.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "homebox": "ok"}


def test_http_requires_tokens():
    with pytest.raises(ValidationError, match="MCP_AUTH_TOKENS is required"):
        make_settings(mcp_auth_tokens=None)


def test_short_tokens_rejected():
    with pytest.raises(ValidationError, match="at least 16"):
        make_settings(mcp_auth_tokens="short")


def test_stdio_needs_no_tokens():
    assert make_settings(mcp_transport="stdio", mcp_auth_tokens=None).auth_tokens == []


def test_requires_homebox_credentials():
    with pytest.raises(ValidationError, match="HOMEBOX_API_KEY"):
        make_settings(homebox_username=None, homebox_password=None)


def test_url_normalised():
    assert make_settings(homebox_url="http://hb:7745/api/").homebox_url == "http://hb:7745"


def test_secret_files(tmp_path, monkeypatch):
    secret = tmp_path / "tokens"
    secret.write_text("x" * 20 + ",y" + "y" * 19 + "\n")
    for k, v in {
        "HOMEBOX_URL": "http://hb",
        "HOMEBOX_API_KEY": "key",
        "MCP_AUTH_TOKENS_FILE": str(secret),
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MCP_AUTH_TOKENS", raising=False)
    monkeypatch.chdir(tmp_path)  # avoid picking up a developer .env
    s = load_settings()
    assert s.auth_tokens == ["x" * 20, "y" * 20]
