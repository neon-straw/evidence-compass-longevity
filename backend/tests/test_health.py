from .conftest import settings_for_tests


async def test_health_reports_ok_when_database_is_reachable(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_reports_unavailable_database(make_client):
    unreachable = settings_for_tests(database_url="postgresql+asyncpg://ec:ec@127.0.0.1:1/nowhere")
    async with make_client(unreachable) as client:
        response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unavailable"}
