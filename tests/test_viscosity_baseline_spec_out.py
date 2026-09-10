"""규격 밖(사용 금지) 측정은 중심·σ 기준을 만들지 않는다(2026-09-10 현장 지적).

APB17 2026: 320.0 과 337.8(사용 금지 340 이하) 두 건이 σ 를 11.99 로 부풀려
관리 하한을 326.6 까지 끌어내렸다. 이 두 건을 뺀 기준은 σ 10.58, 하한 331.8.
"""
from src.services import viscosity_service as vs


def _product(**over):
    base = {
        "id": 1, "code": "T", "name": "T", "target": None,
        "lower_limit": 340.0, "upper_limit": None,
        "warn_low": None, "warn_high": None, "sigma_k": 3.0,
    }
    base.update(over)
    return base


def _values(n, value):
    return [value] * n


def test_spec_out_values_do_not_shape_the_baseline():
    product = _product()
    inside = [360.0, 362.0, 364.0, 358.0, 366.0, 361.0, 363.0, 359.0, 365.0, 357.0]
    control = vs._control_limits(product, inside + [320.0, 337.8])

    assert control["spec_out_n"] == 2
    assert control["baseline_n"] == len(inside)
    assert control["n"] == len(inside) + 2          # 표본 수는 그대로 보여 준다
    clean = vs._control_limits(product, inside)
    assert control["std"] == clean["std"]
    assert control["center"] == clean["center"]
    assert control["lcl"] == clean["lcl"]


def test_baseline_keeps_everything_when_too_few_would_remain():
    """규격 밖이 대부분이면 기준을 버리지 않는다 — 기준 없음보다 낫다."""
    product = _product()
    values = [300.0, 305.0, 310.0, 315.0, 320.0, 325.0, 330.0, 335.0, 360.0]
    control = vs._control_limits(product, values)
    assert control["spec_out_n"] == 0
    assert control["baseline_n"] == len(values)


def test_anomaly_bounds_take_the_inner_of_spec_and_sigma():
    product = _product()
    # 산포가 넓어 3σ 하한이 사용 금지선(340) 밖으로 나가는 표본(APB17 과 같은 모양).
    inside = [345.0, 350.0, 355.0, 360.0, 365.0, 370.0, 375.0, 380.0, 358.0, 368.0]
    control = vs._control_limits(product, inside)
    assert control["lcl"] < 340.0                   # σ 하한은 사용 금지선보다 바깥
    assert control["anomaly_low"] == 340.0          # 실제 경계는 안쪽인 사용 금지선
    assert control["anomaly_high"] == control["ucl"]


def test_product_without_spec_is_unaffected():
    product = _product(lower_limit=None)
    values = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 1.0]
    control = vs._control_limits(product, values)
    assert control["spec_out_n"] == 0
    assert control["baseline_n"] == len(values)
    assert control["anomaly_low"] == control["lcl"]
