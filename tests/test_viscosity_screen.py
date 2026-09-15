"""점도 화면(/viscosity) 재설계 구조 검증 — 2026-08-13.

브라우저 없이도 지킬 수 있는 것만 본다: 화면이 세 탭으로 나뉘어 있고, 재설계로
새로 생긴 자리(사용한 PB 블록·이상 목록·PB 산점도·더보기)가 템플릿에 있으며,
JS 가 새 서버 계약을 실제로 부른다는 것. 렌더 순서·조합 회귀는 브라우저 실연이
따로 잡는다(feedback: browser-verify new flows).
"""

from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = (BASE_DIR / "templates" / "viscosity.html").read_text(encoding="utf-8")
CONTROLLER = (BASE_DIR / "static" / "js" / "viscosity.js").read_text(encoding="utf-8")


def test_screen_is_split_into_three_tabs():
    """한 페이지 세로 8,857px 를 셋으로 나눈 것이 이 재설계의 뼈대다."""
    for tab in ("register", "trend", "pb"):
        assert f'data-tab="{tab}"' in TEMPLATE, f"{tab} 탭 버튼이 없다"
        assert f'id="tab-{tab}"' in TEMPLATE, f"{tab} 탭 패널이 없다"
    # 기본 탭은 측정 등록 — 현장 작업자가 가장 먼저 쓰는 화면.
    assert '<button class="mgmt-tab active" data-tab="register"' in TEMPLATE


def test_toolbar_and_cards_stay_outside_the_tabs():
    """툴바·카드는 어느 탭에서나 같은 조회 조건을 말하므로 탭 밖 공통이다."""
    tabs_at = TEMPLATE.index('<nav class="mgmt-tabs visc-tabs"')
    for marker in ('id="visc-product-select"', 'id="visc-card-anomaly"'):
        assert TEMPLATE.index(marker) < tabs_at, f"{marker} 는 탭 위에 있어야 한다"


def test_existing_features_survive_the_redesign():
    """탭으로 재배치했을 뿐, 있던 기능은 하나도 없애지 않았다."""
    for marker in (
        'id="visc-trend-banner"',        # 추세 경보 배너
        'id="visc-period-alert"',        # 기간 경보 배너
        'id="visc-settings-modal"',      # 반제품 설정 모달
        'id="visc-exclude-modal"',       # 통계 제외 모달
        'id="visc-reactor"',             # 반응기 필터
        'id="visc-export-btn"',          # 이 반제품 Excel
        'id="visc-export-all-btn"',      # 전체 Excel
        'id="visc-gran-toggle"',         # 일/주/월/분기/연
    ):
        assert marker in TEMPLATE, f"{marker} 가 사라졌다"


def test_used_pb_block_and_manual_lot_input_exist():
    """서버가 감지한 '사용한 PB' 를 등록 전에 보여주고, 불확실하면 고칠 수 있어야 한다."""
    assert 'id="visc-usedpb"' in TEMPLATE
    assert 'id="visc-usedpb-lot"' in TEMPLATE
    assert "/used-pb" in CONTROLLER, "감지 미리보기 API 를 부르지 않는다"
    assert "material_lot" in CONTROLLER, "수동 보정값을 저장 요청에 싣지 않는다"


def test_anomaly_list_and_pb_scatter_exist():
    assert 'id="visc-anomaly-panel"' in TEMPLATE
    assert 'id="visc-anomaly-body"' in TEMPLATE
    assert "state.analysis.anomalies" in CONTROLLER or "analysis.anomalies" in CONTROLLER
    assert 'id="visc-pb-chart"' in TEMPLATE, "PB 연계 산점도 캔버스가 없다"


def test_pb_panel_is_not_hidden_when_matching_fails():
    """매칭 0 이어도 패널은 보이고 안내문을 낸다(조용히 사라지면 원인을 알 수 없다)."""
    panel_line = next(
        line for line in TEMPLATE.splitlines() if 'id="visc-source-pb-panel"' in line
    )
    assert "hidden" not in panel_line
    assert 'id="visc-pb-empty"' in TEMPLATE


def test_blend_records_come_from_the_batch_endpoint_not_per_row_hydration():
    """반제품 전환 1회당 HTTP 22회(낱개 상세 조회)를 없앤 것이 핵심 수정이다."""
    assert "/blend-records" in CONTROLLER
    assert "/blend/records/${record.id}" not in CONTROLLER, "낱개 hydrate 가 남아 있다"
    # '미등록만' 은 서버 파라미터로 — 잘라온 목록에만 걸면 거짓 빈 목록이 된다.
    assert "unregistered" in CONTROLLER


def test_delete_confirms_once():
    """되돌릴 수 없는 동작이지만 확인창 두 번은 두 번째를 읽지 않게 만들 뿐이다."""
    start = CONTROLLER.index("async function deleteReading(")
    end = CONTROLLER.index("\n  }", start)
    assert CONTROLLER.count("window.confirm", start, end) == 1


def test_anomaly_tab_and_filters_exist():
    """이상 관리 탭(2026-09-15) — 전 반제품 이상 목록과 상태 필터."""
    assert 'data-tab="anomaly"' in TEMPLATE
    assert 'id="tab-anomaly"' in TEMPLATE
    assert 'id="visc-anom-state"' in TEMPLATE
    start = TEMPLATE.index('id="visc-anom-state"')
    end = TEMPLATE.index("</select>", start)
    for value in ("unreviewed", "reviewed", "excluded", "all"):
        assert f'value="{value}"' in TEMPLATE[start:end], f"상태 옵션 {value} 가 없다"
    assert 'id="visc-anom-body"' in TEMPLATE


def test_review_modal_renders_for_everyone():
    """확인 처리는 담당자도 하므로 책임자 전용 블록(통계 제외 모달) 밖에 있어야 한다."""
    exclude_at = TEMPLATE.index('id="visc-exclude-modal"')
    block_start = TEMPLATE.rindex("{% if can_manage %}", 0, exclude_at)
    block_end = TEMPLATE.index("{% endif %}", exclude_at)
    review_at = TEMPLATE.index('id="visc-review-modal"')
    assert not (block_start < review_at < block_end), "확인 처리 모달이 책임자 블록 안에 있다"
    # 다른 {% if can_manage %} 블록 안에 들어가 있지도 않다.
    before = TEMPLATE[:review_at]
    assert before.count("{% if can_manage %}") == before.count("{% endif %}")


def test_anomaly_controller_contract():
    for text in (
        "미확인 이상이 없습니다",
        "확인 처리한 이상이 없습니다",
        "통계에서 제외한 측정이 없습니다",
        "이상 측정이 없습니다",
    ):
        assert text in CONTROLLER, f"빈 목록 문구 '{text}' 가 없다"
    assert "/viscosity/anomalies" in CONTROLLER
    assert "/review" in CONTROLLER
    assert "/unreview" in CONTROLLER
    # 대시보드 카드 딥링크(?tab=anomaly&state=...)를 받는다.
    assert "URLSearchParams" in CONTROLLER
    assert 'params.get("tab") === "anomaly"' in CONTROLLER
    assert 'params.get("state")' in CONTROLLER


def test_static_assets_are_cache_busted_together():
    """세 파일이 **같은 버전**으로 함께 갱신되는지 — 특정 값이 아니라 일치가 계약이다.

    값을 하드코딩하면 버전을 올릴 때마다 테스트가 따라 깨진다(2026-08-13 실제 사고:
    ?v=b 갱신 후 이 테스트만 a 를 요구해 실패)."""
    import re

    versions = {
        name: re.search(rf"{name}\?v=([0-9a-z]+)", TEMPLATE)
        for name in ("viscosity\\.css", "viscosity_lib\\.js", "viscosity\\.js")
    }
    missing = [n for n, m in versions.items() if m is None]
    assert not missing, f"캐시버스팅 누락: {missing}"
    values = {m.group(1) for m in versions.values()}
    assert len(values) == 1, f"세 파일의 ?v= 가 갈렸다: {values}"
