"""포털 수집 한 회차 — 로그인 → 목록 → 본문 → 저장 항목.

DB 를 모른다. 항목과 진단만 돌려주고 적재는
``services/attendance_approvals.collect_from_portal`` 이 한다.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import Any

from . import parser
from .client import (
    PAGE_SIZE,
    PortalClient,
    PortalError,
    PortalLoginFailed,
    PortalPermissionDenied,
)

logger = logging.getLogger(__name__)

# 한 회차에 읽을 목록 쪽수 상한. 60일 한 부서면 한두 쪽이라, 이 상한에 닿으면
# 조회 조건이 잘못된 것으로 본다(회사 전체로 넓어졌다든지).
MAX_PAGES = 20
# 한 회차에 본문을 열 문서 수 상한. 화면 버튼이 기다리는 요청이라 시간을 묶어 둔다.
MAX_BODIES = 300


def portal_date(day: _dt.date) -> str:
    """포털 검색 칸 형식(`YYYY.MM.DD`)."""
    return f"{day.year:04d}.{day.month:02d}.{day.day:02d}"


def collect(
    *,
    client: PortalClient,
    window_days: int = 60,
    today: _dt.date | None = None,
) -> dict[str, Any]:
    """목록을 훑어 본문까지 읽고 저장 항목을 만든다.

    반환:
        items            저장 항목(진단 키 없음)
        rows             목록에서 읽은 문서 수(문서번호 기준 중복 제거 후)
        pages            읽은 쪽수
        forbidden        권한이 없어 건너뛴 문서 수(`읽을 수 없음`)
        unresolved       못 읽은 값이 있는 문서 [{doc_no, fields, title_raw}]
        incomplete       필수 항목이 비어 저장하지 않은 문서 [{doc_no, missing}]
        errors           본문 하나가 실패했을 때의 사유 목록(수집 자체는 계속)

    한 문서가 실패해도 나머지는 계속한다. 로그인 실패만 예외로 올린다 —
    그때는 가져올 것이 하나도 없다.
    """
    reference = today or _dt.date.today()
    span = max(1, int(window_days))
    start = portal_date(reference - _dt.timedelta(days=span))
    end = portal_date(reference + _dt.timedelta(days=1))

    seen_appr_ids: set[str] = set()
    by_doc_no: dict[str, dict[str, Any]] = {}
    pages = 0

    for page_index in range(1, MAX_PAGES + 1):
        html = client.fetch_list_page(
            start_date=start, end_date=end, page_index=page_index
        )
        rows = parser.parse_list_rows(html)
        pages = page_index
        if not rows:
            break
        # pageIndex 가 끝을 넘으면 마지막 쪽으로 붙들려 같은 행이 다시 온다.
        # 새 문서가 하나도 없으면 거기서 끝이다.
        fresh = [row for row in rows if row.get("appr_id") not in seen_appr_ids]
        if not fresh:
            break
        for row in fresh:
            seen_appr_ids.add(row.get("appr_id") or "")
            # 같은 문서가 여러 행으로 나오는 일이 있다 — 문서번호로 합친다.
            by_doc_no.setdefault(row["doc_no"], row)
        if len(rows) < PAGE_SIZE:
            break

    items: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    incomplete: list[dict[str, Any]] = []
    errors: list[str] = []
    forbidden = 0

    for index, (doc_no, row) in enumerate(sorted(by_doc_no.items())):
        if index >= MAX_BODIES:
            errors.append("문서가 너무 많아 일부는 다음 회차로 미뤘습니다")
            break
        body: dict[str, Any] | None = None
        appr_id = row.get("appr_id") or ""
        if appr_id:
            try:
                body = parser.parse_body(client.fetch_body(appr_id))
            except PortalPermissionDenied:
                # 포털 권한 — 제목만 보고 값을 지어내지 않는다.
                forbidden += 1
                continue
            except PortalLoginFailed:
                raise
            except PortalError as exc:
                errors.append(str(exc))
            except Exception as exc:  # noqa: BLE001 — 한 건의 실패가 회차를 끝내면 안 된다
                logger.warning("결재 본문을 읽지 못했습니다: %s", exc)
                errors.append(type(exc).__name__)

        item = parser.build_item(
            doc_no=doc_no,
            title_raw=row.get("title") or "",
            doc_hash=parser.content_hash(
                [
                    doc_no,
                    row.get("title") or "",
                    row.get("drafter") or "",
                    row.get("dept") or "",
                    row.get("end_date") or "",
                ]
            ),
            status="완료" if (row.get("end_date") or "").strip() else None,
            drafted_at=parser.drafted_at_from_doc_no(doc_no),
            body=body,
        )
        if item["unresolved"]:
            unresolved.append(
                {
                    "doc_no": doc_no,
                    "fields": item["unresolved"],
                    "title_raw": item["title_raw"],
                }
            )
        missing = parser.missing_required(item)
        if missing:
            # 추측하지 않는다 — 못 읽었다는 사실만 남기고 저장하지 않는다.
            incomplete.append({"doc_no": doc_no, "missing": missing})
            continue
        items.append(parser.strip_diagnostics(item))

    return {
        "items": items,
        "rows": len(by_doc_no),
        "pages": pages,
        "forbidden": forbidden,
        "unresolved": unresolved,
        "incomplete": incomplete,
        "errors": errors,
        "window_days": span,
        "search_from": start,
        "search_to": end,
    }
