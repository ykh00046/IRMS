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
    # 현장 XP10002S 실측 포맷: 순번 + N + 값 + 단위
    field = parse_frame(b" 1    N    -4544.27 g   \r\n", protocol="mt-sics")
    assert field["stable"] is True and field["value"] == -4544.27
    net = parse_frame("N      105.00 g", protocol="mt-sics")
    assert net["stable"] is True and net["value"] == 105.0
    bare = parse_frame("   4775.7 g", protocol="mt-sics")
    assert bare["stable"] is True and bare["value"] == 4775.7
    kg = parse_frame("1.2345 kg", protocol="mt-sics")
    assert kg["value"] == 1234.5 and kg["unit"] == "g"
    # 오탐 방지: ES/문자열/단위 없는 숫자는 여전히 무시
    assert parse_frame("ES", protocol="mt-sics") is None
    assert parse_frame("105.00", protocol="mt-sics") is None
    assert parse_frame("G 105.00 g", protocol="mt-sics") is None   # 총중량 줄 제외
    assert parse_frame("1 T 12.00 g", protocol="mt-sics") is None  # 용기 줄 제외


def test_protocol_presets():
    and_comm = resolve_comm({"protocol": "and"})
    assert (and_comm["baudrate"], and_comm["bytesize"], and_comm["parity"]) == (2400, 7, "E")
    assert and_comm["query"] == b"Q\r\n"
    sics_comm = resolve_comm({"protocol": "mt-sics"})
    assert (sics_comm["baudrate"], sics_comm["bytesize"], sics_comm["parity"]) == (9600, 8, "N")
    assert sics_comm["query"] == b"SI\r\n"
    cas_comm = resolve_comm({"protocol": "cas"})
    assert (cas_comm["baudrate"], cas_comm["bytesize"], cas_comm["parity"]) == (9600, 8, "N")
    assert cas_comm["query"] == b""  # PRINT 푸시 위주 — 능동 질의 없음
    # 명시 오버라이드가 프리셋보다 우선(저울이 2400 이면)
    custom = resolve_comm({"protocol": "cas", "baudrate": 2400})
    assert custom["baudrate"] == 2400


# ── CAS(카스) CBX/CBL 저울 ──────────────────────────────────────
def test_cas_stable_and_unstable():
    st = parse_frame("ST,+00123.45 g", protocol="cas")
    assert st == {"header": "ST", "stable": True, "overload": False, "value": 123.45, "unit": "g"}
    us = parse_frame("US,-0012.3 g", protocol="cas")
    assert us["stable"] is False and us["value"] == -12.3


def test_cas_with_weight_type_field():
    """GS(총중량)/NT(순중량) 중간 필드가 있는 계열도 값만 뽑는다."""
    gs = parse_frame("ST,GS,+00123.45 g", protocol="cas")
    assert gs["stable"] is True and gs["value"] == 123.45
    nt = parse_frame("US,NT,+0005.00 g", protocol="cas")
    assert nt["stable"] is False and nt["value"] == 5.0


def test_cas_overload_kg_and_padding():
    ol = parse_frame("OL,+9999999 g", protocol="cas")
    assert ol["overload"] is True and ol["value"] == 0.0
    kg = parse_frame("ST,+0001.234 kg", protocol="cas")
    assert kg["value"] == 1234.0 and kg["unit"] == "g"
    # 부호와 값 사이 공백 패딩 허용
    padded = parse_frame("ST,+  123.45 g", protocol="cas")
    assert padded["value"] == 123.45
    # 단위가 별도 콤마 필드인 계열도 처리
    comma_unit = parse_frame("ST,GS,+00123.45,g", protocol="cas")
    assert comma_unit["value"] == 123.45 and comma_unit["unit"] == "g"


def test_cas_garbage_returns_none():
    assert parse_frame("XX,+00123.45 g", protocol="cas") is None  # 알 수 없는 헤더
    assert parse_frame("ST", protocol="cas") is None              # 값 없음
    assert parse_frame("hello", protocol="cas") is None
    assert parse_frame("ST,GS", protocol="cas") is None           # 값 필드 없음


def test_cas_eb_type_shimadzu():
    """시마즈 EB type — CBX-22KH 실물 계열. 1문자 안정표시(S/U) + 값 + 단위."""
    st = parse_frame("S  123.45g", protocol="cas")
    assert st == {"header": "ST", "stable": True, "overload": False, "value": 123.45, "unit": "g"}
    us = parse_frame("U  123.45g", protocol="cas")
    assert us["stable"] is False and us["value"] == 123.45
    neg = parse_frame(b"S-186.65g\r\n", protocol="cas")
    assert neg["stable"] is True and neg["value"] == -186.65
    kg = parse_frame("S  1.2345kg", protocol="cas")
    assert kg["value"] == 1234.5 and kg["unit"] == "g"
    # 과부하(값 자리에 OL)
    ol = parse_frame("S    OL g", protocol="cas")
    assert ol["overload"] is True
    # 쓰레기 거부: 값 없음/무게 아님(PCS 등)
    assert parse_frame("S", protocol="cas") is None
    assert parse_frame("SI", protocol="cas") is None
    assert parse_frame("S  12PCS", protocol="cas") is None


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


def test_event_dedupe_same_value_within_window():
    """같은 저울·같은 값이 2초 안에 반복되면 중복 전송으로 무시."""
    now = [100.0]
    bus = EventBus(clock=lambda: now[0])
    s = Scale({"name": "XP", "protocol": "mt-sics"}, bus, set())
    frame = parse_frame(" 1 N 105.00 g", protocol="mt-sics")
    s._handle_frame(dict(frame))
    now[0] += 1.0
    s._handle_frame(dict(frame))       # 1초 뒤 같은 값 → 무시
    now[0] += 1.5
    s._handle_frame(dict(frame))       # 이전 무시 시점 기준 1.5초 → 여전히 무시(연속 스트림 억제)
    now[0] += 3.0
    s._handle_frame(dict(frame))       # 3초 경과 → 새 계량으로 인정
    items, last = bus.after(0)
    assert [e["value"] for e in items] == [105.0, 105.0]
    assert last == 2
    # 다른 값은 즉시 통과
    other = parse_frame(" 2 N 58.01 g", protocol="mt-sics")
    s._handle_frame(other)
    items2, _ = bus.after(0)
    assert [e["value"] for e in items2][-1] == 58.01


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


# ── CAS 수신 전용 자동 전환 (질의 없는 저울 probe 오탐 방지) ────────
class _FakeSerial:
    """serial.Serial 대역 — 생성 호출 횟수/포트만 기록. deny 회수만큼 access-denied."""
    opens: list = []
    deny_remaining = 0

    def __init__(self, **kwargs):
        _FakeSerial.opens.append(kwargs)
        if _FakeSerial.deny_remaining > 0:
            _FakeSerial.deny_remaining -= 1
            raise PermissionError("could not open port 'COM3': Access is denied.")

    def close(self):
        pass


def _make_cas_scale(monkeypatch, deny=0):
    import scale_agent.agent as agent
    fake = type("S", (), {"Serial": _FakeSerial})
    monkeypatch.setattr(agent, "serial", fake)
    scale = Scale({"protocol": "cas", "port": "COM3", "name": "카스"}, EventBus(), set())
    # 리더 스레드가 connect 를 중복 호출하지 않도록 즉시 정지(첫 sleep(3) 중에 멈춘다).
    scale._stop.set()
    _FakeSerial.opens = []          # 정지 후 카운터 리셋(생성 시 리더의 잔여 호출 배제)
    _FakeSerial.deny_remaining = deny
    return scale


def test_cas_fixed_port_uses_passive_single_open(monkeypatch):
    """CAS(질의 없음)+고정 포트 → 수신 전용으로 '한 번만' 연다(probe 반복 open/close 없음)."""
    scale = _make_cas_scale(monkeypatch)
    port = scale.connect()
    assert port == "COM3"
    assert len(_FakeSerial.opens) == 1  # 15조합 probe 가 아니라 단 1회 오픈
    assert scale._serial is not None


def test_cas_passive_retries_on_access_denied(monkeypatch):
    """access-denied 는 직전 핸들 해제 지연일 수 있어 짧게 재시도 후 붙는다."""
    scale = _make_cas_scale(monkeypatch, deny=2)  # 2회 거부 후 성공
    port = scale.connect()
    assert port == "COM3"
    assert len(_FakeSerial.opens) == 3  # 2회 실패 + 1회 성공


def test_cas_passive_gives_up_after_persistent_denial(monkeypatch):
    """계속 거부되면(실제 점유) None 반환 — 리더 루프가 나중에 자동 재시도."""
    scale = _make_cas_scale(monkeypatch, deny=99)
    assert scale.connect() is None
    assert scale._serial is None
