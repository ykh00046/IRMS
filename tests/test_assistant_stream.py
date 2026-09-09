"""AI 도우미 스트리밍(SSE) 테스트.

가짜 모델(IRMS_ASSISTANT_FAKE)로 돌린다. 실제 API 호출은 하지 않는다.
확인 사항: 프레임 순서(meta→tool_call→token→done), 꺼짐 안내, 한도 초과 429,
Groq 계획 JSON 파서의 관대함.
"""

import importlib
import json

import pytest


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


def _csrf(client):
    token = client.cookies.get("csrftoken")
    return {"x-csrftoken": token} if token else {}


def _login_admin(client):
    client.get("/api/blend/records")
    res = client.post(
        "/api/auth/management-login", json={"username": "admin", "password": "admin"}
    )
    assert res.status_code == 200, res.text


def _parse_sse(text):
    """프레임 문자열을 [(event, data), ...] 로 푼다. 하트비트 주석은 건너뛴다."""
    events = []
    for block in text.split("\n\n"):
        block = block.strip()
        if not block or block.startswith(":"):
            continue
        name = None
        payload = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
        if name:
            events.append((name, payload))
    return events


def _enable(client, enabled=True):
    _login_admin(client)
    res = client.put(
        "/api/assistant/settings", json={"enabled": enabled}, headers=_csrf(client)
    )
    assert res.status_code == 200, res.text


@pytest.fixture(autouse=True)
def _reset_state():
    from src.services.assistant import session as assistant_session
    from src.services.assistant import tools

    assistant_session.reset()
    tools.clear_cache()
    yield
    assistant_session.reset()
    tools.clear_cache()
    from src.db import get_connection
    from src.services.assistant import settings as aset

    try:
        with get_connection() as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (aset.ENABLED_KEY,))
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


@pytest.fixture
def fake_client():
    client = _client()
    _enable(client)
    import src.config as cfg

    cfg.ASSISTANT_FAKE = True
    try:
        yield client
    finally:
        cfg.ASSISTANT_FAKE = False


def test_stream_returns_sse_sequence(fake_client):
    res = fake_client.post(
        "/api/assistant/stream",
        json={"query": "오늘 배합 몇 건이야?"},
        headers=_csrf(fake_client),
    )
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    assert res.headers["cache-control"] == "no-cache, no-transform"
    assert res.headers["x-accel-buffering"] == "no"

    events = _parse_sse(res.text)
    names = [name for name, _ in events]
    assert names[0] == "meta"
    assert names[-1] == "done"
    assert "tool_call" in names
    assert "token" in names
    # 순서: meta → tool_call → token → done
    assert names.index("tool_call") < names.index("token") < names.index("done")

    meta = events[0][1]
    assert meta["enabled"] is True
    assert meta["provider"] == "fake"
    assert meta["session_id"]

    done = events[-1][1]
    assert done["answer"].strip()
    assert len(done["tools_used"]) == 1
    assert done["tools_used"][0]["status"] == "done"
    assert done["tools_used"][0]["label"]
    assert len(done["suggestions"]) == 2
    # 토큰을 이어 붙이면 최종 답과 같아야 한다.
    joined = "".join(
        data["text"] for name, data in events if name == "token"
    )
    assert joined == done["answer"]


def test_stream_routes_howto_question_to_usage_guide(fake_client):
    """사용법 말투는 데이터 조회가 아니라 안내 도구로 간다."""
    res = fake_client.post(
        "/api/assistant/stream",
        json={"query": "배합 기록이 저장 안 된 것 같아요"},
        headers=_csrf(fake_client),
    )
    assert res.status_code == 200
    events = _parse_sse(res.text)

    tool_names = [data["name"] for name, data in events if name == "tool_call"]
    assert "get_usage_guide" in tool_names
    labels = [data["label"] for name, data in events if name == "tool_call"]
    assert "사용법 안내" in labels

    done = events[-1][1]
    assert done["answer"]
    assert "작성 중 배합" in done["answer"]
    assert "/blend/drafts" in done["answer"]
    assert "—" not in done["answer"]


def test_stream_context_and_session_are_echoed(fake_client):
    res = fake_client.post(
        "/api/assistant/stream",
        json={
            "query": "점도 상태 알려줘",
            "session_id": "sess-1",
            "context": {"path": "/viscosity", "product": "PB"},
        },
        headers=_csrf(fake_client),
    )
    events = _parse_sse(res.text)
    assert events[0][1]["session_id"] == "sess-1"
    assert events[-1][1]["session_id"] == "sess-1"

    from src.services.assistant import session as assistant_session

    assert len(assistant_session.get_history("sess-1")) == 2

    reset = fake_client.post(
        "/api/assistant/reset",
        json={"session_id": "sess-1"},
        headers=_csrf(fake_client),
    )
    assert reset.status_code == 200
    assert reset.json()["cleared"] is True
    assert assistant_session.get_history("sess-1") == []


def test_stream_when_disabled_emits_meta_then_error():
    client = _client()
    _enable(client, enabled=False)
    import src.config as cfg

    cfg.ASSISTANT_FAKE = True
    try:
        res = client.post(
            "/api/assistant/stream",
            json={"query": "오늘 배합 요약"},
            headers=_csrf(client),
        )
    finally:
        cfg.ASSISTANT_FAKE = False

    assert res.status_code == 200
    events = _parse_sse(res.text)
    assert [name for name, _ in events] == ["meta", "error"]
    assert events[0][1]["enabled"] is False
    assert events[1][1]["code"] == "DISABLED"
    assert "책임자" in events[1][1]["message"]


def test_stream_rejects_blank_query(fake_client):
    res = fake_client.post(
        "/api/assistant/stream", json={"query": "   "}, headers=_csrf(fake_client)
    )
    assert res.status_code == 400
    res = fake_client.post(
        "/api/assistant/stream", json={"query": ""}, headers=_csrf(fake_client)
    )
    assert res.status_code == 422  # 모델 최소 길이


def test_stream_rate_limited_returns_429(fake_client):
    """한도 초과는 스트림이 아니라 429 JSON 으로 끊는다."""
    from src.limiter import limiter

    limiter.enabled = True
    try:
        codes = []
        for _ in range(8):
            res = fake_client.post(
                "/api/assistant/stream",
                json={"query": "오늘 배합 요약"},
                headers=_csrf(fake_client),
            )
            codes.append(res.status_code)
            if res.status_code == 429:
                body = res.json()
                assert body["code"] == "RATE_LIMITED"
                assert body["retry_after"] == 60
                assert res.headers["retry-after"] == "60"
                break
    finally:
        limiter.enabled = False
        limiter.reset()
    assert 429 in codes, f"한도가 걸리지 않았다: {codes}"


def test_parse_tool_plan_is_tolerant():
    from src.services.assistant import llm

    assert llm.parse_tool_plan('{"tool": "get_attention", "parameters": {}}') == (
        "get_attention",
        {},
    )
    # 키 이름이 흔들려도 받아준다.
    assert llm.parse_tool_plan('{"tool_code": "get_attention"}') == ("get_attention", {})
    assert llm.parse_tool_plan('{"tool_name": "get_attention", "args": {"a": 1}}') == (
        "get_attention",
        {"a": 1},
    )
    # 코드펜스와 앞뒤 잡담이 섞여도 읽는다.
    fenced = '```json\n{"tool": "get_recipe", "parameters": {"product": "PB"}}\n```'
    assert llm.parse_tool_plan(fenced) == ("get_recipe", {"product": "PB"})
    chatty = '이걸 부르면 됩니다: {"tool": "get_recipe", "params": {"product": "PB"}}'
    assert llm.parse_tool_plan(chatty) == ("get_recipe", {"product": "PB"})


def test_parse_tool_plan_rejects_unknown_tool():
    from src.services.assistant import llm

    assert llm.parse_tool_plan('{"tool": "drop_everything"}') is None
    assert llm.parse_tool_plan('{"tool": "none"}') is None
    assert llm.parse_tool_plan("도구 필요 없습니다") is None
    assert llm.parse_tool_plan("") is None


def test_error_classification():
    from src.services.assistant import llm

    class _Err(Exception):
        code = 429

    assert llm.extract_http_status(_Err()) == 429
    assert llm.is_fallbackable(_Err()) is True
    assert llm.is_fallbackable(Exception("connection timed out")) is True
    assert llm.is_fallbackable(Exception("bad request")) is False


def test_system_prompt_has_rules_and_context():
    from datetime import date as date_type

    from src.services.assistant import llm

    prompt = llm.build_system_prompt(
        date_type(2026, 9, 9), {"path": "/viscosity", "product": "PB"}
    )
    assert "2026-09-09" in prompt
    assert "이번 주" in prompt and "이번 달" in prompt
    assert "지어내지" in prompt
    assert "잉크" in prompt  # 쓰지 말라는 지시로만 등장한다
    assert "/viscosity" in prompt and "PB" in prompt


def test_session_store_trims_and_expires():
    from src.services.assistant import session as store

    store.reset()
    for i in range(15):
        store.append_exchange("s", f"q{i}", f"a{i}")
    history = store.get_history("s")
    assert len(history) == store.SESSION_MAX_ENTRIES
    assert history[0] == ("user", "q5")
    assert store.clear("s") is True
    assert store.session_count() == 0
