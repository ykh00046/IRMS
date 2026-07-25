"""점도 판정: 표본 부족 시 σ 판정 보류 + 등록 즉시/저장 후 판정 일치 (2026-07-25 감사).

두 결함:
  1. 표본이 3~5개뿐이면 stdev 가 우연히 아주 작게 나와(49.0/49.0/49.1 → σ=0.058)
     정상 산포인 49.4 가 '이상'으로 떴다. 배합 연계로 자동 생성된 규격 미설정 신규
     반제품이 정확히 이 구간을 지나므로, 현장이 판정 자체를 불신하게 되는 경로였다.
  2. classify_value(등록 즉시 판정)는 새 값을 표본에서 빼고 계산하는데 analyze_product
     (저장 후 목록)는 포함해 계산해, 같은 응답 안에서 new_reading.status='이상' 과
     readings[] 의 같은 행 status='정상' 이 모순됐다.
"""

from src.services.viscosity_service import (
    MIN_SIGMA_SAMPLES,
    _classify,
    _control_limits,
)

# 소스에 리터럴 탭/줄바꿈을 넣지 않기 위한 상수(임포트 TSV 조립용).
TAB = chr(9)
NL = chr(10)

_NO_SPEC = {"target": None, "upper_limit": None, "lower_limit": None, "sigma_k": 3.0}
_WITH_SPEC = {"target": 49.0, "upper_limit": 50.0, "lower_limit": 48.0, "sigma_k": 3.0}


def test_sigma_limits_are_withheld_until_enough_samples():
    control = _control_limits(_NO_SPEC, [49.0, 49.0, 49.1])
    assert control["sigma_ready"] is False
    assert control["ucl"] is None and control["lcl"] is None
    # 예전에는 UCL 49.207 이 나와 이 값이 'anomaly' 였다.
    assert _classify(49.4, _NO_SPEC, control)["status"] == "normal"


def test_sigma_judgement_resumes_once_samples_accumulate():
    values = [49.0, 49.0, 49.1, 49.2, 48.9, 49.05, 49.15, 48.95]
    assert len(values) >= MIN_SIGMA_SAMPLES
    control = _control_limits(_NO_SPEC, values)
    assert control["sigma_ready"] is True
    assert control["ucl"] is not None
    assert _classify(49.4, _NO_SPEC, control)["status"] == "anomaly"


def test_spec_limits_apply_regardless_of_sample_size():
    """규격은 공학 기준이라 표본 수와 무관하게 항상 적용된다(보류 대상 아님)."""
    control = _control_limits(_WITH_SPEC, [49.0, 49.0, 49.1])
    assert control["sigma_ready"] is False
    verdict = _classify(51.0, _WITH_SPEC, control)
    assert verdict["status"] == "anomaly"
    assert "spec_high" in verdict["reasons"]


def test_immediate_and_stored_judgement_agree():
    """등록 즉시 판정이 새 값을 표본에 포함해, 저장 후 목록 판정과 같은 결론을 낸다."""
    import importlib
    import uuid

    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    client = TestClient(mainmod.app)
    assert client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    ).status_code == 200
    headers = {"x-csrftoken": client.cookies.get("csrftoken")}

    code = "VS" + uuid.uuid4().hex[:4].upper()
    # 점도 제품은 레시피에 있는 반제품만 등록할 수 있다 — 레시피를 먼저 만든다.
    imported = client.post(
        "/api/recipes/import",
        json={"raw_text": (TAB.join(["반제품명", "원료A", "원료B"]) + NL
                           + TAB.join([code, "60", "40"])),
              "product_code": code},
        headers=headers,
    )
    assert imported.status_code == 200, imported.text
    created = client.post(
        "/api/viscosity/products",
        json={"code": code, "name": code, "sigma_k": 3.0},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    pid = created.json()["id"]

    # 판정이 활성화될 만큼 표본을 쌓는다.
    base = [49.0, 49.0, 49.1, 49.2, 48.9, 49.05, 49.15, 48.95]
    for i, v in enumerate(base):
        res = client.post(
            "/api/viscosity/readings",
            json={"product_id": pid, "lot_no": f"{code}{i:04d}", "viscosity": v,
                  "measured_date": "2026-07-25"},
            headers=headers,
        )
        assert res.status_code == 200, res.text

    # 명백한 이상값을 넣고, 즉시 판정과 저장된 목록의 판정이 일치하는지 본다.
    res = client.post(
        "/api/viscosity/readings",
        json={"product_id": pid, "lot_no": f"{code}9999", "viscosity": 60.0,
              "measured_date": "2026-07-25"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    payload = res.json()
    immediate = (payload.get("new_reading") or {}).get("status")
    stored = next(
        (r["status"] for r in payload.get("readings", []) if r["lot_no"] == f"{code}9999"),
        None,
    )
    assert immediate == stored, f"즉시 판정({immediate}) != 저장 후 판정({stored})"
