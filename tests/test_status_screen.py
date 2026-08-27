"""배합 기록 화면(/status) 재구축 구조 검증 — 2026-08-14.

브라우저 없이 지킬 수 있는 것만 본다: 표가 쪽으로 나뉘고, 선택이 화면이 아니라 상태
(Set)에 있으며, 정렬이 서버를 다시 부르지 않고, 없어진 요약 엔드포인트 왕복이 정말
사라졌다는 것. 렌더 순서·조합 회귀는 브라우저 실연이 따로 잡는다
(feedback: browser-verify new flows).
"""

from __future__ import annotations

import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = (BASE_DIR / "templates" / "status.html").read_text(encoding="utf-8")
CONTROLLER = (BASE_DIR / "static" / "js" / "status.js").read_text(encoding="utf-8")
BLEND_CSS = (BASE_DIR / "static" / "css" / "blend.css").read_text(encoding="utf-8")


def test_table_is_paged_at_fifty_rows():
    """진입 즉시 500행을 전부 그려 18,659px 가 되던 것이 이 재구축의 출발점이다."""
    assert "const PAGE_SIZE = 50;" in CONTROLLER
    for marker in ('id="status-pager"', 'id="status-page-prev"',
                   'id="status-page-next"', 'id="status-page-info"'):
        assert marker in TEMPLATE, f"{marker} 가 없다"


def test_selection_lives_in_a_set_not_in_the_checkboxes():
    """쪽 이동·정렬·재조회를 넘어 살아남아야 하므로 선택의 주인은 상태다."""
    assert "const selected = new Set()" in CONTROLLER
    assert 'id="status-sel-count"' in TEMPLATE, "선택 건수 표시가 없다"
    assert 'id="status-sel-clear"' in TEMPLATE, "선택 해제 버튼이 없다"
    # 출력·취소는 DOM 을 훑지 않고 선택 집합에서 id 를 가져온다(쪽 밖 선택 포함).
    assert "function selectedIdsInOrder()" in CONTROLLER
    assert ".rec-chk:checked" not in CONTROLLER, "아직 화면 체크박스에서 선택을 긁고 있다"


def test_all_query_results_can_be_selected_across_pages():
    """머리글 체크는 보이는 쪽 50건만 잡는다 — 기간 조회 146건을 3번 끊어 출력하던 불편
    (2026-08-27). 조회 결과 전체 선택은 쪽을 넘기지 않고 allRecords 전부를 선택 집합에 넣는다."""
    assert 'id="status-sel-all-results"' in TEMPLATE, "조회 결과 전체 선택 버튼이 없다"
    handler = CONTROLLER.split('$("status-sel-all-results").addEventListener("click"', 1)[1]
    assert "allRecords.forEach((r) => selected.add(Number(r.id)))" in handler
    # 200건 출력 상한을 넘는 조회는 선택 시점에 미리 말한다.
    assert "n > MAX_PRINT" in handler
    # 한 쪽이면 머리글 체크와 같은 뜻 — 여러 쪽일 때만 보인다.
    assert "allBtn.hidden = total <= PAGE_SIZE || everyLoaded;" in CONTROLLER


def test_sorting_redraws_without_calling_the_server():
    """정렬 머리글이 loadRecords 를 부르면 API 를 두 번 치고 선택이 날아간다."""
    start = CONTROLLER.index("const key = th.dataset.sort;")
    end = CONTROLLER.index("const gotoPage", start)
    block = CONTROLLER[start:end]
    assert "renderTable()" in block
    assert "loadRecords" not in block


def test_unacked_filter_is_a_server_parameter():
    """상한(500건) 밖의 미확인 건이 영영 안 보이던 통제 사각의 수정."""
    assert "unacked: isUnackedOnly() ? 1 : undefined" in CONTROLLER


def test_the_separate_rescale_summary_roundtrip_is_gone():
    """행 플래그로 배지를 그린다 — 무조건 최신 1000건이던 짝맞추기 왕복 제거."""
    assert "loadRescaleMap" not in CONTROLLER
    assert "rescaleMap" not in CONTROLLER
    # 주석 밖에서 그 경로를 실제로 부르지 않는지(문자열로 남아 있지 않은지) 확인.
    calls = [ln for ln in CONTROLLER.splitlines()
             if "rescales/summary" in ln and not ln.strip().startswith("//")]
    assert not calls, f"아직 요약 엔드포인트를 부른다: {calls}"


def test_product_filter_population_comes_from_the_records_table():
    assert "/blend/records/product-names" in CONTROLLER
    assert "/blend/product-usage" not in CONTROLLER


def test_detail_open_reports_its_failure():
    """행을 눌러도 아무 일이 없던(콘솔에만 남던) 조용한 실패를 없앤다."""
    start = CONTROLLER.index("async function openDetail(")
    end = CONTROLLER.index("// ── 전체 수정", start)
    block = CONTROLLER[start:end]
    assert "기록을 불러오지 못했습니다" in block
    assert "catch" in block


def test_sign_checkbox_is_one_state_and_single_excel_honors_it():
    """툴바 체크와 모달 체크가 서로 모르던 탓에 서명 없는 파일이 서명본으로 오해됐다."""
    assert "function setSign(" in CONTROLLER
    assert "const signOn = ()" in CONTROLLER
    assert re.search(r"/export\$\{signOn\(\) \? \"\?sign=1\" : \"\"\}", CONTROLLER), \
        "단건 Excel 에 sign 파라미터가 붙지 않는다"


def test_long_exports_never_navigate_away_from_the_screen():
    """전체 Excel 이 location.assign 이면 변환 몇 분 동안 화면을 떠나 있게 된다."""
    navigations = [ln for ln in CONTROLLER.splitlines()
                   if "location.assign" in ln and not ln.strip().startswith("//")]
    assert not navigations, f"아직 화면을 떠나는 내려받기가 있다: {navigations}"
    # 정의 + 일괄 출력 공통 경로 + 전체 Excel. 일괄 PDF·ZIP 은 wireBatchExport 를 공유한다.
    assert CONTROLLER.count("startLongExport(") >= 3
    assert CONTROLLER.count("wireBatchExport(\"status-rec-") == 2


def test_batch_print_warns_about_canceled_and_names_which_two_hundred():
    assert "배합일지에서 제외됩니다" in CONTROLLER
    assert "표 정렬 순서 기준 위에서" in CONTROLLER


def test_bulk_cancel_is_capped_and_states_the_exact_count():
    assert "const MAX_BULK_CANCEL = 50;" in CONTROLLER
    assert "각 기록에 사유가 남습니다" in CONTROLLER


def test_detail_head_gains_code_detail_name_and_reactor():
    """DHR 인쇄물(인허가 양식)이 아니라 이 상세 화면에만 붙는 값들."""
    start = CONTROLLER.index('<div class="dhr-head">')
    end = CONTROLLER.index("${bulkLine}", start)
    head = CONTROLLER[start:end]
    assert "품목코드" in head
    assert "rec.product_code" in head
    # 세부 품명·반응기는 값이 있을 때만 끼우므로 머리 그리드에는 자리(${...})로 들어간다.
    assert "${detailName}" in head and "${reactorCell}" in head
    assert '세부 품명</span><b>${esc(rec.ink_name)}' in CONTROLLER, \
        "ink_name 은 '세부 품명' 으로 표시한다(잉크 표기 금지)"
    assert '반응기</span><b>${esc(rec.reactor)}' in CONTROLLER


def test_rows_are_reachable_by_keyboard():
    assert "tr.tabIndex = 0" in CONTROLLER
    assert 'e.key === "Enter"' in CONTROLLER


def test_print_rules_are_scoped_to_the_open_modal():
    """모달이 닫혀 있을 때 Ctrl+P 가 백지 한 장이던 규칙의 수정."""
    start = BLEND_CSS.index("@media print {")
    block = BLEND_CSS[start:BLEND_CSS.index("\n}", start)]
    assert "body.dhr-open *" in block
    assert re.search(r"^\s*body \*", block, re.M) is None, "무조건 숨기는 규칙이 남아 있다"
    assert "body.dhr-open { overflow: hidden; }" in BLEND_CSS


def test_rescale_drivers_have_a_rule_of_their_own():
    assert ".blend-rescale-drivers {" in BLEND_CSS


def test_deep_links_cover_period_and_unacked():
    """대시보드 '오늘 배합' 카드가 /status?from=…&to=… 로 들어온다."""
    for key in ('get("search")', 'get("from")', 'get("to")', 'get("unacked")'):
        assert key in CONTROLLER, f"딥링크 {key} 미지원"


def test_shared_stylesheet_is_cache_busted_together():
    """blend.css 는 배합 화면들과 공유한다 — 한 화면만 올리면 나머지가 옛 CSS 를 쓴다.

    특정 값이 아니라 **일치**가 계약이다(값을 박으면 버전을 올릴 때마다 테스트가 깨진다).
    """
    versions = set()
    for name in ("status.html", "blend.html", "blend_continuous.html"):
        html = (BASE_DIR / "templates" / name).read_text(encoding="utf-8")
        m = re.search(r"blend\.css\?v=([0-9a-z]+)", html)
        assert m, f"{name} 에 blend.css 캐시버스팅이 없다"
        versions.add(m.group(1))
    assert len(versions) == 1, f"blend.css ?v= 가 화면마다 갈렸다: {versions}"
    assert re.search(r"status\.js\?v=([0-9a-z]+)", TEMPLATE), "status.js 캐시버스팅 누락"
