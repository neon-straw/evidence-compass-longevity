import asyncio
import os
import re
import subprocess
import sys
from datetime import timedelta

import pytest
from fastapi.routing import APIRoute

from evidence_compass.accounts import create_account
from evidence_compass.api import PUBLIC_ROUTERS, create_app
from evidence_compass.api.auth import SESSION_COOKIE

from .conftest import TEST_DATABASE_URL, settings_for_tests

LOGIN, PASSWORD = "researcher", "correct horse battery"


@pytest.fixture
async def account(db):
    return await create_account(db, LOGIN, PASSWORD)


async def sign_in(client, password=PASSWORD):
    return await client.post("/api/session", json={"login": LOGIN, "password": password})


async def test_sign_in_opens_session_in_httponly_lax_cookie(client, account):
    response = await sign_in(client)

    assert response.status_code == 200
    assert response.json() == {"login": LOGIN}
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie
    assert (await client.get("/api/session")).json() == {"login": LOGIN}


async def test_wrong_password_is_rejected_without_session(client, account):
    response = await sign_in(client, password="wrong")

    assert response.status_code == 401
    assert "set-cookie" not in response.headers
    assert (await client.get("/api/session")).status_code == 401


async def test_missing_session_gets_401(client, account):
    assert (await client.get("/api/session")).status_code == 401


async def test_foreign_session_token_gets_401(client, account):
    client.cookies.set(SESSION_COOKIE, "not-a-session-issued-by-this-server")

    assert (await client.get("/api/session")).status_code == 401


async def test_sign_out_ends_session(client, account):
    await sign_in(client)
    token = client.cookies[SESSION_COOKIE]

    assert (await client.delete("/api/session")).status_code == 204

    client.cookies.set(SESSION_COOKIE, token)
    assert (await client.get("/api/session")).status_code == 401


async def test_expired_session_gets_401(make_client, account):
    async with make_client(settings_for_tests(session_lifetime=timedelta(seconds=-1))) as client:
        response = await sign_in(client)
        token = re.search(f"{SESSION_COOKIE}=([^;]+)", response.headers["set-cookie"])[1]
        client.cookies.set(SESSION_COOKIE, token)

        assert (await client.get("/api/session")).status_code == 401


async def test_every_route_except_public_requires_sign_in(client, account):
    public = {f"/api{route.path}" for router in PUBLIC_ROUTERS for route in router.routes}
    protected = [
        route
        for route in create_app(settings_for_tests()).routes
        if isinstance(route, APIRoute) and route.path not in public
    ]
    for route in protected:
        path = re.sub(r"{[^}]+}", "1", route.path)
        for method in route.methods:
            assert (await client.request(method, path)).status_code == 401, (method, path)


@pytest.mark.parametrize("origin", [None, "https://evil.example"])
async def test_mutating_command_requires_own_origin(make_client, account, origin):
    headers = {} if origin is None else {"Origin": origin}
    async with make_client() as client:
        client.headers.pop("Origin")
        client.headers.update(headers)
        response = await sign_in(client)

    assert response.status_code == 403


async def test_cli_creates_account_that_can_sign_in(client, db):
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "evidence_compass.cli", "create-account", LOGIN, "--password-stdin"],
        input=PASSWORD,
        capture_output=True,
        text=True,
        env={**os.environ, "EC_DATABASE_URL": TEST_DATABASE_URL},
    )

    assert result.returncode == 0, result.stderr
    assert (await sign_in(client)).status_code == 200
