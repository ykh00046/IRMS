"""감사로그 이벤트가 화면에 영문 코드로 새어나가지 않는지 검사.

서버가 write_audit_log(action="...") 로 남기는 이벤트는 계속 늘어나는데,
한글 라벨표(static/js/admin_users.js 의 AUDIT_GROUPS)는 손으로 관리한다.
실제로 2026-07 시점에 49종 중 26종이 라벨 없이 영문 코드로 표시되고 있었고,
하필 수기 입력 승인·증량 승인 거절·계정 삭제처럼 책임자가 감사 때 찾아보는
이벤트들이 거기 속했다. 새 이벤트를 추가하면 이 테스트가 먼저 알려준다.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_JS = ROOT / "static" / "js" / "admin_users.js"


def _server_actions() -> set[str]:
    """src/ 전체에서 write_audit_log(action="리터럴") 을 수집한다."""
    actions: set[str] = set()
    for path in (ROOT / "src").rglob("*.py"):
        source = path.read_text(encoding="utf-8", errors="ignore")
        for call in re.finditer(r"write_audit_log\((.{0,300}?)\)", source, re.S):
            found = re.search(r"""action\s*=\s*["']([\w.\-]+)["']""", call.group(1))
            if found:
                actions.add(found.group(1))
    return actions


def _labeled_actions() -> set[str]:
    """AUDIT_GROUPS 안의 ["action", "라벨"] 쌍에서 action 키만 뽑는다."""
    source = ADMIN_JS.read_text(encoding="utf-8")
    start = source.index("const AUDIT_GROUPS")
    end = source.index("const AUDIT_LABELS", start)
    return set(re.findall(r"""\[\s*["']([\w.\-]+)["']\s*,\s*["']""", source[start:end]))


def test_every_server_audit_action_has_a_korean_label() -> None:
    missing = sorted(_server_actions() - _labeled_actions())
    assert not missing, (
        "감사로그 화면에 영문 코드로 표시될 이벤트가 있습니다. "
        f"static/js/admin_users.js 의 AUDIT_GROUPS 에 추가하세요: {missing}"
    )


def test_audit_actions_are_collected_at_all() -> None:
    """수집 정규식이 깨지면 위 테스트가 조용히 통과하므로 하한선을 둔다."""
    assert len(_server_actions()) >= 40


def test_filter_options_are_not_hardcoded_in_template() -> None:
    """필터 목록을 템플릿에 다시 적으면 라벨표와 또 어긋난다."""
    html = (ROOT / "templates" / "admin_users.html").read_text(encoding="utf-8")
    block = html[html.index('id="audit-action-filter"') :]
    block = block[: block.index("</select>")]
    assert block.count("<option") == 1, "이벤트 필터는 AUDIT_GROUPS 에서 생성해야 합니다"
