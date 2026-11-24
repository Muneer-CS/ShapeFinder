from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from shape_finder.application.massive_schedule import refresh_if_due
from shape_finder.config import Settings
from shape_finder.main import create_app

OWNER = "test-owner"
PASSWORD = "offline-test-value-" * 2
ORIGIN = "https://preview.example.test"
SCAN = "/api/v1/pattern-scans"
MARKET_DATA = "/api/v1/market-data/AAPL"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def hosted_settings(root: Path, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        **(
            {
                "app_env": "production",
                "shapefinder_owner_username": SecretStr(OWNER),
                "shapefinder_owner_password": SecretStr(PASSWORD),
                "shapefinder_public_origin": ORIGIN,
                "shapefinder_data_dir": root,
                "database_path": root / "shapefinder.sqlite3",
            }
            | overrides
        ),
    )


def test_hosted_api_requires_owner_credentials_even_without_gateway(tmp_path: Path) -> None:
    with TestClient(create_app(settings=hosted_settings(tmp_path))) as client:
        for path in ("/__owner_auth", "/docs", f"{SCAN}/capabilities", MARKET_DATA):
            response = client.get(path, headers={"X-Forwarded-User": OWNER})
            assert response.status_code == 401
            assert response.headers["www-authenticate"] == 'Basic realm="ShapeFinder"'
            assert response.headers["cache-control"] == "private, no-store"

        assert client.get("/__owner_auth", auth=(OWNER, "wrong")).status_code == 401
        assert (
            client.get("/__owner_auth", headers={"Authorization": "Basic !!!!"}).status_code == 401
        )
        assert client.get("/__owner_auth", auth=(OWNER, PASSWORD)).status_code == 204
        health = client.get("/__health")
        assert (health.status_code, health.text) == (200, "ok")
        assert client.post("/__health").status_code == 401

        capability = client.get(f"{SCAN}/capabilities", auth=(OWNER, PASSWORD))
        assert capability.status_code == 200
        assert capability.headers["cache-control"] == "private, no-store"
        denied = client.post(
            SCAN, json={}, auth=(OWNER, PASSWORD), headers={"Origin": "https://another.test"}
        )
        assert denied.status_code == 403
        assert "access-control-allow-origin" not in denied.headers
        allowed = client.post(SCAN, json={}, auth=(OWNER, PASSWORD), headers={"Origin": ORIGIN})
        assert allowed.status_code == 422


def test_hosted_config_fails_closed_and_requires_runtime_volume(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="SHAPEFINDER_OWNER_USERNAME"):
        hosted_settings(tmp_path, shapefinder_owner_password=None)
    with pytest.raises(ValidationError, match="SHAPEFINDER_PUBLIC_ORIGIN"):
        hosted_settings(
            tmp_path,
            railway_project_id="test-project",
            shapefinder_public_origin="http://localhost:4181",
        )
    with pytest.raises(ValidationError, match="persistent data mount"):
        hosted_settings(tmp_path, app_env="development", railway_environment_id="test-env")
    with pytest.raises(ValidationError, match="persistent data mount"):
        hosted_settings(tmp_path, database_path=tmp_path.parent / "outside.sqlite3")
    with pytest.raises(ValidationError, match="MASSIVE_API_KEY"):
        hosted_settings(tmp_path, current_daily_provider="massive", massive_refresh_enabled=True)
    with pytest.raises(ValidationError, match="massive_backfill_sessions"):
        hosted_settings(tmp_path, massive_backfill_sessions=64)

    settings = hosted_settings(
        tmp_path,
        app_env="development",
        railway_project_id="test-project",
        railway_volume_mount_path=tmp_path,
    )
    assert settings.hosted
    assert PASSWORD not in repr(settings)


@pytest.mark.anyio
async def test_refresh_waits_for_eastern_publication_and_resumes_failures(tmp_path: Path) -> None:
    settings = hosted_settings(tmp_path)
    stamp = tmp_path / "shapefinder.massive-refresh-date"
    stamp.write_text("2026-11-25\n", encoding="ascii")
    refresh = AsyncMock(side_effect=[False, True])
    # Thanksgiving has no session. Even the early-close Friday waits until Saturday ET.
    assert await refresh_if_due(settings, datetime(2026, 11, 26, 23, tzinfo=UTC), refresh)
    assert await refresh_if_due(settings, datetime(2026, 11, 28, 1, 14, tzinfo=UTC), refresh)
    refresh.assert_not_called()
    assert await refresh_if_due(settings, datetime(2026, 11, 28, 5, 4, tzinfo=UTC), refresh)
    release = datetime(2026, 11, 28, 5, 5, tzinfo=UTC)
    assert not await refresh_if_due(settings, release, refresh)
    assert stamp.read_text(encoding="ascii").strip() == "2026-11-25"
    assert await refresh_if_due(settings, release, refresh)
    assert stamp.read_text(encoding="ascii").strip() == "2026-11-27"
    assert await refresh_if_due(settings, datetime(2026, 11, 29, 18, tzinfo=UTC), refresh)
    assert refresh.await_count == 2
