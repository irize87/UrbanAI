"""fixture 적재 CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.db.fixtures import load_fixtures  # noqa: E402


if __name__ == "__main__":
    print(load_fixtures())
