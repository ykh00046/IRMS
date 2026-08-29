"""근태 화면(/attendance)의 구조 계약 — 2026-08-14 재설계.

서버 계약은 test_attendance_admin_api.py 가 지킨다. 여기서는 **화면**이 그 계약을
실제로 쓰고 있는지, 그리고 사람이 뜻을 알 수 있는 형태로 그리는지를 지킨다.

사고 배경: 책임자가 트레이 알림에서 "구분 0 / 내용 근태 이상 / 추가 내용 빈칸" 을
받고 무슨 뜻인지 알 수 없었다(2026-08-14). 판정 사유 원문은 서버가 이미 주고
있었는데 화면이 그것을 툴팁에만 숨겨 두었기 때문이다. 그래서:

  ① anomaly.py 가 만들 수 있는 사유 문자열 전부에 화면 해설이 있어야 한다
     (서버에 새 사유가 생기면 여기서 먼저 걸린다).
  ② 사유는 행 아래 서브라인으로 그려야 한다(툴팁 의존 금지 — 터치 화면).
  ③ 캐시버스팅 ?v= 는 같은 파일을 참조하는 모든 템플릿에서 일치해야 한다
     (한 화면만 올리면 다른 화면이 옛 JS 를 계속 쓴다).
"""

from __future__ import annotations

import collections
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = BASE_DIR / "templates"
STATIC = BASE_DIR / "static"

ATTENDANCE_PAGE = (TEMPLATES / "attendance.html").read_text(encoding="utf-8")
LOGIN_PAGE = (TEMPLATES / "attendance_login.html").read_text(encoding="utf-8")
ATTENDANCE_JS = (STATIC / "js" / "attendance.js").read_text(encoding="utf-8")
LOGIN_JS = (STATIC / "js" / "attendance_login.js").read_text(encoding="utf-8")
SESSION_JS = (STATIC / "js" / "attendance_session.js").read_text(encoding="utf-8")
ATTENDANCE_CSS = (STATIC / "css" / "attendance.css").read_text(encoding="utf-8")


# ── ① 사유 해설 사전이 서버 사유를 전부 덮는가 ──────────────────────────────
def _server_issue_strings() -> set[str]:
    """anomaly.py 가 _append_issue 로 붙이는 리터럴 사유를 걷어낸다.

    f-string 사유("근태코드 확인: {code}")는 값이 런타임에 정해지므로 제외한다 —
    그 경우는 화면이 원문 그대로 보여주는 것이 계약이다.
    """
    source = (
        BASE_DIR / "src" / "services" / "attendance_excel" / "anomaly.py"
    ).read_text(encoding="utf-8")
    literals = set(re.findall(r'_append_issue\(\s*issues,\s*"([^"{}]+)"\s*\)', source))
    # 휴가 코드 + 공제시간 조합은 return 문에서 직접 만든다.
    literals.update(re.findall(r'return \["([^"{}]+)"\]', source))
    return literals


def test_the_scan_actually_finds_server_issue_strings():
    """전제가 무너지면(정규식이 아무것도 못 잡으면) 아래 계약이 빈손으로 통과한다."""
    issues = _server_issue_strings()
    assert "출근 누락" in issues and "지각 미처리" in issues, issues
    assert len(issues) >= 8, issues


def test_every_server_issue_string_has_a_korean_explanation():
    missing = [
        issue for issue in _server_issue_strings() if f'"{issue}":' not in ATTENDANCE_JS
    ]
    assert not missing, (
        "판정 사유에 화면 해설이 없습니다(attendance.js 의 ISSUE_EXPLANATIONS 에 추가하세요): "
        + ", ".join(sorted(missing))
    )


def test_unknown_issue_falls_back_to_the_raw_text():
    """사전에 없는 사유는 해설 없이 원문만 — 삼켜서 사라지면 안 된다."""
    assert 'ISSUE_EXPLANATIONS[text] || ""' in ATTENDANCE_JS
    assert "att-issue-label" in ATTENDANCE_JS, "원문 사유를 그리는 자리가 없다"


# ── ② 사유를 행에 직접 그리는가(툴팁 의존 금지) ─────────────────────────────
def test_issue_reason_is_rendered_as_a_row_subline():
    assert "att-issue-detail-row" in ATTENDANCE_JS
    assert "att-issue-why" in ATTENDANCE_JS
    assert ".att-issue-detail-row" in ATTENDANCE_CSS
    assert ".att-issue-why" in ATTENDANCE_CSS


def test_pending_card_counts_unprocessed_issues_and_scrolls():
    assert 'id="att-pending-card"' in ATTENDANCE_PAGE
    assert 'id="att-pending-count"' in ATTENDANCE_PAGE
    # 뜻이 다른 두 숫자를 라벨로 갈라 둔다.
    assert "ERP 반영분" in ATTENDANCE_PAGE
    assert "ERP 입력 대기" in ATTENDANCE_PAGE
    assert "scrollToFirstIssue" in ATTENDANCE_JS
    assert "att-row-issue" in ATTENDANCE_JS


def test_admin_anomaly_panel_uses_the_new_api():
    assert 'id="att-anomaly-panel"' in ATTENDANCE_PAGE
    assert "/api/attendance/admin/anomalies" in ATTENDANCE_JS
    assert "detail_total" in ATTENDANCE_JS, "N명 · M건 표기가 서버 값을 안 쓴다"
    assert "selectEmployeeFromPanel" in ATTENDANCE_JS, "행 클릭 → 직원 조회 전환이 없다"
    # 패널은 책임자에게만 렌더된다.
    assert "{% if admin_mode %}" in ATTENDANCE_PAGE


# ── ③ 월초 막다른 골목 ──────────────────────────────────────────────────────
def test_month_file_missing_offers_a_way_out():
    assert 'id="att-month-missing"' in ATTENDANCE_PAGE
    assert 'id="att-month-missing-btn"' in ATTENDANCE_PAGE
    assert "showMonthMissing" in ATTENDANCE_JS
    # 404 detail 이 구조체로 온다 — 문자열만 읽던 예전 방식이면 "[object Object]" 가 된다.
    assert "available_months" in ATTENDANCE_JS
    assert "error.detail" in ATTENDANCE_JS


def test_month_nav_survives_a_month_that_has_no_file():
    """목록에 없는 달에 서 있어도 이전/다음이 죽지 않아야 한다."""
    assert "nearestMonth" in ATTENDANCE_JS
    assert "monthPrev.disabled = !nearestMonth(-1)" in ATTENDANCE_JS


# ── ④ 책임자는 3분 자동 로그아웃에서 제외 ───────────────────────────────────
def test_session_guard_skips_admin_mode():
    assert "/api/attendance/session" in SESSION_JS
    assert "admin_mode" in SESSION_JS
    # attach() 가 무조건 걸리면 안 된다 — 관리자 판별 뒤에만 붙는다.
    assert "if (await isAdminSession()) return;" in SESSION_JS
    # 직원 동작은 그대로: 3분 타이머와 sendBeacon 로그아웃이 남아 있어야 한다.
    assert "VISIBLE_TIMEOUT_MS = 3 * 60 * 1000" in SESSION_JS
    assert "navigator.sendBeacon" in SESSION_JS


# ── ④-1 앱 안 화면 이동은 탭 닫힘이 아니다 ─────────────────────────────────
CHANGE_PW_JS = (STATIC / "js" / "attendance_change_password.js").read_text(encoding="utf-8")


def test_in_app_navigation_does_not_fire_the_logout_beacon():
    """근태 → 비밀번호 변경 → 근태 로 옮길 때 pagehide beacon 이 세션을 지우면 안 된다.

    종전엔 임시 비밀번호로 로그인한 직원이 변경 화면에서 [변경하기]를 누르는 순간
    401(세션 만료)을 받았다 — 이동 자체가 로그아웃이었다(2026-08-28).
    """
    assert "window.IRMS.attendanceSession = { allowNavigation }" in SESSION_JS
    assert "if (inAppNavigation) return;" in SESSION_JS
    # beacon 가드는 firedLogout 검사 바로 뒤, beacon 발사 앞에 있어야 한다.
    on_bye = SESSION_JS[SESSION_JS.index("function onBye()"):]
    assert on_bye.index("if (inAppNavigation) return;") < on_bye.index("navigator.sendBeacon")
    # 앱 안 이동 세 곳 모두 이동 직전에 가드를 푼다.
    call = "window.IRMS?.attendanceSession?.allowNavigation?.();"
    att_go = ATTENDANCE_JS.index('window.location.assign("/attendance/change-password")')
    assert ATTENDANCE_JS.rfind(call, 0, att_go) > att_go - 200
    assert CHANGE_PW_JS.count(call) == 2
    for target in ('window.location.assign("/attendance")',):
        idx = 0
        while True:
            idx = CHANGE_PW_JS.find(target, idx)
            if idx < 0:
                break
            assert CHANGE_PW_JS.rfind(call, 0, idx) > idx - 200, "이동 직전에 allowNavigation 이 없다"
            idx += len(target)
    # 템플릿의 일반 링크(배너 <a href="/attendance/change-password">)도 앱 안 이동이다.
    assert 'href="/attendance/change-password"' in ATTENDANCE_PAGE
    assert 'closest?.("a[href]")' in SESSION_JS
    assert "url.origin !== window.location.origin" in SESSION_JS
    # 명시적 로그아웃 뒤의 이동은 가드를 풀지 않는다(세션은 이미 끝났다).
    logout_go = CHANGE_PW_JS.index('window.location.assign("/attendance/login")')
    assert CHANGE_PW_JS.rfind(call, 0, logout_go) < logout_go - 200


# ── ⑤ 로그인 화면 안내 ──────────────────────────────────────────────────────
def test_login_screen_explains_idle_logout_and_lockout():
    assert 'id="att-login-notice"' in LOGIN_PAGE
    assert "자동 로그아웃되었습니다" in LOGIN_JS
    assert "남은 시도" in LOGIN_JS
    assert "locked_until" in LOGIN_JS
    assert "잠금 해제를 요청" in LOGIN_JS
    # detail 구조체를 통째로 넘겨야 remaining/locked_until 을 읽을 수 있다.
    assert "payload?.detail ?? \"\"" in LOGIN_JS


# ── ⑥ 화면 정리 ─────────────────────────────────────────────────────────────
def test_header_warning_banner_is_a_page_wide_banner():
    """헤더 안 flex 항목이면 버튼들 사이에 눌린다 — </header> 밖에 있어야 한다."""
    banner = ATTENDANCE_PAGE.index('id="att-header-banner"')
    header_end = ATTENDANCE_PAGE.index("</header>")
    assert banner > header_end
    assert "att-page-banner" in ATTENDANCE_PAGE
    assert ".att-page-banner" in ATTENDANCE_CSS


def test_change_password_button_is_hidden_without_an_employee_session():
    """책임자 전용 보기에서는 바꿀 대상이 없다 — 버튼 자체를 렌더하지 않는다."""
    index = ATTENDANCE_PAGE.index('id="att-change-pw-btn"')
    preceding = ATTENDANCE_PAGE[:index]
    assert preceding.rstrip().endswith("{% if emp_id %}") or (
        "{% if emp_id %}" in preceding.rsplit("{% endif %}", 1)[-1]
    )


def test_employee_filter_narrows_the_select_without_replacing_it():
    assert 'id="att-emp-filter"' in ATTENDANCE_PAGE
    assert 'id="att-emp-select"' in ATTENDANCE_PAGE, "기존 select 배선을 지우면 안 된다"
    assert "renderEmpOptions" in ATTENDANCE_JS
    assert "employeeMatchesFilter" in ATTENDANCE_JS


def test_reset_banner_dismiss_lives_in_one_storage():
    """공용 PC 대비 sessionStorage 가 정본 — localStorage 잔재는 걷어낸다."""
    assert "sessionStorage.setItem(storageKey" in ATTENDANCE_JS
    assert "localStorage.removeItem(key)" in ATTENDANCE_JS


def test_existing_features_are_not_removed():
    """기존 기능 유지 계약 — 월 내비·요약 카드·기준 그림·범례·바로가기."""
    for marker in (
        'id="att-month-prev"',
        'id="att-month-next"',
        'class="att-rules"',
        'class="att-legend"',
        'href="/viscosity"',
        'href="/blend"',
        'id="att-emp-direct"',
        "올해 지각 누적",
        "연차 사용 합계",
        "평일 근무시간",
        "휴일 근무시간",
    ):
        assert marker in ATTENDANCE_PAGE, f"기존 요소가 사라졌습니다: {marker}"


# ── ⑦ 캐시버스팅은 '버전 일치' 계약 ─────────────────────────────────────────
_REF_RE = re.compile(r"/static/([A-Za-z0-9_./-]+\.(?:js|css))\?v=([A-Za-z0-9_.-]+)")


def test_attendance_asset_versions_agree_across_templates():
    """같은 파일을 참조하는 템플릿들의 ?v= 가 어긋나면 한쪽 화면이 옛 파일을 쓴다."""
    versions: dict[str, set[str]] = collections.defaultdict(set)
    where: dict[str, set[str]] = collections.defaultdict(set)
    for template in TEMPLATES.rglob("*.html"):
        for path, version in _REF_RE.findall(template.read_text(encoding="utf-8")):
            if "attendance" not in path:
                continue
            versions[path].add(version)
            where[path].add(f"{template.name}:{version}")

    mismatched = {path for path, vals in versions.items() if len(vals) > 1}
    assert not mismatched, "근태 정적 파일의 ?v= 가 템플릿마다 다릅니다: " + "; ".join(
        f"{path} → {sorted(where[path])}" for path in sorted(mismatched)
    )
    assert "js/attendance.js" in versions, "참조를 하나도 못 읽었다(테스트 전제 붕괴)"


def test_edited_assets_got_a_fresh_version_this_round():
    """이번에 고친 파일은 옛 버전 토큰으로 남아 있으면 안 된다."""
    for path, stale in (
        ("js/attendance.js", "20260808a"),
        ("css/attendance.css", "20260808a"),
        ("js/attendance_session.js", "20260814a"),
        ("js/attendance_login.js", "20260624a"),
        ("js/attendance_change_password.js", "20260624a"),
        ("js/attendance_change_password.js", "20260828a"),
    ):
        for template in TEMPLATES.rglob("*.html"):
            text = template.read_text(encoding="utf-8")
            assert f"/static/{path}?v={stale}" not in text, (
                f"{template.name} 이 {path} 를 옛 버전({stale})으로 참조합니다"
            )


# ── ⑧ 신입 첫 로그인 동선(2026-08-28) ───────────────────────────────────────
# 배경: 신입이 처음 근태를 보려면 ① 책임자가 임시 비밀번호를 발급하고 ② 그것을
# 전달받아 ③ 로그인한 뒤 ④ 새 비밀번호를 정해야 하는데, 화면 어디에도 그 순서가
# 적혀 있지 않았다. 임시 비밀번호는 window.prompt 로 한 번 스쳐 지나갔고(차단되면
# 통째로 소실), 로그인 후에는 배너만 뜰 뿐 변경 화면으로 데려가지 않았다.
ADMIN_USERS_PAGE = (TEMPLATES / "admin_users.html").read_text(encoding="utf-8")
ADMIN_USERS_JS = (STATIC / "js" / "admin_users.js").read_text(encoding="utf-8")
ADMIN_USERS_CSS = (STATIC / "css" / "admin_users.css").read_text(encoding="utf-8")
CHANGE_PW_PAGE = (TEMPLATES / "attendance_change_password.html").read_text(
    encoding="utf-8"
)
ACCESS_CSS = (STATIC / "css" / "access.css").read_text(encoding="utf-8")


def test_manager_panel_spells_out_the_issue_sequence():
    assert "신입 계정 발급 · 비밀번호 초기화" in ADMIN_USERS_PAGE
    assert "att-issue-steps" in ADMIN_USERS_PAGE
    assert ".att-issue-steps" in ADMIN_USERS_CSS
    for step in ("근태 엑셀", "임시 비밀번호 발급", "홈 › 근태 확인", "책임자 로그아웃"):
        assert step in ADMIN_USERS_PAGE, f"발급 순서 안내에 '{step}' 이 없습니다"
    assert "신입 사번 입력" in ADMIN_USERS_PAGE


def test_issued_password_is_shown_in_a_card_not_a_prompt():
    """prompt 가 차단되면 발급된 비밀번호가 통째로 사라진다 — 화면 안 카드로 옮겼다."""
    assert 'id="att-issue-result"' in ADMIN_USERS_PAGE
    # 카드는 hidden 속성으로만 여닫는다(display 토글은 CSS 와 싸운다).
    assert 'id="att-issue-result" class="att-issue-result" hidden' in ADMIN_USERS_PAGE
    assert "attIssueResult.hidden = false" in ADMIN_USERS_JS
    assert "employee_label" in ADMIN_USERS_JS, "누구 것인지 못 박는 라벨을 안 쓴다"
    assert "navigator.clipboard.writeText" in ADMIN_USERS_JS
    assert "selectNodeContents" in ADMIN_USERS_JS, "복사 실패 시 선택 폴백이 없다"
    assert "다시 볼 수 없습니다" in ADMIN_USERS_PAGE
    # 전달 문구(책임자가 그대로 읽어 주는 한 줄).
    assert "홈 › 근태 확인 → 사번 ${empId} + 임시 비밀번호 ${password}" in ADMIN_USERS_JS


def test_first_issuance_does_not_nag_but_overwrite_does():
    """최초 발급은 지울 것이 없다 — 확인창은 남의 비밀번호를 덮어쓸 때만."""
    issue_handler = ADMIN_USERS_JS[ADMIN_USERS_JS.index("attIssueBtn?.addEventListener"):]
    issue_handler = issue_handler[: issue_handler.index("attNewEmp?.addEventListener")]
    assert "attKnownEmpIds.has(empId) && !window.confirm" in issue_handler
    # 표의 행은 전부 기존 계정이므로 확인창을 유지한다.
    assert "지금 쓰고 있는 비밀번호는 사라집니다" in ADMIN_USERS_JS
    # prompt 로 비밀번호를 흘리던 경로는 카드가 없는 구 템플릿 폴백 하나만 남는다.
    assert ADMIN_USERS_JS.count("window.prompt(") == 1


def test_login_screen_guides_the_very_first_login():
    assert 'class="login-help"' in LOGIN_PAGE
    assert "처음 로그인하세요?" in LOGIN_PAGE
    assert ".login-help" in ACCESS_CSS
    assert "login-demo" not in LOGIN_PAGE, "옛 한 줄 안내가 남아 있다"
    # 자격 실패는 계정 존재 여부를 흘리지 않는다 — 그래서 안내는 조건 없이 뜬다.
    assert "처음 로그인이라면 책임자에게 임시 비밀번호 발급을 요청하세요." in LOGIN_JS
    assert "if (isInvalidCredentials(detail)) showFirstLoginHint();" in LOGIN_JS


def test_temporary_password_login_lands_on_the_change_screen():
    assert "payload.password_reset_required" in LOGIN_JS
    change_go = LOGIN_JS.index('window.location.assign("/attendance/change-password")')
    guard = LOGIN_JS.index("if (payload && payload.password_reset_required)")
    assert guard < change_go
    # 서버가 그 값을 실제로 세션에서 읽어 템플릿에 넘긴다.
    pages = (BASE_DIR / "src" / "routers" / "pages.py").read_text(encoding="utf-8")
    change_page = pages[pages.index("def attendance_change_password_page"):]
    change_page = change_page[: change_page.index("@router.get(\"/work.html\"")]
    assert '"password_reset_required"' in change_page
    # 화면은 2단계 중 2단계임을 알린다.
    assert "{% if password_reset_required %}" in CHANGE_PW_PAGE
    assert "1/2 임시 비밀번호로 로그인 완료 → 2/2 새 비밀번호를 정하세요" in CHANGE_PW_PAGE
    assert "현재 비밀번호" in CHANGE_PW_PAGE, "스스로 바꾸러 온 사람의 문구가 사라졌다"
    # 규칙은 틀리기 전에 보여준다.
    assert "같은 숫자 반복(1111)·연속 숫자(1234) 불가" in CHANGE_PW_PAGE
    # 빠져나갈 길은 그대로.
    assert 'id="att-change-later"' in CHANGE_PW_PAGE
    assert 'id="att-change-logout"' in CHANGE_PW_PAGE


def test_admin_logout_ends_the_manager_session_too():
    """공용 PC: 근태 세션만 지우면 /attendance/login 이 책임자 모드로 되돌린다.

    그러면 다음 직원은 로그인 창을 아예 만나지 못한다(pages.py 의 리다이렉트).
    """
    handler = ATTENDANCE_JS[ATTENDANCE_JS.index('logoutBtn?.addEventListener'):]
    handler = handler[: handler.index("empSelect?.addEventListener")]
    assert 'apiPost("/api/attendance/logout"' in handler
    assert 'apiPost("/api/auth/logout"' in handler
    assert "if (adminMode)" in handler
    # 책임자도 직원과 같은 곳(로그인 화면)으로 나간다 — 예전엔 "/" 로 갔다.
    assert 'window.location.assign("/attendance/login")' in handler
    assert 'adminMode ? "/"' not in ATTENDANCE_JS
    assert 'title="책임자 세션도 함께 종료합니다"' in ATTENDANCE_PAGE


# ── ⑨ 임시 비밀번호 상태면 그 비밀번호를 다시 묻지 않는다(2026-08-28 결정 B1) ──
def test_reset_flow_does_not_ask_for_the_temporary_password_again():
    """방금 임시 비밀번호로 로그인한 사람에게 그것을 한 번 더 타이핑시키지 않는다."""
    # 현재 비밀번호 칸 전체가 조건부로 렌더된다(라벨만 바꾸는 것으로는 부족하다).
    assert "{% if not password_reset_required %}" in CHANGE_PW_PAGE
    field = CHANGE_PW_PAGE.index('id="att-current-password"')
    guard = CHANGE_PW_PAGE.rindex("{% if not password_reset_required %}", 0, field)
    endif = CHANGE_PW_PAGE.index("{% endif %}", field)
    assert guard < field < endif
    # 옛 이중 라벨(임시 비밀번호를 다시 묻던 문구)은 사라져야 한다.
    assert "임시 비밀번호 (방금 로그인에 쓴 것)" not in CHANGE_PW_PAGE
    # JS 가 상태를 읽을 표식.
    assert 'data-reset-flow="{{ \'true\' if password_reset_required else \'false\' }}"' in CHANGE_PW_PAGE


def test_reset_flow_javascript_omits_current_password_and_survives_a_missing_input():
    assert 'form?.dataset?.resetFlow === "true"' in CHANGE_PW_JS
    # 초기화 경로의 본문에는 current_password 자체가 없다.
    assert "{ new_password: next }" in CHANGE_PW_JS
    assert "{ current_password: current, new_password: next }" in CHANGE_PW_JS
    # 칸이 없을 수 있으므로 값 읽기·포커스 모두 null 을 견뎌야 한다.
    assert "currentInput ? currentInput.value" in CHANGE_PW_JS
    assert "(currentInput || newInput)?.focus();" in CHANGE_PW_JS
    assert "currentInput.value ||" not in CHANGE_PW_JS.replace(
        "currentInput ? currentInput.value || \"\" : \"\"", ""
    )
    # 새 오류 코드 해설.
    assert "CURRENT_PASSWORD_REQUIRED" in CHANGE_PW_JS
    assert "임시 비밀번호 상태가 아닙니다" in CHANGE_PW_JS
