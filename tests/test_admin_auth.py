"""관리 API는 ADMIN_TOKEN 과 X-Admin-Token 이 같을 때만 연다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_admin_routes_require_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    from app.core.config import get_settings

    get_settings.cache_clear()
    started: list[str] = []

    def _drop(coro):  # type: ignore[no-untyped-def]
        started.append("called")
        coro.close()

    monkeypatch.setattr("app.api.routes.asyncio.create_task", _drop)

    from app.main import create_app

    try:
        with TestClient(create_app()) as client:
            denied = client.post("/api/admin/sync")
            assert denied.status_code == 401
            assert started == []

            wrong = client.post(
                "/api/admin/sync",
                headers={"X-Admin-Token": "wrong"},
            )
            assert wrong.status_code == 401
            assert started == []

            fixtures = client.post("/api/admin/fixtures")
            assert fixtures.status_code == 401

            ok = client.post(
                "/api/admin/sync",
                headers={"X-Admin-Token": "test-admin-token"},
            )
            assert ok.status_code == 200
            assert started == ["called"]

            status = client.get("/api/admin/sync/status")
            assert status.status_code == 401
            status_ok = client.get(
                "/api/admin/sync/status",
                headers={"X-Admin-Token": "test-admin-token"},
            )
            assert status_ok.status_code == 200

            monkeypatch.setenv("ADMIN_TOKEN", "")
            get_settings.cache_clear()
            blocked = client.post(
                "/api/admin/sync",
                headers={"X-Admin-Token": "test-admin-token"},
            )
            assert blocked.status_code == 401
            assert started == ["called"]
    finally:
        get_settings.cache_clear()
