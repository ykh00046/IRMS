"""Defense-in-depth hardening 회귀 테스트.

한 배치로 추가된 5개 방어 조치를 검증한다:
  1. Content-Security-Policy 응답 헤더(보수적 same-origin 잠금)
  2. 서명 샘플 파일명 경로 탈출(../) 거부
  3. 공개 POST 두 곳(blend session login / workers)의 rate-limit 데코레이터가
     본문 모델과 함께 정상 동작(slowapi 422 회귀 없음)
  4. 레시피 임포트 raw_text 길이 상한(과대 붙여넣기 422)
  5. 미처리 500 예외의 파일 로깅(errors.log)
"""

import importlib
import io
import uuid

import pytest
from fastapi.testclient import TestClient
from PIL import Image


def _reload_app(monkeypatch, data_dir=None):
    """src.config + src.main 재적재. 결정적 세션/시드 설정을 강제한다."""
    monkeypatch.setenv("IRMS_ENV", "development")
    monkeypatch.setenv("IRMS_REQUIRE_SESSION_SECRET", "false")
    monkeypatch.setenv("IRMS_SESSION_SECRET", "0" * 64)
    monkeypatch.setenv("IRMS_SEED_DEMO_DATA", "false")
    if data_dir is not None:
        monkeypatch.setenv("IRMS_DATA_DIR", str(data_dir))

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    return mainmod


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (120, 40), (255, 255, 255, 0)).save(buf, "PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Task 1 — Content-Security-Policy
# ---------------------------------------------------------------------------

_EXPECTED_CSP = (
    "default-src 'self'; "
    "frame-ancestors 'none'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com https://cdn.jsdelivr.net data:; "
    "connect-src 'self' http://127.0.0.1:8787; "
    "script-src 'self' 'unsafe-inline'"
)


def test_csp_header_present_and_exact(monkeypatch):
    mainmod = _reload_app(monkeypatch)
    client = TestClient(mainmod.app)
    resp = client.get("/health")
    assert resp.headers.get("Content-Security-Policy") == _EXPECTED_CSP


def test_csp_present_on_404(monkeypatch):
    """CSP 도 outermost 미들웨어라 4xx 에도 붙어야 한다."""
    mainmod = _reload_app(monkeypatch)
    client = TestClient(mainmod.app)
    resp = client.get("/this-path-does-not-exist")
    assert resp.status_code == 404
    assert resp.headers.get("Content-Security-Policy") == _EXPECTED_CSP


def test_csp_keeps_unsafe_inline(monkeypatch):
    """인라인 스크립트/스타일이 템플릿에 남아 있어 unsafe-inline 유지가 필수."""
    mainmod = _reload_app(monkeypatch)
    client = TestClient(mainmod.app)
    csp = client.get("/health").headers.get("Content-Security-Policy", "")
    assert "script-src 'self' 'unsafe-inline'" in csp
    assert "style-src 'self' 'unsafe-inline'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp


# ---------------------------------------------------------------------------
# Task 2 — 서명 샘플 경로 탈출 차단
# ---------------------------------------------------------------------------

def _fresh_signature(tmp_path, monkeypatch):
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path))
    import src.config as cfg
    importlib.reload(cfg)
    from src.services import signature_samples
    importlib.reload(signature_samples)
    return signature_samples


@pytest.mark.parametrize("evil", ["../evil", "..\\evil", "a/b", "a\\b", ".."])
def test_signature_rejects_path_separators(tmp_path, monkeypatch, evil):
    ss = _fresh_signature(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        ss.add_sample("charge", evil, _png())
    # 폴더 밖으로 새 파일이 새지 않았는지 — SAMPLES_DIR 안에만 png 가 있어야 한다.
    from pathlib import Path
    escaped = list((Path(str(tmp_path)).parent).glob("*_charge_*.png"))
    assert escaped == []


def test_signature_normal_worker_still_ok(tmp_path, monkeypatch):
    ss = _fresh_signature(tmp_path, monkeypatch)
    assert ss.add_sample("charge", "홍길동", _png()) == "홍길동_charge_1.png"


# ---------------------------------------------------------------------------
# Task 3 — 공개 POST rate-limit (slowapi 422 회귀 없음)
# ---------------------------------------------------------------------------

def test_blend_session_login_decorator_no_422(monkeypatch):
    """@limiter.limit + 본문 모델이 미등록 작업자에 422 가 아니라 404 를 줘야 한다.

    422 가 나오면 from __future__ import annotations 회귀(slowapi 버그)를 의미한다.
    """
    mainmod = _reload_app(monkeypatch)
    client = TestClient(mainmod.app)
    # blend session login 은 CSRF 면제라 헤더 없이 검증 계층까지 도달한다.
    resp = client.post(
        "/api/blend/session/login",
        json={"worker": "존재하지않는작업자" + uuid.uuid4().hex[:6]},
    )
    assert resp.status_code == 404, resp.text


def test_workers_register_decorator_ok(monkeypatch):
    """@limiter.limit 를 단 POST /api/workers 가 정상 등록(200)돼야 한다(422 회귀 없음)."""
    mainmod = _reload_app(monkeypatch)
    client = TestClient(mainmod.app)
    client.get("/api/workers")  # csrf 쿠키 확보
    tok = client.cookies.get("csrftoken")
    headers = {"x-csrftoken": tok} if tok else {}
    name = "속도제한테스트" + uuid.uuid4().hex[:6]
    resp = client.post("/api/workers", json={"name": name}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == name


def test_no_future_annotations_in_limited_routers():
    """rate-limit + 본문 모델 라우터 상단에 future annotations 가 없어야 한다(slowapi 422)."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in (
        "src/routers/blend_session_routes.py",
        "src/routers/worker_routes.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        assert "from __future__ import annotations" not in text, rel


# ---------------------------------------------------------------------------
# Task 4 — 레시피 임포트 raw_text 길이 상한
# ---------------------------------------------------------------------------

def test_import_request_rejects_oversized_raw_text():
    from pydantic import ValidationError

    from src.routers.models import ImportRequest

    with pytest.raises(ValidationError):
        ImportRequest(raw_text="x" * 200_001)


def test_import_request_accepts_normal_raw_text():
    from src.routers.models import ImportRequest

    body = ImportRequest(raw_text="반제품\tPB\t100")
    assert body.raw_text.startswith("반제품")
    # 상한 경계(정확히 200,000자)는 허용.
    assert ImportRequest(raw_text="y" * 200_000).raw_text


# ---------------------------------------------------------------------------
# Task 5 — 미처리 500 예외 파일 로깅
# ---------------------------------------------------------------------------

def test_unhandled_exception_written_to_errors_log(tmp_path, monkeypatch):
    mainmod = _reload_app(monkeypatch, data_dir=tmp_path)
    # 캐시된 로거 리셋(다른 테스트가 먼저 초기화했을 수 있음).
    mainmod._error_logger = None
    mainmod._log_unhandled_exception("GET", "/boom", ValueError("폭발"))

    log_file = tmp_path / "errors.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "GET /boom" in content
    assert "ValueError" in content
    assert "폭발" in content


def test_log_unhandled_exception_never_raises(monkeypatch):
    """로깅 실패가 요청/응답을 절대 깨지 않도록 — 어떤 경우에도 예외를 던지지 않는다."""
    mainmod = _reload_app(monkeypatch)
    # 로거 획득이 실패하도록 강제해도 조용히 넘어가야 한다.
    monkeypatch.setattr(mainmod, "_get_error_logger", lambda: (_ for _ in ()).throw(OSError("x")))
    # 예외가 밖으로 새지 않으면 성공.
    mainmod._log_unhandled_exception("POST", "/x", RuntimeError("y"))
