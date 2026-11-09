from __future__ import annotations

import httpx
import pytest
from httpx import Response

from homebox_mcp.client import HomeBoxClient, HomeBoxError, _parse_expiry

from .conftest import HB_URL, login_response


def client(**kw) -> HomeBoxClient:
    return HomeBoxClient(HB_URL, **({"username": "svc", "password": "pw"} | kw))


async def test_logs_in_once_and_reuses_token(hb_mock):
    route = hb_mock.get("/tags").mock(return_value=Response(200, json=[]))
    hb = client()
    await hb.get("/tags")
    await hb.get("/tags")
    assert hb_mock["login"].call_count == 1
    assert route.calls[-1].request.headers["Authorization"] == "Bearer session-1"
    login_body = hb_mock["login"].calls[0].request.content
    assert b'"stayLoggedIn":true' in login_body.replace(b" ", b"")


async def test_refreshes_when_close_to_expiry(hb_mock):
    hb_mock["login"].mock(return_value=login_response("2000-01-01T00:00:00Z"))
    refresh = hb_mock.get("/users/refresh").mock(
        return_value=Response(200, json={"token": "fresh", "expiresAt": "2099-01-01T00:00:00Z"})
    )
    tags = hb_mock.get("/tags").mock(return_value=Response(200, json=[]))
    hb = client()
    await hb.get("/tags")  # login (already-expired token)
    await hb.get("/tags")  # triggers refresh
    assert refresh.call_count == 1
    assert tags.calls[-1].request.headers["Authorization"] == "Bearer fresh"


async def test_refresh_failure_falls_back_to_login(hb_mock):
    hb_mock["login"].mock(return_value=login_response("2000-01-01T00:00:00Z"))
    hb_mock.get("/users/refresh").mock(return_value=Response(500))
    hb_mock.get("/tags").mock(return_value=Response(200, json=[]))
    hb = client()
    await hb.get("/tags")
    await hb.get("/tags")
    assert hb_mock["login"].call_count == 2


async def test_retries_once_after_401(hb_mock):
    tags = hb_mock.get("/tags").mock(
        side_effect=[Response(401, json={"error": "expired"}), Response(200, json=[{"id": 1}])]
    )
    hb = client()
    assert await hb.get("/tags") == [{"id": 1}]
    assert tags.call_count == 2
    assert hb_mock["login"].call_count == 2


async def test_api_key_skips_login(hb_mock):
    tags = hb_mock.get("/tags").mock(return_value=Response(200, json=[]))
    hb = HomeBoxClient(HB_URL, api_key="hbk_secret")
    await hb.get("/tags")
    assert hb_mock["login"].call_count == 0
    assert tags.calls[0].request.headers["Authorization"] == "Bearer hbk_secret"


async def test_error_message_and_fields(hb_mock):
    hb_mock.post("/tags").mock(
        return_value=Response(422, json={"error": "validation failed", "fields": {"name": "req"}})
    )
    with pytest.raises(HomeBoxError) as exc:
        await client().post("/tags", json={})
    assert exc.value.status == 422
    assert "validation failed" in str(exc.value)
    assert "name" in str(exc.value)


async def test_bad_login_raises(hb_mock):
    hb_mock["login"].mock(return_value=Response(401, json={"error": "invalid credentials"}))
    with pytest.raises(HomeBoxError, match="login failed: invalid credentials"):
        await client().get("/tags")


async def test_connection_error_wrapped(hb_mock):
    hb_mock.get("/tags").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(HomeBoxError, match="unreachable"):
        await client().get("/tags")


async def test_list_params_drop_none_and_repeat_arrays(hb_mock):
    route = hb_mock.get("/entities").mock(return_value=Response(200, json={"items": []}))
    await client().get("/entities", params={"q": None, "tags": ["a", "b"], "parentIds": []})
    assert str(route.calls[0].request.url.query, "ascii") == "tags=a&tags=b"


def test_parse_expiry_handles_nanoseconds():
    dt = _parse_expiry("2026-11-10T19:57:00.123456789Z")
    assert (dt.year, dt.microsecond, dt.utcoffset().total_seconds()) == (2026, 123456, 0)


async def test_default_entity_type(hb_mock):
    hb = client()
    assert (await hb.default_entity_type(location=True))["id"] == "type-loc"
    assert (await hb.default_entity_type(location=False))["id"] == "type-item"


async def test_redirecting_proxy_gives_actionable_error(hb_mock):
    html = "<html><head><title>301 Moved Permanently</title></head><body>openresty</body></html>"
    hb_mock["login"].mock(
        return_value=Response(
            301, text=html, headers={"Location": "https://hb.example/api/v1/users/login"}
        )
    )
    hb_mock["status"].mock(
        return_value=Response(
            301, text=html, headers={"Location": "https://hb.example/api/v1/status"}
        )
    )
    hb = client()
    with pytest.raises(HomeBoxError, match=r"HOMEBOX_URL redirects \(301\) to https://hb.example"):
        await hb.get("/tags")
    with pytest.raises(HomeBoxError, match="HOMEBOX_URL redirects"):
        await hb.status()


async def test_html_error_page_reduced_to_title(hb_mock):
    hb_mock.get("/tags").mock(
        return_value=Response(502, text="<html><head><title>502 Bad Gateway</title></head></html>")
    )
    with pytest.raises(HomeBoxError, match=r"error 502: 502 Bad Gateway$"):
        await client().get("/tags")
