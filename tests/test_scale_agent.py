"""저울 에이전트(A&D 프레임 파서) + 배합 허용 편차(±0.05g) 검증."""

from __future__ import annotations

from scale_agent.agent import parse_frame
from src.services import blend_service


# ── A&D 프레임 파서 ──────────────────────────────────────────────
def test_parse_stable_frame():
    frame = parse_frame("ST,+0004775.7   g")
    assert frame == {
        "header": "ST", "stable": True, "overload": False,
        "value": 4775.7, "unit": "g",
    }


def test_parse_unstable_and_overload():
    us = parse_frame("US,-0000012.3   g")
    assert us["stable"] is False and us["value"] == -12.3
    ol = parse_frame("OL,+9999999.9   g")
    assert ol["overload"] is True


def test_parse_bytes_and_smart_range():
    # GX-10202M 스마트레인지: 0.01g 해상도 값도 그대로
    frame = parse_frame(b"ST,+0000058.01  g\r\n")
    assert frame["value"] == 58.01 and frame["stable"] is True


def test_parse_garbage_returns_none():
    assert parse_frame("") is None
    assert parse_frame("hello world") is None
    assert parse_frame("ST+00123") is None  # 콤마 없음
    assert parse_frame("XX,+0000001.0   g") is None  # 알 수 없는 헤더


# ── 허용 편차 검증(서버) ─────────────────────────────────────────
def _detail(name, theory, actual):
    return {"material_name": name, "theory_amount": theory, "actual_amount": actual}


def test_tolerance_within_005_passes():
    details = [
        _detail("A", 100.0, 100.05),   # 정확히 +0.05 → 허용
        _detail("B", 100.0, 99.95),    # -0.05 → 허용
        _detail("C", 100.0, 100.0),
    ]
    assert blend_service.weighing_tolerance_violations(details) == []


def test_tolerance_exceeded_flags_material():
    details = [
        _detail("A", 100.0, 100.06),   # +0.06 → 초과
        _detail("B", 100.0, 100.0),
        _detail("C", 50.0, 49.9),      # -0.10 → 초과
    ]
    assert blend_service.weighing_tolerance_violations(details) == ["A", "C"]


def test_tolerance_total_variance_unlimited():
    # 자재별로는 전부 ±0.05 이내 — 합계 편차(+0.20g)는 제한하지 않는다
    details = [_detail(f"M{i}", 100.0, 100.05) for i in range(4)]
    assert blend_service.weighing_tolerance_violations(details) == []


def test_tolerance_skips_missing_actual():
    details = [_detail("A", 100.0, None), _detail("B", None, 5.0)]
    assert blend_service.weighing_tolerance_violations(details) == []
