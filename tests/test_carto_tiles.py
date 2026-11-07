from __future__ import annotations

from app.core.config import carto_voyager_url


def test_carto_voyager_url_appends_key() -> None:
    url = carto_voyager_url(15, 27952, 12721, "test-key")
    assert url.startswith(
        "https://basemaps.cartocdn.com/rastertiles/voyager/15/27952/12721.png"
    )
    assert url.endswith("?key=test-key")
    assert "YOUR_KEY" not in url
