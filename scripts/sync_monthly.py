"""월간 동기화 CLI — VWorld WFS 호출."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.sync.monthly import sync_monthly  # noqa: E402


async def main() -> None:
    result = await sync_monthly()
    for row in result["results"]:
        print(f"[{row['status']}] {row['layer']}: {row.get('message')} (n={row.get('feature_count')})")


if __name__ == "__main__":
    asyncio.run(main())
