from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


@pytest.fixture(scope="session", autouse=True)
def _isolate_test_db(tmp_path_factory: pytest.TempPathFactory) -> None:
    """테스트가 실데이터 SQLite를 fixture로 덮어쓰지 않게 분리한다."""
    db_path = tmp_path_factory.mktemp("db") / "sitedia_test.sqlite"
    os.environ["DATABASE_PATH"] = str(db_path)
    from app.core.config import get_settings

    get_settings.cache_clear()
    from app.db.schema import init_db

    init_db()
