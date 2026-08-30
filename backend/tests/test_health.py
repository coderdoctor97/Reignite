"""
Tests for the /api/health endpoint and basic backend setup.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import create_app
from app.storage.database import close_database


@pytest.fixture
def app():
    """Create a fresh app instance for each test."""
    return create_app()


@pytest.fixture
async def client(app):
    """Async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_database()


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    """GET /api/health should return 200 with status 'ok'."""
    response = await client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "app" in data


@pytest.mark.asyncio
async def test_health_response_shape(client):
    """Health response should have the correct shape."""
    response = await client.get("/api/health")
    data = response.json()
    assert isinstance(data["status"], str)
    assert isinstance(data["version"], str)
    assert isinstance(data["app"], str)
    assert data["app"] == "Gateway Control Center"


@pytest.mark.asyncio
async def test_health_content_type(client):
    """Health endpoint should return JSON."""
    response = await client.get("/api/health")
    assert response.headers["content-type"] == "application/json"
