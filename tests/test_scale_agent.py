"""저울 에이전트(A&D 프레임 파서·PRINT 이벤트) + 배합 허용 편차(±0.05g) 검증."""

from __future__ import annotations

from scale_agent.agent import (
    EventBus,
    Scale,
    parse_frame,
    resolve_comm,
    scale_entries,
)
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


# ── Mettler MT-SICS (XP10002S 등) ───────────────────────────────
def test_sics_stable_and_dynamic():
    stable = parse_frame("S S     105.00 g", protocol="mt-sics")
    assert stable == {"header": "ST", "stable": True, "overload": False, "value": 105.0, "unit": "g"}
    dynamic = parse_frame("S D 104.87 g", protocol="mt-sics")
    assert dynamic["stable"] is False and dynamic["value"] == 104.87


def test_sics_overload_and_invalid():
    ol = parse_frame("S +", protocol="mt-sics")
    assert ol["overload"] is True
    assert parse_frame("S I", protocol="mt-sics") is None  # 처리불가 → 무시
    assert parse_frame("ES", protocol="mt-sics") is None   # 문법 오류 응답
    assert parse_frame("ST,+0004775.7   g", protocol="mt-sics") is None  # A&D 프레임 혼입


def test_sics_print_template_lines():
    """PRINT(전송) 키 인쇄 템플릿 출력도 안정값으로 해석(수신 전용 모드)."""
    net = parse_frame("N      105.00 g", protocol="mt-sics")
    assert net["stable"] is True and net["value"] == 105.0
    bare = parse_frame("   4775.7 g", protocol="mt-sics")
    assert bare["stable"] is True and bare["value"] == 4775.7
    kg = parse_frame("1.2345 kg", protocol="mt-sics")
    assert kg["value"] == 1234.5 and kg["unit"] == "g"
    # 오탐 방지: ES/문자열/단위 없는 숫자는 여전히 무시
    assert parse_frame("ES", protocol="mt-sics") is None
    assert parse_frame("105.00", protocol="mt-sics") is None
    assert parse_frame("G 105.00 g", protocol="mt-sics") is None  # 총중량 줄은 제외


def test_protocol_presets():
    and_comm = resolve_comm({"protocol": "and"})
    assert (and_comm["baudrate"], and_comm["bytesize"], and_comm["parity"]) == (2400, 7, "E")
    assert and_comm["query"] == b"Q\r\n"
    sics_comm = resolve_comm({"protocol": "mt-sics"})
    assert (sics_comm["baudrate"], sics_comm["bytesize"], sics_comm["parity"]) == (9600, 8, "N")
    assert sics_comm["query"] == b"SI\r\n"
    # 명시 오버라이드가 프리셋보다 우선
    custom = resolve_comm({"protocol": "mt-sics", "baudrate": 19200})
    assert custom["baudrate"] == 19200


# ── PRINT 푸시 이벤트 분배 (다중 저울 공용 EventBus) ─────────────
def _scale(name="GX", protocol="and"):
    bus = EventBus()
    return Scale({"name": name, "protocol": protocol}, bus, set()), bus


def test_print_push_becomes_event():
    s, bus = _scale()
    s._handle_frame(parse_frame("ST,+0004775.7   g"))
    s._handle_frame(parse_frame("ST,+0000181.0   g"))
    items, last = bus.after(0)
    assert [e["value"] for e in items] == [4775.7, 181.0]
    assert [e["source"] for e in items] == ["GX", "GX"]
    assert last == 2
    # after 커서 이후만
    items2, _ = bus.after(1)
    assert [e["value"] for e in items2] == [181.0]


def test_two_scales_share_one_event_stream():
    """A&D + Mettler 두 저울의 PRINT 가 한 스트림으로 합쳐진다(전환 불필요)."""
    bus = EventBus()
    gx = Scale({"name": "GX", "protocol": "and"}, bus, set())
    xp = Scale({"name": "XP", "protocol": "mt-sics"}, bus, set())
    gx._handle_frame(parse_frame("ST,+0004775.7   g"))
    xp._handle_frame(parse_frame("S S     105.00 g", protocol="mt-sics"))
    items, last = bus.after(0)
    assert [(e["source"], e["value"]) for e in items] == [("GX", 4775.7), ("XP", 105.0)]
    assert last == 2


def test_unstable_or_overload_not_evented():
    s, bus = _scale()
    s._handle_frame(parse_frame("US,+0000012.3   g"))  # 측정 중
    s._handle_frame(parse_frame("OL,+9999999.9   g"))  # 과부하
    assert bus.after(0) == ([], 0)


def test_q_response_consumed_not_evented():
    """질의(/weight) 응답은 이벤트로 새지 않는다(이중 입력 방지)."""
    s, bus = _scale()
    s._expect_q = True
    frame = parse_frame("ST,+0000058.0   g")
    s._handle_frame(frame)
    assert bus.after(0) == ([], 0)           # 이벤트 아님
    assert s._q_result == frame              # 질의 응답으로 전달
    assert s._q_waiter.is_set()


def test_scale_entries_multi_and_legacy():
    multi = scale_entries({"scales": [
        {"protocol": "and", "port": "COM4"},
        {"name": "XP10002S", "protocol": "mt-sics", "port": "COM3", "yield_to": ["COMBINATION.exe"]},
    ]})
    assert [e["name"] for e in multi] == ["and", "XP10002S"]
    # 구 단일(평평한) 설정도 저울 1대로 해석 (하위 호환)
    legacy = scale_entries({"protocol": "and", "port": "COM4", "yield_to": []})
    assert len(legacy) == 1 and legacy[0]["port"] == "COM4"


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
