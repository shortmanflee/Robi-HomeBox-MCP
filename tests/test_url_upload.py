from __future__ import annotations

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from fastmcp.server.auth.ssrf import SSRFFetchError, SSRFFetchResponse
from httpx import Response

from homebox_mcp.tools import attachments
from homebox_mcp.tools.attachments import filename_for

from .conftest import entity

PNG = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def fake_fetch(monkeypatch):
    """Replace the network fetch with a scripted list of responses; records URLs."""
    calls: list[str] = []
    script: list[SSRFFetchResponse | Exception] = []

    async def fake(url, **kw):
        calls.append(url)
        item = script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(attachments, "ssrf_safe_fetch_response", fake)
    return calls, script


def ok(content=PNG, ctype="image/png"):
    return SSRFFetchResponse(content=content, status_code=200, headers={"Content-Type": ctype})


async def test_downloads_follows_redirect_and_uploads(mcp_client, hb_mock, fake_fetch):
    calls, script = fake_fetch
    script += [
        SSRFFetchResponse(b"", 302, {"Location": "/cdn/drill-photo"}),
        ok(),
    ]
    up = hb_mock.post("/entities/e1/attachments").mock(return_value=Response(201, json=entity()))
    await mcp_client.call_tool(
        "upload_attachment_from_url",
        {"entity_id": "e1", "url": "https://shop.example/p/1", "primary": True},
    )
    assert calls == ["https://shop.example/p/1", "https://shop.example/cdn/drill-photo"]
    raw = up.calls[0].request.content
    assert b'filename="drill-photo.png"' in raw and PNG in raw
    assert b'name="type"\r\n\r\nphoto' in raw and b'name="primary"\r\n\r\ntrue' in raw


async def test_rejects_html_page_for_photo(mcp_client, hb_mock, fake_fetch):
    fake_fetch[1].append(ok(b"<html>", "text/html; charset=utf-8"))
    with pytest.raises(ToolError, match="not an image"):
        await mcp_client.call_tool(
            "upload_attachment_from_url", {"entity_id": "e1", "url": "https://x.example/a"}
        )


async def test_too_many_redirects(mcp_client, fake_fetch):
    fake_fetch[1].extend(SSRFFetchResponse(b"", 301, {"Location": "/again"}) for _ in range(4))
    with pytest.raises(ToolError, match="too many redirects"):
        await mcp_client.call_tool(
            "upload_attachment_from_url", {"entity_id": "e1", "url": "https://x.example/a"}
        )


async def test_size_limit_reported(mcp_client, fake_fetch):
    fake_fetch[1].append(SSRFFetchError("Response too large: exceeded size limit"))
    with pytest.raises(ToolError, match=r"exceeds the .* limit"):
        await mcp_client.call_tool(
            "upload_attachment_from_url", {"entity_id": "e1", "url": "https://x.example/a"}
        )


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://example.com/a.png", "HTTPS"),
        ("https://127.0.0.1/a.png", ""),
        ("https://192.168.1.178:7745/a.png", ""),
        ("https://169.254.169.254/latest/meta-data", ""),
    ],
)
async def test_ssrf_guard_blocks_unsafe_urls(mcp_client, url, reason):
    # Real SSRF validation (no monkeypatch): these never leave the process.
    with pytest.raises(ToolError, match=f"URL not allowed.*{reason}"):
        await mcp_client.call_tool("upload_attachment_from_url", {"entity_id": "e1", "url": url})


async def test_can_be_disabled(server_factory):
    async with Client(server_factory(mcp_allow_url_fetch=False)) as c:
        names = {t.name for t in await c.list_tools()}
    assert "upload_attachment_from_url" not in names and "upload_attachment" in names


def test_filename_for():
    assert filename_for("https://x/img/My%20Drill.jpg?w=200", "image/jpeg") == "My Drill.jpg"
    assert filename_for("https://x/", "image/png") == "download.png"
