"""점도 경고 문턱(warn_low/warn_high) — 관리 한계 안쪽의 고정 '확인 필요' 선.

사용자 결정(2026-09-08): PB 점도가 48 이하로 내려가면 **경고**로 띄운다. σ 경고 밴드는
표본이 8건 이상 쌓여야 생기고 표본 따라 움직이므로, 이런 현장 규칙은 고정값으로 두어
표본과 무관하게 항상 적용한다. 이상(spec/kσ)이 먼저고, 그 다음 고정 경고, 그 다음 2σ.
"""

from __future__ import annotations

import importlib

from src.services.viscosity_service import _classify, _control_limits

_PB = {"target": None, "upper_limit": None, "lower_limit": None, "sigma_k": 3.0,
       "warn_low": 48.0, "warn_high": None}


def test_warn_low_is_inclusive_and_ignores_sample_size():
    control = _control_limits(_PB, [49.0, 49.1])          # 표본 부족 → σ 판정 보류
    assert control["sigma_ready"] is False
    assert _classify(48.0, _PB, control)["status"] == "warn"
    assert _classify(48.0, _PB, control)["reasons"] == ["warn_low_limit"]
    assert _classify(47.5, _PB, control)["side"] == "low"
    assert _classify(48.1, _PB, control)["status"] == "normal"


def test_spec_anomaly_still_wins_over_warn_threshold():
    product = dict(_PB, lower_limit=47.0)
    control = _control_limits(product, [49.0, 49.1])
    verdict = _classify(46.0, product, control)
    assert verdict["status"] == "anomaly"
    assert "spec_low" in verdict["reasons"]
    assert _classify(47.5, product, control)["status"] == "warn"


def test_warn_high_threshold_mirrors_low():
    product = {"target": None, "upper_limit": None, "lower_limit": None, "sigma_k": 3.0,
               "warn_low": None, "warn_high": 52.0}
    control = _control_limits(product, [49.0])
    assert _classify(52.0, product, control)["reasons"] == ["warn_high_limit"]
    assert _classify(51.9, product, control)["status"] == "normal"


def test_products_without_thresholds_are_unchanged():
    product = {"target": None, "upper_limit": None, "lower_limit": None, "sigma_k": 3.0}
    control = _control_limits(product, [49.0, 49.1])
    assert _classify(10.0, product, control)["status"] == "normal"


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def test_migration_seeds_pb_warn_low_48_and_api_exposes_it():
    client = _client()
    from src.db import get_connection

    with get_connection() as connection:
        row = connection.execute(
            "SELECT warn_low, warn_high FROM viscosity_products WHERE upper(code) = 'PB'"
        ).fetchone()
    assert row is not None
    assert row["warn_low"] == 48.0 and row["warn_high"] is None

    res = client.get("/api/viscosity/products")
    assert res.status_code == 200
    pb = next(p for p in res.json()["items"] if p["code"].upper() == "PB")
    assert pb["warn_low"] == 48.0


def test_update_body_rejects_inverted_warn_thresholds():
    from pydantic import ValidationError

    from src.routers.models import ViscosityProductUpdateBody

    try:
        ViscosityProductUpdateBody(name="PB", warn_low=50, warn_high=48)
    except ValidationError as exc:
        assert "warn_low" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("warn_low >= warn_high 는 거부돼야 한다")


def test_fixed_warn_threshold_folds_into_the_warn_band():
    """관리 기준 그림·문구가 판정과 같은 말을 해야 한다: 2σ 경고선이 48 아래여도 경고 밴드
    하한은 48 로 올라온다(안쪽 선이 이김). 관리 한계(lcl/ucl)는 그대로."""
    values = [49.0, 48.2, 49.8, 49.3, 48.5, 49.9, 48.3, 49.6, 49.1, 48.7]   # σ≈0.6 → 2σ 하한 ≈ 47.8
    base = _control_limits(dict(_PB, warn_low=None), values)
    assert base["sigma_ready"] and base["lwl"] is not None and base["lwl"] < 48.0
    folded = _control_limits(_PB, values)
    assert folded["lwl"] == 48.0
    assert folded["uwl"] == base["uwl"]
    assert folded["lcl"] == base["lcl"] and folded["ucl"] == base["ucl"]
    # 표본이 없어도 고정 경고선은 밴드에 남는다.
    sparse = _control_limits(_PB, [49.0, 49.1])
    assert sparse["lwl"] == 48.0 and sparse["uwl"] is None
