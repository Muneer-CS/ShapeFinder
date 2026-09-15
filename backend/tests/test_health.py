from fastapi.testclient import TestClient

from shape_finder.main import app

client = TestClient(app)


def test_health_endpoint() -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "shape-finder-api",
        "version": "0.2.0-rc.1",
    }


def test_openapi_exposes_health_endpoint() -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 200
    assert "/api/v1/health" in response.json()["paths"]
    assert "/api/v1/readiness" in response.json()["paths"]
    assert response.json()["info"]["version"] == "0.2.0-rc.1"
