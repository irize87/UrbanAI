from __future__ import annotations

from io import BytesIO

import numpy as np
import tifffile

from app.core.config import Settings
from app.sync.copernicus_dem import dem_output_size, dem_tiff_to_grid


def test_has_cdse_key_requires_both() -> None:
    assert Settings(cdse_client_id="", cdse_client_secret="x").has_cdse_key is False
    assert Settings(cdse_client_id="id", cdse_client_secret="").has_cdse_key is False
    assert Settings(cdse_client_id="id", cdse_client_secret="sec").has_cdse_key is True


def test_dem_output_size_caps() -> None:
    bbox = {
        "min_lng": 126.90,
        "min_lat": 37.435,
        "max_lng": 127.12,
        "max_lat": 37.535,
    }
    w, h = dem_output_size(bbox, 90)
    assert 32 <= w <= 2500
    assert 32 <= h <= 2500
    assert w > h


def test_dem_tiff_flips_north_up() -> None:
    arr = np.array([[1.0, 2.0], [3.0, 4.0], [5.0, 6.0]], dtype=np.float32)
    buf = BytesIO()
    tifffile.imwrite(buf, arr)
    bbox = {"min_lat": 0.0, "max_lat": 2.0, "min_lng": 10.0, "max_lng": 11.0}
    _lats, lngs, grid = dem_tiff_to_grid(buf.getvalue(), bbox)
    assert grid[0] == [5.0, 6.0]
    assert grid[-1] == [1.0, 2.0]
    assert lngs[0] == 10.0
    assert lngs[-1] == 11.0
