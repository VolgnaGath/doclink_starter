from fastapi.testclient import TestClient

from mock1c.main import app


def test_mock1c_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_mock1c_odata_list_is_empty() -> None:
    with TestClient(app) as client:
        response = client.get("/odata/standard.odata/Catalog_Документы")

    assert response.status_code == 200
    assert response.json() == {"value": []}
