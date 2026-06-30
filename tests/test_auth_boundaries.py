from fastapi.testclient import TestClient

from src.main import create_app


def test_manager_pages_and_apis_require_login() -> None:
    client = TestClient(create_app())

    page_response = client.get("/management", follow_redirects=False)
    api_response = client.get("/api/recipes/progress")

    assert page_response.status_code == 303
    assert page_response.headers["location"].startswith("/management/login")
    assert api_response.status_code == 401


def test_blend_record_create_requires_worker_session() -> None:
    client = TestClient(create_app())
    login = client.post(
        "/api/auth/management-login",
        json={"username": "admin", "password": "admin"},
    )
    token = client.cookies.get("csrftoken")

    assert login.status_code == 200
    assert token
    response = client.post(
        "/api/blend/records",
        headers={"x-csrftoken": token},
        json={
            "product_name": "QA-SESSION",
            "worker": "QA",
            "work_date": "2026-06-30",
            "total_amount": 10,
            "details": [
                {
                    "material_name": "QA-MATERIAL",
                    "ratio": 100,
                    "theory_amount": 10,
                    "actual_amount": 10,
                }
            ],
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "BLEND_WORKER_REQUIRED"
