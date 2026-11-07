from __future__ import annotations

from app.services.address import format_vworld_address


def test_format_vworld_address_prefers_road_and_simplifies_dong() -> None:
    payload = {
        "response": {
            "result": [
                {
                    "type": "parcel",
                    "text": "서울특별시 서초구 서초동 1376-3",
                    "structure": {"level4L": "서초동"},
                },
                {
                    "type": "road",
                    "text": "서울특별시 서초구 남부순환로 2584 (서초동,서초구청)",
                    "structure": {
                        "level3": "서초동",
                        "level4L": "남부순환로",
                        "level4A": "서초2동",
                    },
                },
            ]
        }
    }
    assert format_vworld_address(payload) == "서울특별시 서초구 남부순환로 2584 (서초동)"


def test_format_vworld_address_appends_dong_when_missing() -> None:
    payload = {
        "response": {
            "result": {
                "type": "road",
                "text": "서울특별시 서초구 서초대로 77",
                "structure": {"level3": "서초동", "level4L": "서초대로"},
            }
        }
    }
    assert format_vworld_address(payload) == "서울특별시 서초구 서초대로 77 (서초동)"


def test_format_vworld_address_empty() -> None:
    assert format_vworld_address({}) is None
    assert format_vworld_address({"response": {"result": []}}) is None
