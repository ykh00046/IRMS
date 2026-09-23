"""포털 근태허가원 수집 — 로그인·목록·본문 세 단계를 가짜 응답으로 검증.

**실제 포털을 부르지 않는다.** requests.Session 자리에 가짜를 끼워 넣어, 조사로
확인된 응답 모양(2026-09-22)만 재현한다:

  - 로그인 성공/실패(최종 URL 이 loginForm.do)
  - 유휴 만료 → 200 인데 본문이 로그인 폼 → 다시 로그인하고 이어간다
  - 목록 HTML(`<table id="listTable">`, onclick="getApprDetail('apprId',…)")
  - pageIndex 가 끝을 넘으면 마지막 쪽으로 붙들린다(같은 행 반복)
  - 같은 문서번호가 여러 행으로 나온다
  - 본문 HTML 에 HTML 이스케이프된 서식/값 JSON 두 덩이
  - 남의 부서 문서는 500 + '접근 권한이 없습니다'
"""

from __future__ import annotations

import html as html_mod
import json
from typing import Any

import pytest

from src.services import portal_approvals
from src.services.portal_approvals import client as client_mod
from src.services.portal_approvals import parser

BASE = "https://portal.example.test"

LOGIN_FORM_HTML = (
    '<html><body><form id="loginForm" action="/login.do">'
    '<input name="j_username"><input name="j_password"></form></body></html>'
)
PORTAL_MAIN_HTML = "<html><body><div id='portalMain'>메인</div></body></html>"
FORBIDDEN_HTML = "<html><body><h1>오류</h1><p>접근 권한이 없습니다.</p></body></html>"


# ── 가짜 응답·세션 ──────────────────────────────────────────────────────────


class _Response:
    def __init__(self, text: str = "", *, status_code: int = 200, url: str = BASE):
        self.text = text
        self.status_code = status_code
        self.url = url


class _FakeSession:
    """요청을 기록하고 준비된 응답을 돌려준다(경로별 핸들러)."""

    def __init__(self, handlers: dict[str, Any]):
        self.handlers = handlers
        self.calls: list[tuple[str, str, dict[str, Any]]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> _Response:
        self.calls.append((method.upper(), url, kwargs))
        for path, handler in self.handlers.items():
            if url.endswith(path) or path in url:
                return handler(self, kwargs) if callable(handler) else handler
        return _Response("", status_code=404, url=url)

    def get(self, url: str, **kwargs: Any) -> _Response:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> _Response:
        return self.request("POST", url, **kwargs)


def _list_html(rows: list[dict[str, str]]) -> str:
    """목록 HTML 한 쪽. 실제 열 순서를 그대로 흉내 낸다."""
    body = [
        '<table id="listTable"><thead><tr><th>No.</th><th>문서번호</th><th>유형</th>'
        "<th>분류</th><th>그룹사 여부</th><th>문서 제목</th><th>기안자</th>"
        "<th>기안부서</th><th>완료일</th></tr></thead><tbody>"
    ]
    for row in rows:
        body.append(
            f"<tr onclick=\"getApprDetail('{row['appr_id']}','listApprDeptOpen');\">"
            f"<td>{row.get('no', '1')}</td><td>{row['doc_no']}</td><td>근태</td>"
            f"<td>일반</td><td>N</td><td>{row['title']}</td>"
            f"<td>{row.get('drafter', '박용재')}</td><td>원료생산팀</td>"
            f"<td>{row.get('end_date', '2026-08-05')}</td></tr>"
        )
    body.append("</tbody></table>")
    return "<html><body>" + "".join(body) + "</body></html>"


def _body_html(
    *,
    doc_no: str = "20260805P227-0040",
    drafted_at: str = "2026-08-05",
    drafter: str = "박용재",
    emp_name: str = "김민솔",
    emp_id: str = "221023",
    period: str = "26년08월07일 13시부터 ~ 26년08월07일17시30분까지(0.5일간)",
    reason: str = "개인 사정",
) -> str:
    """본문 HTML — 서식/값 JSON 두 덩이가 HTML 이스케이프되어 들어 있다."""
    schema = [
        {"type": "text", "name": "text1", "title": "소속"},
        {"type": "text", "name": "text2", "title": "사번"},
        {"type": "text", "name": "text3", "title": "직위"},
        {"type": "text", "name": "text4", "title": "성명"},
        {"type": "text", "name": "text6", "title": "기간"},
        {"type": "text", "name": "text7", "title": "사유"},
    ]
    values = [
        {"type": "text", "name": "text1", "value": "원료생산팀"},
        {"type": "text", "name": "text2", "value": emp_id},
        {"type": "text", "name": "text3", "value": "사원"},
        {"type": "text", "name": "text4", "value": emp_name},
        {"type": "text", "name": "text6", "value": period},
        {"type": "text", "name": "text7", "value": reason},
    ]
    return (
        "<html><body>"
        f"<div class='doc-head'>문서번호 {doc_no} 기안일자 {drafted_at} 기안자 {drafter}</div>"
        f'<input type="hidden" id="formSchema" value="{html_mod.escape(json.dumps(schema, ensure_ascii=False))}">'
        f'<input type="hidden" id="formValue" value="{html_mod.escape(json.dumps(values, ensure_ascii=False))}">'
        "</body></html>"
    )


def _client(handlers: dict[str, Any], **over: Any) -> client_mod.PortalClient:
    session = _FakeSession(handlers)
    return client_mod.PortalClient(
        BASE, over.pop("username", "collector"), over.pop("password", "pw"),
        session=session, **over,
    )


def _ok_handlers(list_pages: dict[int, str], bodies: dict[str, Any]) -> dict[str, Any]:
    def login(_session, kwargs):
        data = kwargs.get("data") or {}
        if data.get("j_username") == "collector" and data.get("j_password") == "pw":
            return _Response(PORTAL_MAIN_HTML, url=f"{BASE}/portal/main/portalMain.do")
        return _Response(LOGIN_FORM_HTML, url=f"{BASE}/loginForm.do?error=1")

    def listing(_session, kwargs):
        index = int((kwargs.get("data") or {}).get("pageIndex", 1))
        # pageIndex 가 끝을 넘으면 포털이 마지막 쪽으로 붙들어 같은 행을 다시 준다.
        last = max(list_pages)
        return _Response(list_pages.get(index, list_pages[last]))

    def body(_session, kwargs):
        appr_id = (kwargs.get("params") or {}).get("apprId", "")
        prepared = bodies.get(appr_id)
        if prepared is None:
            return _Response(FORBIDDEN_HTML, status_code=500)
        return prepared if isinstance(prepared, _Response) else _Response(prepared)

    return {
        client_mod.LOGIN_PATH: login,
        client_mod.LOGIN_PAGE_PATH: _Response(LOGIN_FORM_HTML),
        client_mod.LIST_PATH: listing,
        client_mod.BODY_PATH: body,
    }


# ── 1. 순수 파서 ────────────────────────────────────────────────────────────


def test_list_rows_read_doc_no_title_and_appr_id():
    html = _list_html(
        [
            {
                "appr_id": "A1",
                "doc_no": "20260805P227-0040",
                "title": "근태허가원/원료생산팀/박용재/반차/26.08.07",
                "no": "2",
            }
        ]
    )
    rows = parser.parse_list_rows(html)
    assert len(rows) == 1, "머리글 행까지 세면 안 된다"
    row = rows[0]
    assert row["doc_no"] == "20260805P227-0040"
    assert row["appr_id"] == "A1", "본문 조회 키는 문서번호가 아니라 apprId 다"
    assert row["title"].endswith("26.08.07")
    assert row["drafter"] == "박용재"
    assert row["end_date"] == "2026-08-05"


def test_list_rows_survive_a_shifted_column():
    """포털이 열을 하나 끼워 넣어도 문서번호를 생김새로 다시 찾는다."""
    html = (
        '<table id="listTable"><tr onclick="getApprDetail(\'A9\',\'x\');">'
        "<td>1</td><td>추가된칸</td><td>20260805P227-0041</td>"
        "<td>근태허가원/원료생산팀/김민솔/연차/26.07.09</td></tr></table>"
    )
    rows = parser.parse_list_rows(html)
    assert rows and rows[0]["doc_no"] == "20260805P227-0041"


def test_body_json_blobs_join_on_name():
    body = parser.parse_body(_body_html())
    assert body["emp_name"] == "김민솔"
    assert body["emp_id"] == "221023"
    assert body["dept"] == "원료생산팀"
    assert body["period_start_date"] == "2026-08-07"
    assert body["period_start_hour"] == 13
    assert body["drafted_at"] == "2026-08-05"


def test_period_ignores_the_empty_second_row():
    """서식에는 늘 빈 '00년 00월 00일' 칸이 붙는다 — 0 값은 버린다."""
    period = parser.parse_period(
        "26년08월07일 13시부터 ~ 26년08월07일17시30분까지(0.5일간) "
        "00년 00월 00일 00시부터 ~ 00년 00월 00일 00시까지"
    )
    assert period["start_date"] == "2026-08-07"
    assert period["end_date"] == "2026-08-07"
    assert period["days"] == 0.5


def test_title_parsing_does_not_trust_position():
    """관측된 7가지 제목 형식 — 위치가 아니라 토큰의 생김새로 찾는다."""
    cases = {
        "근태허가원/원료생산팀/박용재/반차/26.08.07": ("박용재", "반차", "2026-08-07"),
        "원료생산팀/임현규/26.03.12/근태허가원/훈련": ("임현규", "훈련", "2026-03-12"),
        "원료생산팀/근태허가원/송보란/25.12.19~26.01.03/병가": (
            "송보란", "병가", "2025-12-19",
        ),
        "원료생산팀/근태허가원/박용재/2025.12.31/반차": ("박용재", "반차", "2025-12-31"),
    }
    for title, (name, kind, start) in cases.items():
        parsed = parser.parse_title(title)
        assert parsed["emp_name"] == name, title
        assert kind in (parsed["kind_raw"] or ""), title
        assert parsed["start_date"] == start, title


def test_a_kind_inside_the_date_token_is_still_read():
    """`원료생산팀/김**/2026.09.23(반차)` — 날짜 칸 안에 종류가 같이 적힌 형식.

    양식명으로 찾기 시작하면서(2026-09-23) 이 모양이 다수가 됐다. 날짜 칸을 통째로
    건너뛰면 그 문서들이 전부 '기타'로 접힌다(실연에서 확인).
    """
    parsed = parser.parse_title("원료생산팀/김철수/2026.09.08(연차)")
    assert parsed["emp_name"] == "김철수"
    assert parsed["start_date"] == "2026-09-08"
    assert parsed["kind_raw"] == "연차", "종류 원문에 날짜까지 넣지 않는다"
    assert "kind" not in parsed["unresolved"]

    # 날짜 밖에 종류가 따로 있으면 그쪽이 먼저다(기존 형식 불변).
    other = parser.parse_title("근태허가원/원료생산팀/박용재/반차/26.08.07")
    assert other["kind_raw"] == "반차"


def test_unreadable_title_is_reported_not_guessed():
    parsed = parser.parse_title("원료생산팀/강도윤/근태허가원")
    assert parsed["emp_name"] == "강도윤"
    assert parsed["kind_raw"] is None
    assert parsed["start_date"] is None
    assert set(parsed["unresolved"]) == {"kind", "start_date"}


def test_reason_boilerplate_is_stripped_before_kind_detection():
    """서식 안내문('구분(연차,반차,…)')을 두면 철야 건이 반반차로 잡힌다."""
    reason = parser.strip_boilerplate(
        "철야 근무 구분(연차,반차,반반차 등등) 사이에 근태 종류를 적으세요"
    )
    assert reason == "철야 근무"
    assert parser.detect_kind_token(reason) == "철야"


def test_half_comes_from_the_period_hour_not_a_guess():
    assert parser.half_from_hour("반차", 13) == "오후"
    assert parser.half_from_hour("반차", 9) == "오전"
    # 하루 단위 종류에는 시각을 적용하지 않는다.
    assert parser.half_from_hour("연차", 13) is None
    assert parser.half_from_hour("반차", None) is None


# ── 2. 클라이언트 ───────────────────────────────────────────────────────────


def test_login_rejects_a_wrong_password():
    portal = _client(_ok_handlers({1: _list_html([])}, {}), password="틀린비번")
    with pytest.raises(client_mod.PortalLoginFailed):
        portal.login()


def test_missing_credentials_never_hit_the_network():
    portal = _client({}, username="", password="")
    with pytest.raises(client_mod.PortalLoginFailed):
        portal.login()
    assert portal.session.calls == []


def test_dead_session_triggers_one_re_login():
    """유휴 만료는 200 + 로그인 폼이다 — 다시 로그인하고 같은 요청을 이어간다."""
    state = {"listing": 0}
    handlers = _ok_handlers({1: _list_html([])}, {})

    def listing(_session, _kwargs):
        state["listing"] += 1
        if state["listing"] == 1:
            return _Response(LOGIN_FORM_HTML)   # 세션이 끊겼다
        return _Response(_list_html([]))

    handlers[client_mod.LIST_PATH] = listing
    portal = _client(handlers)
    html = portal.fetch_list_page(
        start_date="2026.07.01", end_date="2026.09.01", page_index=1
    )
    assert 'id="listTable"' in html
    assert state["listing"] == 2, "같은 요청을 한 번만 다시 보내야 한다"
    logins = [c for c in portal.session.calls if c[1].endswith(client_mod.LOGIN_PATH)]
    assert len(logins) == 2


def test_forbidden_document_raises_permission_denied():
    portal = _client(_ok_handlers({1: _list_html([])}, {}))
    with pytest.raises(client_mod.PortalPermissionDenied):
        portal.fetch_body("남의부서")


def test_list_query_keeps_the_department_default():
    """조회를 회사 전체로 넓히지 않는다 — searchGroupId 를 보내지 않는다(사용자 결정)."""
    portal = _client(_ok_handlers({1: _list_html([])}, {}))
    portal.fetch_list_page(start_date="2026.07.01", end_date="2026.09.01", page_index=1)
    listing = [c for c in portal.session.calls if c[1].endswith(client_mod.LIST_PATH)][0]
    payload = listing[2]["data"]
    assert "searchGroupId" not in payload
    assert payload["searchUserName"] == ""
    assert payload["sortColumn"] == "apprEndDate"


def test_list_query_searches_by_form_name_not_by_title():
    """실측(2026-09-23, 270일 같은 부서): 문서제목=근태허가원 10건 / 양식명=근태허가원 328건.

    제목으로 찾으면 344건 중 334건을 놓친다 — 제목에 '근태허가원'이라는 말이 아예 없는
    평범한 휴가 결재가 대부분이기 때문이다. 양식명 칸으로 찾고 제목은 비운다.
    """
    portal = _client(_ok_handlers({1: _list_html([])}, {}))
    portal.fetch_list_page(start_date="2026.07.01", end_date="2026.09.01", page_index=1)
    payload = [
        c for c in portal.session.calls if c[1].endswith(client_mod.LIST_PATH)
    ][0][2]["data"]
    assert payload["searchApprTitle"] == "", "제목으로 찾으면 대부분을 놓친다"
    assert payload["searchFormName"] == client_mod.DEFAULT_FORM_NAME == "근태허가원"

    # 양식명은 부분일치라 '근태' 로 넓힐 수 있다(운영자 선택).
    portal.fetch_list_page(
        start_date="2026.07.01", end_date="2026.09.01", page_index=1, form_name="근태"
    )
    wider = [
        c for c in portal.session.calls if c[1].endswith(client_mod.LIST_PATH)
    ][-1][2]["data"]
    assert wider["searchFormName"] == "근태"


# ── 3. 한 회차 ──────────────────────────────────────────────────────────────


def _page_rows(*specs: tuple[str, str, str]) -> list[dict[str, str]]:
    return [
        {"appr_id": appr_id, "doc_no": doc_no, "title": title}
        for appr_id, doc_no, title in specs
    ]


# 목록 열 순서 그대로 쓰는 헬퍼들은 _list_html 이 기본값을 채운다(완료일 2026-08-05).


def test_collect_reads_bodies_and_dedupes_by_doc_no():
    rows = _page_rows(
        ("A1", "20260805P227-0040", "근태허가원/원료생산팀/박용재/반차/26.08.07"),
        # 같은 문서가 두 행으로 나오는 일이 있다.
        ("A2", "20260805P227-0040", "근태허가원/원료생산팀/박용재/반차/26.08.07"),
        ("A3", "20260709P227-0011", "근태허가원/원료생산팀/김민솔/연차/26.07.09"),
    )
    bodies = {
        "A1": _body_html(emp_name="박용재", emp_id="171013"),
        "A3": _body_html(
            doc_no="20260709P227-0011",
            emp_name="김민솔",
            emp_id="221023",
            period="26년07월09일 09시부터 ~ 26년07월09일18시까지(1일간)",
        ),
    }
    portal = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    result = portal_approvals.collect(client=portal, window_days=60)

    assert result["rows"] == 2, "문서번호로 합쳐야 한다"
    by_doc = {item["doc_no"]: item for item in result["items"]}
    assert set(by_doc) == {"20260805P227-0040", "20260709P227-0011"}
    half_day = by_doc["20260805P227-0040"]
    assert half_day["emp_name"] == "박용재"
    assert half_day["emp_id"] == "171013"
    assert half_day["kind"] == "반차"
    assert half_day["half"] == "오후", "13시 시작이면 오후다(추측이 아니라 문서의 사실)"
    assert half_day["start_date"] == "2026-08-07"
    assert half_day["status"] == "완료"
    assert half_day["drafted_at"] == "2026-08-05"
    assert half_day["doc_hash"].startswith("sha256:")
    assert "unresolved" not in half_day, "진단 키는 저장 항목에 남지 않는다"


def test_collect_skips_a_forbidden_document_without_guessing():
    rows = _page_rows(
        ("A1", "20260805P227-0040", "근태허가원/원료생산팀/박용재/반차/26.08.07"),
        ("AX", "20260805P999-0001", "근태허가원/다른팀/홍길동/연차/26.08.08"),
    )
    portal = _client(
        _ok_handlers({1: _list_html(rows)}, {"A1": _body_html(emp_name="박용재")})
    )
    result = portal_approvals.collect(client=portal, window_days=60)

    assert result["forbidden"] == 1
    assert [item["doc_no"] for item in result["items"]] == ["20260805P227-0040"]
    # 제목에 이름·종류가 있어도 본문을 못 읽었으면 값을 지어내지 않는다.
    assert "20260805P999-0001" not in {item["doc_no"] for item in result["items"]}


def _full_page(prefix: str) -> str:
    """가득 찬 한 쪽(50행) — 다음 쪽을 더 봐야 하는 상태를 만든다."""
    return _list_html(
        _page_rows(
            *[
                (
                    f"{prefix}{index}",
                    f"202608{index % 28 + 1:02d}{prefix}227-{index:04d}",
                    f"근태허가원/원료생산팀/김민솔/연차/26.08.{index % 28 + 1:02d}",
                )
                for index in range(client_mod.PAGE_SIZE)
            ]
        )
    )


def test_collect_stops_when_paging_clamps_to_the_last_page():
    portal = _client(_ok_handlers({1: _full_page("A"), 2: _full_page("B")}, {}))
    result = portal_approvals.collect(client=portal, window_days=60)
    # 3쪽을 요청하면 포털이 2쪽으로 붙들어 같은 행이 다시 온다 → 거기서 멈춘다.
    assert result["pages"] == 3
    assert result["rows"] == client_mod.PAGE_SIZE * 2


def test_collect_stops_on_a_short_page_without_asking_again():
    portal = _client(
        _ok_handlers(
            {
                1: _full_page("A"),
                2: _list_html(
                    _page_rows(
                        ("B1", "20260810P227-0099", "근태허가원/원료생산팀/박용재/연차/26.08.10")
                    )
                ),
            },
            {},
        )
    )
    result = portal_approvals.collect(client=portal, window_days=60)
    assert result["pages"] == 2
    assert result["rows"] == client_mod.PAGE_SIZE + 1


def test_collect_reports_unreadable_titles_instead_of_storing_them():
    rows = _page_rows(("A1", "20260805P227-0040", "원료생산팀/강도윤/근태허가원"))
    portal = _client(_ok_handlers({1: _list_html(rows)}, {"A1": _body_html(
        emp_name="강도윤", period="00년 00월 00일 00시부터 ~ 00년 00월 00일 00시까지",
        reason="",
    )}))
    result = portal_approvals.collect(client=portal, window_days=60)
    assert result["items"] == [], "시작일을 모르는 문서를 지어내 저장하면 안 된다"
    assert result["incomplete"] == [
        {"doc_no": "20260805P227-0040", "missing": ["start_date"]}
    ]
    assert result["unresolved"][0]["fields"] == ["kind", "start_date"]


def test_collect_window_uses_the_completion_date_filter():
    import datetime as dt

    portal = _client(_ok_handlers({1: _list_html([])}, {}))
    portal_approvals.collect(
        client=portal, window_days=30, today=dt.date(2026, 9, 22)
    )
    listing = [c for c in portal.session.calls if c[1].endswith(client_mod.LIST_PATH)][0]
    payload = listing[2]["data"]
    assert payload["searchStartDate"] == "2026.08.23"
    assert payload["searchEndDate"] == "2026.09.23"


def _body_fetch_ids(portal) -> list[str]:
    """이 회차에 실제로 연 문서(apprId) 목록."""
    return [
        (call[2].get("params") or {}).get("apprId", "")
        for call in portal.session.calls
        if call[1].endswith(client_mod.BODY_PATH)
    ]


def test_already_stored_rows_are_not_fetched_again():
    """목록 행이 그대로면 본문을 열지 않는다 — 회차 비용은 본문 왕복이 전부다."""
    rows = _page_rows(
        ("A1", "20260805P227-0040", "근태허가원/원료생산팀/박용재/반차/26.08.07"),
        ("A2", "20260806P227-0041", "근태허가원/원료생산팀/김민솔/연차/26.08.08"),
    )
    bodies = {"A1": _body_html(emp_name="박용재"), "A2": _body_html(emp_name="김민솔")}

    first = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    seeded = portal_approvals.collect(client=first, window_days=60)
    assert seeded["fetched"] == 2
    assert seeded["skipped_unchanged"] == 0
    known = {item["doc_no"]: item["doc_hash"] for item in seeded["items"]}

    again = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    second = portal_approvals.collect(client=again, window_days=60, known=known)
    assert second["skipped_unchanged"] == 2
    assert second["fetched"] == 0
    assert second["items"] == []
    assert _body_fetch_ids(again) == [], "저장된 문서를 다시 열었다"


def test_a_changed_completion_date_forces_a_fresh_fetch():
    """결재가 다시 완료되면 완료일이 바뀐다 — 그 문서만 다시 연다."""
    base = [
        {
            "appr_id": "A1",
            "doc_no": "20260805P227-0040",
            "title": "근태허가원/원료생산팀/박용재/반차/26.08.07",
        },
        {
            "appr_id": "A2",
            "doc_no": "20260806P227-0041",
            "title": "근태허가원/원료생산팀/김민솔/연차/26.08.08",
        },
    ]
    bodies = {"A1": _body_html(emp_name="박용재"), "A2": _body_html(emp_name="김민솔")}
    first = _client(_ok_handlers({1: _list_html(base)}, bodies))
    seeded = portal_approvals.collect(client=first, window_days=60)
    known = {item["doc_no"]: item["doc_hash"] for item in seeded["items"]}

    changed = [dict(base[0], end_date="2026-08-20"), base[1]]
    again = _client(_ok_handlers({1: _list_html(changed)}, bodies))
    second = portal_approvals.collect(client=again, window_days=60, known=known)
    assert second["fetched"] == 1
    assert second["skipped_unchanged"] == 1
    assert _body_fetch_ids(again) == ["A1"]
    assert [item["doc_no"] for item in second["items"]] == ["20260805P227-0040"]


def test_the_run_cap_reports_how_many_are_left():
    """상한에 걸리면 남은 수를 알린다 — 다시 부르면 이어서 받는다."""
    rows = _page_rows(
        *[
            (
                f"A{index}",
                f"2026080{index}P227-000{index}",
                f"근태허가원/원료생산팀/김민솔/연차/26.08.0{index}",
            )
            for index in range(1, 6)
        ]
    )
    bodies = {f"A{index}": _body_html(emp_name="김민솔") for index in range(1, 6)}

    portal = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    first = portal_approvals.collect(client=portal, window_days=60, max_bodies=2)
    assert first["fetched"] == 2
    assert first["remaining"] == 3
    assert len(first["items"]) == 2
    # 최신 문서부터 연다 — 회차가 나뉘어도 오늘 것이 먼저 들어온다.
    assert _body_fetch_ids(portal) == ["A5", "A4"]

    known = {item["doc_no"]: item["doc_hash"] for item in first["items"]}
    rest = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    second = portal_approvals.collect(
        client=rest, window_days=60, known=known, max_bodies=2
    )
    assert second["fetched"] == 2
    assert second["skipped_unchanged"] == 2
    assert second["remaining"] == 1


def test_unreadable_documents_do_not_eat_the_budget_every_run():
    """못 읽은 문서(권한 없음·값 부족)를 기억하지 않으면 밀린 문서가 영영 안 들어온다.

    2026-09-23 브라우저 실연에서 실제로 막혔다: 상한 2인데 최신 두 건이 매번
    '권한 없음'과 '값 부족'이라, 몇 번을 눌러도 남은 수가 줄지 않았다.
    """
    rows = _page_rows(
        # 최신순으로 먼저 걸리는 두 건이 저장 불가다.
        ("AX", "20260909P227-0099", "근태허가원/다른팀/홍길동/연차/26.09.09"),
        ("A5", "20260908P227-0098", "원료생산팀/강도윤/근태허가원"),
        ("A1", "20260907P227-0097", "근태허가원/원료생산팀/박용재/반차/26.09.07"),
    )
    bodies = {
        "A5": _body_html(emp_name="강도윤", period="", reason=""),
        "A1": _body_html(emp_name="박용재"),
    }

    first = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    one = portal_approvals.collect(client=first, window_days=60, max_bodies=2)
    assert one["fetched"] == 2
    assert one["items"] == [], "이번 회차에 저장할 수 있는 문서는 없다"
    assert one["remaining"] == 1
    assert set(one["skip_memo"]) == {"20260909P227-0099", "20260908P227-0098"}

    second = _client(_ok_handlers({1: _list_html(rows)}, bodies))
    two = portal_approvals.collect(
        client=second,
        window_days=60,
        max_bodies=2,
        skipped_before=one["skip_memo"],
    )
    assert two["skipped_unreadable"] == 2
    assert two["fetched"] == 1, "이번에는 남아 있던 문서를 연다"
    assert two["remaining"] == 0
    assert [item["doc_no"] for item in two["items"]] == ["20260907P227-0097"]
    # 건너뛰어도 건수는 그대로 알린다 — 사라진 것처럼 보이면 안 된다.
    assert two["forbidden"] == 1
    assert [row["doc_no"] for row in two["incomplete"]] == ["20260908P227-0098"]


def test_a_changed_row_reopens_a_previously_unreadable_document():
    """완료일이 바뀌면 기억을 무시하고 다시 연다 — 결재가 다시 돌았을 수 있다."""
    base = [
        {
            "appr_id": "A5",
            "doc_no": "20260908P227-0098",
            "title": "원료생산팀/강도윤/근태허가원",
        }
    ]
    bodies = {"A5": _body_html(emp_name="강도윤", period="", reason="")}
    first = _client(_ok_handlers({1: _list_html(base)}, bodies))
    one = portal_approvals.collect(client=first, window_days=60)

    changed = [dict(base[0], end_date="2026-09-30")]
    again = _client(_ok_handlers({1: _list_html(changed)}, bodies))
    two = portal_approvals.collect(
        client=again, window_days=60, skipped_before=one["skip_memo"]
    )
    assert two["fetched"] == 1, "행이 바뀌었으면 기억을 믿지 않는다"
    assert two["skipped_unreadable"] == 0


def test_a_period_without_a_clock_time_stores_half_unresolved():
    """기간에 시각이 없으면 오전/오후를 추측하지 않는다(2026-09-23 실제 문서 2건)."""
    rows = _page_rows(
        ("A1", "20260921P227-0050", "원료생산팀/26.09.21/정민수/반반차")
    )
    portal = _client(
        _ok_handlers(
            {1: _list_html(rows)},
            {"A1": _body_html(emp_name="정민수", period="26년09월21일부터 ~ 26년09월21일까지")},
        )
    )
    result = portal_approvals.collect(client=portal, window_days=60)
    assert len(result["items"]) == 1, "시각이 없다고 문서를 버리지는 않는다"
    item = result["items"][0]
    assert item["kind"] == "반반차"
    assert item["start_date"] == "2026-09-21"
    assert item["half"] is None, "시각이 없으면 오전/오후를 지어내지 않는다"
    assert result["unresolved"][0]["fields"] == ["half"]


def test_header_fields_skip_the_instruction_text_and_keep_the_drafter_department():
    r"""머리글은 안내문이 아니라 '문서번호' 뒤에서 읽는다(2026-09-23 실제 문서 확인).

    본문 앞쪽 안내문에 "문서 제목 작성시 ➡ …" 이 먼저 나와, 글 전체에서 라벨을 찾으면
    문서제목이 '작성시' 가 됐다. 기안자는 '박용재/ 원료생산팀' 처럼 슬래시 뒤 부서까지가
    한 값이라 \S+ 로 끊으면 '박용재/' 만 남는다. 둘 다 저장값은 아니지만 진단을 흐린다.
    """
    from src.services.portal_approvals import parser as parser_mod

    html = (
        "<div>문서 제목 작성시 ➡ 부서 / 성명 / 사용일자 / 구분(연차,반차,반반차 등등)</div>"
        "<div>문서번호 20260805P227-0040   기안일자 2026-08-05   "
        "기안자 박용재/ 원료생산팀   문서 제목 근태허가원/원료생산팀/박용재/반차/26.08.07</div>"
    )
    found = parser_mod.parse_header_fields(html)
    assert found["문서번호"] == "20260805P227-0040"
    assert found["기안일자"] == "2026-08-05"
    assert found["기안자"] == "박용재/ 원료생산팀", "슬래시 뒤 부서까지 한 값이다"
    assert found["문서제목"].startswith("근태허가원/"), "안내문의 '작성시' 가 아니다"
