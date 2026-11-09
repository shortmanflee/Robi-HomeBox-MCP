"""Async HTTP client for the HomeBox REST API (sysadminsmedia/homebox v0.27+)."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx
from fastmcp.exceptions import ToolError

log = logging.getLogger(__name__)

REFRESH_MARGIN = timedelta(minutes=5)
_FRACTION_RE = re.compile(r"(\.\d{6})\d+")
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)

Expect = Literal["json", "bytes", "text", "none", "response"]


class HomeBoxError(ToolError):
    """A HomeBox API failure. Subclasses ToolError so the message reaches the MCP client."""

    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        prefix = f"HomeBox error {status}" if status else "HomeBox unreachable"
        super().__init__(f"{prefix}: {message}")


def _parse_expiry(value: str | None) -> datetime:
    """Parse Go RFC3339 timestamps (which may carry nanoseconds). Falls back to +1h."""
    if value:
        try:
            parsed = datetime.fromisoformat(_FRACTION_RE.sub(r"\1", value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed
        except ValueError:
            log.warning("Could not parse HomeBox token expiry %r", value)
    return datetime.now(UTC) + timedelta(hours=1)


def _bearer(token: str) -> str:
    token = token.strip()
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def _error_message(resp: httpx.Response) -> str:
    if resp.is_redirect:
        target = resp.headers.get("location", "?")
        return (
            f"HOMEBOX_URL redirects ({resp.status_code}) to {target} - set HOMEBOX_URL to the "
            "final address (e.g. https://...) or to HomeBox directly, bypassing the proxy"
        )
    try:
        body = resp.json()
    except ValueError:
        text = resp.text.strip()
        title = _TITLE_RE.search(text)
        if title:  # HTML error page from a reverse proxy: keep just the title
            return title.group(1).strip()
        return text[:300] or resp.reason_phrase
    if isinstance(body, dict):
        msg = str(body.get("error") or body.get("message") or resp.reason_phrase)
        fields = body.get("fields")
        if fields:
            msg += f" (fields: {fields})"
        return msg
    return str(body)[:300]


class HomeBoxClient:
    """Thin authenticated wrapper around HomeBox's /api/v1 endpoints.

    Authentication is either a long-lived API key or a username/password login whose
    session token is refreshed shortly before it expires (and re-acquired on a 401).
    """

    def __init__(
        self,
        base_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        verify_ssl: bool = True,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not api_key and not (username and password):
            raise ValueError("HomeBoxClient needs an api_key or username+password")
        self.base_url = base_url.rstrip("/")
        self.api_base = f"{self.base_url}/api/v1"
        self._username = username
        self._password = password
        self._api_key = api_key
        self._verify = verify_ssl
        self._timeout = timeout
        self._transport = transport
        self._http: httpx.AsyncClient | None = None
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    # -- lifecycle -------------------------------------------------------------
    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=self.api_base,
                verify=self._verify,
                timeout=self._timeout,
                transport=self._transport,
                headers={"Accept": "application/json", "User-Agent": "homebox-mcp"},
            )
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    # -- auth ------------------------------------------------------------------
    async def _login(self) -> None:
        log.info("Logging in to HomeBox as %s", self._username)
        try:
            resp = await self.http.post(
                "/users/login",
                json={
                    "username": self._username,
                    "password": self._password,
                    "stayLoggedIn": True,
                },
            )
        except httpx.HTTPError as exc:
            raise HomeBoxError(0, f"{self.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise HomeBoxError(resp.status_code, f"login failed: {_error_message(resp)}")
        data = resp.json()
        self._token = _bearer(data["token"])
        self._expires_at = _parse_expiry(data.get("expiresAt"))

    async def _refresh(self) -> None:
        assert self._token is not None
        try:
            resp = await self.http.get("/users/refresh", headers={"Authorization": self._token})
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict) and data.get("token"):
                    self._token = _bearer(data["token"])
                    self._expires_at = _parse_expiry(data.get("expiresAt"))
                    return
        except (httpx.HTTPError, ValueError) as exc:
            log.info("Token refresh failed (%s); logging in again", exc)
        await self._login()

    async def _auth_header(self, *, force_login: bool = False) -> str:
        if self._api_key:
            return _bearer(self._api_key)
        async with self._lock:
            if force_login or self._token is None:
                await self._login()
            elif self._expires_at and self._expires_at - datetime.now(UTC) < REFRESH_MARGIN:
                await self._refresh()
            assert self._token is not None
            return self._token

    # -- requests --------------------------------------------------------------
    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        expect: Expect = "json",
    ) -> Any:
        if params:
            params = {k: v for k, v in params.items() if v is not None and v != []}
        resp = await self._send(method, path, params=params, json=json, files=files, data=data)
        if resp.status_code == 401 and not self._api_key:
            resp = await self._send(
                method, path, params=params, json=json, files=files, data=data, force_login=True
            )
        if resp.status_code >= 300:  # redirects aren't followed (would turn POST into GET)
            raise HomeBoxError(resp.status_code, _error_message(resp))
        if expect == "response":
            return resp
        if expect == "none" or resp.status_code == 204 or not resp.content:
            return None
        if expect == "bytes":
            return resp.content
        if expect == "text":
            return resp.text
        return resp.json()

    async def _send(self, method: str, path: str, *, force_login: bool = False, **kw: Any):
        headers = {"Authorization": await self._auth_header(force_login=force_login)}
        try:
            return await self.http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise HomeBoxError(0, f"{self.base_url}: {exc}") from exc

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def put(self, path: str, **kw: Any) -> Any:
        return await self.request("PUT", path, **kw)

    async def patch(self, path: str, **kw: Any) -> Any:
        return await self.request("PATCH", path, **kw)

    async def delete(self, path: str, **kw: Any) -> Any:
        return await self.request("DELETE", path, expect="none", **kw)

    # -- unauthenticated -------------------------------------------------------
    async def status(self) -> dict[str, Any]:
        """GET /v1/status (no auth required)."""
        try:
            resp = await self.http.get("/status")
        except httpx.HTTPError as exc:
            raise HomeBoxError(0, f"{self.base_url}: {exc}") from exc
        if resp.status_code != 200:
            raise HomeBoxError(resp.status_code, _error_message(resp))
        return resp.json()

    # -- shared lookups ----------------------------------------------------------
    async def entity_types(self) -> list[dict[str, Any]]:
        return await self.get("/entity-types") or []

    async def default_entity_type(self, *, location: bool) -> dict[str, Any]:
        """Pick the entity type to use for a generic 'item' or 'location'."""
        types = [t for t in await self.entity_types() if bool(t.get("isLocation")) == location]
        if not types:
            kind = "location" if location else "item"
            raise HomeBoxError(404, f"no {kind} entity type exists; create one first")
        preferred = "location" if location else "item"
        for t in types:
            if str(t.get("name", "")).strip().lower() == preferred:
                return t
        return types[0]
