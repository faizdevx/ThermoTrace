"""
Tests for /api/health.

The health endpoint now also returns data_mode so we test both fields.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from backend.app.main import app

client = TestClient(app)


def test_health_endpoint_returns_ok() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_health_endpoint_returns_data_mode() -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    # data_mode must be one of the two valid values
    assert data["data_mode"] in ("synthetic", "postgis")


def test_health_endpoint_data_mode_matches_env(monkeypatch: object) -> None:
    """Default DATA_MODE in test environment should be 'synthetic'."""
    response = client.get("/api/health")
    data = response.json()
    # Unless the tester has explicitly set DATA_MODE=postgis in their env,
    # the test suite runs against the synthetic dataset.
    import os
    expected = os.getenv("DATA_MODE", "synthetic")
    assert data["data_mode"] == expected
