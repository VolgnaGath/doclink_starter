from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import __version__


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": True, "version": __version__}


def test_health_degraded(client: TestClient, app: FastAPI) -> None:
    app.state.check_database = lambda: False

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "degraded", "db": False, "version": __version__}
