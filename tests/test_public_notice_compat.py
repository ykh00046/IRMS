from fastapi.testclient import TestClient

from src.main import create_app


def test_legacy_notice_poll_returns_empty_result_on_internal_network() -> None:
    client = TestClient(create_app(), client=("192.168.11.108", 50000))

    response = client.get("/api/public/notice/poll?after_id=29&limit=20")

    assert response.status_code == 200
    assert response.json() == {
        "room": {"key": "notice", "name": "공지", "scope": "notice"},
        "items": [],
        "latest_id": 29,
        "total": 0,
    }


def test_legacy_notice_poll_is_internal_network_only() -> None:
    client = TestClient(create_app())

    response = client.get("/api/public/notice/poll?after_id=29&limit=20")

    assert response.status_code == 403
    assert response.json() == {"detail": "INTERNAL_NETWORK_ONLY"}
