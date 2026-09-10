"""AI 도우미 사용법 안내(guide.py + get_usage_guide) 테스트.

확인 사항:
- 현장에서 실제로 나온 두 물음이 맞는 화면으로 간다.
- 안내에 적힌 경로가 전부 실제 페이지 라우트다(죽은 링크 금지).
- 안내 글에 줄표(—)와 금지어가 없다.
- 화면 지도가 사이드바 메뉴를 빠짐없이 담는다.
"""

import importlib
import re

import pytest


def _client():
    import src.config as cfg
    import src.main as mainmod

    importlib.reload(cfg)
    importlib.reload(mainmod)
    from fastapi.testclient import TestClient

    return TestClient(mainmod.app)


@pytest.fixture(scope="module")
def app_paths():
    """앱이 실제로 들고 있는 GET 페이지 경로 집합."""
    import src.main as mainmod

    paths = set()
    for route in mainmod.app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", None) or set()
        if path and "GET" in methods:
            paths.add(path)
    return paths


def _guide():
    from src.services.assistant import guide

    return guide


# ── 두 가지 대표 물음 ────────────────────────────────────────────
def test_missing_record_question_points_to_drafts():
    hits = _guide().search("배합 기록이 저장 안 된 것 같아요")
    assert hits, "안내를 하나도 못 찾았다"
    assert hits[0]["path"] == "/blend/drafts"
    assert "작성 중 배합" in hits[0]["screen"]


def test_material_lot_question_points_to_lot_history():
    hits = _guide().search("원재료 LOT 변동이 궁금해요")
    assert hits, "안내를 하나도 못 찾았다"
    assert hits[0]["path"] == "/lot-history"


def test_search_returns_at_most_three():
    assert len(_guide().search("배합")) <= 3


# ── 경로 정합 ────────────────────────────────────────────────────
def test_every_entry_path_is_a_real_page(app_paths):
    guide = _guide()
    for entry in guide.ENTRIES:
        assert entry["path"] in app_paths, f"{entry['id']} 경로 없음: {entry['path']}"
    for item in guide.screen_map():
        assert item["path"] in app_paths, f"화면 지도 경로 없음: {item['path']}"


def test_entry_paths_answer_a_get_request():
    """리다이렉트여도 좋다. 500·404 만 아니면 된다."""
    client = _client()
    guide = _guide()
    for entry in guide.ENTRIES:
        res = client.get(entry["path"], follow_redirects=False)
        assert res.status_code in (200, 303, 307), f"{entry['path']} -> {res.status_code}"


# ── 문구 규칙 ────────────────────────────────────────────────────
def _all_text(entry):
    parts = [entry["title"], entry["screen"], entry.get("notes") or ""]
    parts.extend(entry["steps"])
    parts.extend(entry["keywords"])
    return "\n".join(parts)


def test_entries_avoid_dash_and_banned_word():
    for entry in _guide().ENTRIES:
        text = _all_text(entry)
        assert "—" not in text, f"{entry['id']} 에 줄표가 있다"
        assert "잉크" not in text, f"{entry['id']} 에 금지어가 있다"
        assert "ink" not in text.lower(), f"{entry['id']} 에 금지어가 있다"


def test_entries_are_well_formed():
    guide = _guide()
    seen = set()
    assert len(guide.ENTRIES) >= 20
    for entry in guide.ENTRIES:
        assert entry["id"] not in seen, f"중복 id: {entry['id']}"
        seen.add(entry["id"])
        assert entry["title"].strip()
        assert entry["screen"].strip()
        assert entry["keywords"]
        assert 1 <= len(entry["steps"]) <= 6, f"{entry['id']} 단계 수"
        for step in entry["steps"]:
            assert step.strip()


# ── 화면 지도 ────────────────────────────────────────────────────
def test_screen_map_covers_sidebar_menus():
    from pathlib import Path

    html = Path("templates/_base_app.html").read_text(encoding="utf-8")
    nav = html.split('class="sidebar-nav"', 1)[1].split("</nav>", 1)[0]
    menu_paths = set(re.findall(r'<a href="(/[^"?]*)" class="side-link', nav))
    assert menu_paths, "사이드바 메뉴를 못 읽었다"

    mapped = {item["path"] for item in _guide().screen_map()}
    missing = menu_paths - mapped
    assert not missing, f"화면 지도에 빠진 메뉴: {sorted(missing)}"


def test_screen_map_block_is_compact():
    block = _guide().screen_map_block()
    assert "/blend/drafts" in block
    assert "/lot-history" in block
    assert "—" not in block
    assert len(block.splitlines()) == len(_guide().screen_map())


# ── 도구 계약 ────────────────────────────────────────────────────
def test_tool_never_errors_and_always_gives_screen_map():
    from src.services.assistant import tools

    tools.clear_cache()
    result = tools.get_usage_guide("zzz 아무 상관 없는 말")
    assert "error" not in result
    assert result["entries"] == []
    assert result["screen_map"]

    hit = tools.get_usage_guide("배합 기록이 저장 안 된 것 같아요")
    assert hit["entries"][0]["path"] == "/blend/drafts"
    assert set(hit["entries"][0]) == {"title", "screen", "path", "steps", "notes"}


def test_tool_is_registered():
    from src.services.assistant import tools

    assert "get_usage_guide" in tools.TOOL_NAMES
    assert tools.TOOL_LABELS["get_usage_guide"] == "사용법 안내"
    assert tools.call_tool("get_usage_guide", {"question": "어디서 하나요"})["screen_map"]


def test_system_prompt_carries_role_and_screen_map():
    from src.services.assistant import llm

    prompt = llm.build_system_prompt()
    assert "[역할]" in prompt
    assert "[화면 지도]" in prompt
    assert "[안내 답변 형식]" in prompt
    assert "get_usage_guide" in prompt
    assert "/blend/drafts" in prompt


def test_follow_ups_differ_by_topic_and_never_repeat_the_question():
    """방금 답한 질문을 다시 권하지 않는다(2026-09-10 현장 지적)."""
    from src.services.assistant import guide, llm

    asked = "수기 입력 승인은 어떻게 받나요?"
    ups = guide.follow_ups_for(asked, asked)
    assert asked not in ups
    assert len(ups) == 2

    saved = guide.follow_ups_for("기록이 저장 안 된 것 같아요", "기록이 저장 안 된 것 같아요")
    assert saved != ups                       # 주제가 다르면 추천도 다르다

    picked = llm.suggestions_for([{"name": "get_usage_guide"}], asked)
    assert asked not in picked
    assert len(picked) == 2


def test_every_entry_has_two_follow_ups_that_are_not_its_own_title():
    from src.services.assistant import guide

    for entry in guide.ENTRIES:
        ups = entry.get("follow_ups") or []
        assert len(ups) == 2, entry["id"]
        for text in ups:
            assert "—" not in text and "잉크" not in text, entry["id"]
            assert text != entry["title"], entry["id"]


def test_clear_howto_questions_are_answered_without_the_model():
    """정해진 안내는 모델을 부르지 않는다(2026-09-10 토큰 낭비 지적)."""
    from src.services.assistant import guide

    entry = guide.answerable("기록이 저장 안 된 것 같아요")
    assert entry is not None and entry["id"] == "missing-record"
    text = guide.render_answer(entry)
    assert "작성 중 배합" in text and "/blend/drafts" in text
    assert "1. " in text                      # 번호 붙은 절차
    assert "—" not in text and "잉크" not in text


def test_data_questions_never_take_the_fast_path():
    from src.services.assistant import guide

    for question in (
        "최근 배합 기록 보여줘", "오늘 배합 몇 건이야?", "지금 봐야 할 것 알려줘",
        "NPR-S 자재 LOT 언제 바뀌었어?", "이번 달 자재 사용량 알려줘",
        "APB17 점도 평균이 얼마야?", "점도 이상 목록 보여줘",
    ):
        assert guide.answerable(question) is None, question


def test_fast_path_returns_a_guide_answer_without_any_provider():
    from src.services.assistant import llm

    seen = []
    cfg = llm.ProviderConfig(provider=None, model="", gemini_key="", groq_key="")
    result = llm.run_answer("수기 입력 승인은 어떻게 받나요?", [], None, cfg,
                            lambda event, data: seen.append(event))
    assert result.provider == "guide"
    assert "수기 입력 승인 요청" in result.answer
    assert [t["name"] for t in result.tools_used] == ["get_usage_guide"]
    assert "token" in seen and "tool_call" in seen
    assert "수기 입력 승인은 어떻게 받나요?" not in result.suggestions
