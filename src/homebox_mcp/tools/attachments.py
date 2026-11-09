"""Attachment tools: photos, manuals, receipts, warranties and external links."""

from __future__ import annotations

import base64
import binascii
import mimetypes
import posixpath
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import unquote, urljoin, urlparse

from fastmcp.server.auth.ssrf import SSRFError, SSRFFetchError, ssrf_safe_fetch_response
from fastmcp.utilities.types import Image
from pydantic import Field

from homebox_mcp.client import HomeBoxClient, HomeBoxError
from homebox_mcp.tools._common import DELETE, READ, UPDATE, WRITE, summarize_attachment

if TYPE_CHECKING:
    from fastmcp import FastMCP

    from homebox_mcp.config import Settings

AttachmentType = Literal["photo", "manual", "receipt", "warranty", "attachment"]
_TEXT_MIME_PREFIXES = ("text/", "application/json", "application/xml")


def _find_attachment(entity: dict[str, Any], attachment_id: str) -> dict[str, Any]:
    for a in entity.get("attachments") or []:
        if a.get("id") == attachment_id:
            return a
    raise HomeBoxError(404, f"attachment {attachment_id} not found on entity {entity.get('id')}")


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


_REDIRECTS = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 3


async def fetch_url(url: str, max_bytes: int) -> tuple[bytes, str, str]:
    """Download a public HTTPS URL with SSRF protection.

    Private/loopback/link-local targets are refused, DNS is pinned against rebinding,
    the size limit is enforced while streaming, and each redirect hop is re-validated.
    Returns (content, mime_type, final_url).
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            resp = await ssrf_safe_fetch_response(
                current,
                max_size=max_bytes,
                timeout=15.0,
                overall_timeout=60.0,
                request_headers={"User-Agent": "homebox-mcp/0.1 (attachment fetch)"},
                allowed_status_codes={200} | _REDIRECTS,
            )
        except SSRFError as exc:
            raise HomeBoxError(400, f"URL not allowed: {exc}") from exc
        except SSRFFetchError as exc:
            msg = str(exc)
            status = 413 if "size" in msg.lower() else 502
            if status == 413:
                msg = f"file exceeds the {_mb(max_bytes)} limit (MCP_MAX_ATTACHMENT_MB)"
            raise HomeBoxError(status, f"download failed: {msg}") from exc
        if resp.status_code in _REDIRECTS:
            location = {k.lower(): v for k, v in resp.headers.items()}.get("location")
            if not location:
                raise HomeBoxError(502, f"redirect without Location from {current}")
            current = urljoin(current, location)
            continue
        headers = {k.lower(): v for k, v in resp.headers.items()}
        mime = (headers.get("content-type") or "application/octet-stream").split(";")[0].strip()
        return resp.content, mime, current
    raise HomeBoxError(502, f"too many redirects (> {MAX_REDIRECTS})")


def filename_for(url: str, mime: str) -> str:
    name = posixpath.basename(unquote(urlparse(url).path)) or "download"
    if not posixpath.splitext(name)[1]:
        name += mimetypes.guess_extension(mime) or ""
    return name[:200]


def register(mcp: FastMCP, hb: HomeBoxClient, settings: Settings) -> None:
    max_bytes = settings.max_attachment_bytes

    @mcp.tool(annotations=READ)
    async def list_attachments(entity_id: str) -> list[dict[str, Any]]:
        """List the attachments (metadata only) on an item or location."""
        entity = await hb.get(f"/entities/{entity_id}")
        return [summarize_attachment(a) for a in entity.get("attachments") or []]

    @mcp.tool(annotations=READ, output_schema=None)
    async def get_attachment(entity_id: str, attachment_id: str) -> Any:
        """Download an attachment. Images are returned as viewable images, text files as
        text, and other files as base64 (subject to the server's size limit)."""
        resp = await hb.get(f"/entities/{entity_id}/attachments/{attachment_id}", expect="response")
        declared = int(resp.headers.get("content-length") or 0)
        content: bytes = resp.content
        size = max(declared, len(content))
        if size > max_bytes:
            raise HomeBoxError(
                413, f"attachment is {_mb(size)}; limit is {_mb(max_bytes)} (MCP_MAX_ATTACHMENT_MB)"
            )
        mime = (resp.headers.get("content-type") or "application/octet-stream").split(";")[0]
        if mime.startswith("image/"):
            return Image(data=content, format=mime.removeprefix("image/"))
        if mime.startswith(_TEXT_MIME_PREFIXES):
            return content.decode("utf-8", errors="replace")
        return {
            "mime_type": mime,
            "size_bytes": len(content),
            "content_base64": base64.b64encode(content).decode(),
        }

    if settings.mcp_read_only:
        return

    @mcp.tool(annotations=WRITE)
    async def upload_attachment(
        entity_id: str,
        filename: Annotated[str, Field(description="File name including extension")],
        content_base64: Annotated[str, Field(description="File contents, base64-encoded")],
        attachment_type: AttachmentType = "attachment",
        primary: Annotated[
            bool, Field(description="Make this the primary photo (photos only)")
        ] = False,
    ) -> list[dict[str, Any]]:
        """Upload a file (photo, manual, receipt, warranty...) to an item or location.
        Returns the entity's updated attachment list."""
        try:
            payload = base64.b64decode(content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HomeBoxError(400, f"content_base64 is not valid base64: {exc}") from exc
        if len(payload) > max_bytes:
            raise HomeBoxError(413, f"file is {_mb(len(payload))}; limit is {_mb(max_bytes)}")
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return await _upload(entity_id, filename, payload, mime, attachment_type, primary)

    async def _upload(
        entity_id: str,
        filename: str,
        payload: bytes,
        mime: str,
        attachment_type: str,
        primary: bool,
    ) -> list[dict[str, Any]]:
        entity = await hb.post(
            f"/entities/{entity_id}/attachments",
            files={"file": (filename, payload, mime)},
            data={"name": filename, "type": attachment_type, "primary": str(primary).lower()},
        )
        return [summarize_attachment(a) for a in entity.get("attachments") or []]

    if settings.mcp_allow_url_fetch:

        @mcp.tool(annotations=WRITE.model_copy(update={"open_world_hint": True}))
        async def upload_attachment_from_url(
            entity_id: str,
            url: Annotated[str, Field(description="Public https:// URL of the file to download")],
            attachment_type: AttachmentType = "photo",
            primary: Annotated[
                bool, Field(description="Make this the primary photo (photos only)")
            ] = False,
            filename: Annotated[
                str | None, Field(description="Override the file name (default: from the URL)")
            ] = None,
        ) -> list[dict[str, Any]]:
            """Download a file (e.g. a product photo) from a public HTTPS URL and store a copy
            in HomeBox as an attachment. Use add_link_attachment instead to only save a link.
            Returns the entity's updated attachment list."""
            payload, mime, final_url = await fetch_url(url, max_bytes)
            if attachment_type == "photo" and not mime.startswith("image/"):
                raise HomeBoxError(
                    415, f"URL returned {mime}, not an image; check the URL points at the file"
                )
            if mime == "text/html":
                raise HomeBoxError(415, "URL returned an HTML page, not a file")
            name = filename or filename_for(final_url, mime)
            return await _upload(entity_id, name, payload, mime, attachment_type, primary)

    @mcp.tool(annotations=WRITE)
    async def add_link_attachment(
        entity_id: str,
        url: Annotated[str, Field(description="http(s) URL to link (not downloaded)")],
        title: str | None = None,
        attachment_type: AttachmentType = "attachment",
    ) -> list[dict[str, Any]]:
        """Attach an external link (e.g. product page or online manual) to an entity
        without copying the file into HomeBox."""
        body = {
            "source_type": "link",
            "external_id": url,
            "title": title,
            "attachment_type": attachment_type,
        }
        entity = await hb.post(
            f"/entities/{entity_id}/attachments/external",
            json={k: v for k, v in body.items() if v is not None},
        )
        return [summarize_attachment(a) for a in entity.get("attachments") or []]

    @mcp.tool(annotations=UPDATE)
    async def update_attachment(
        entity_id: str,
        attachment_id: str,
        title: str | None = None,
        attachment_type: AttachmentType | None = None,
        primary: bool | None = None,
    ) -> dict[str, Any]:
        """Rename an attachment, change its type, or set it as the primary photo."""
        cur = _find_attachment(await hb.get(f"/entities/{entity_id}"), attachment_id)
        body = {
            "title": title if title is not None else cur.get("title"),
            "type": attachment_type or cur.get("type"),
            "primary": primary if primary is not None else bool(cur.get("primary")),
        }
        entity = await hb.put(f"/entities/{entity_id}/attachments/{attachment_id}", json=body)
        return summarize_attachment(_find_attachment(entity, attachment_id))

    @mcp.tool(annotations=DELETE)
    async def delete_attachment(entity_id: str, attachment_id: str) -> dict[str, Any]:
        """Delete an attachment from an entity."""
        await hb.delete(f"/entities/{entity_id}/attachments/{attachment_id}")
        return {"deleted": attachment_id}
