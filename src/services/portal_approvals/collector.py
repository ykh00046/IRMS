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

# 한 회차에 읽을 목록 쪽수 상한. 실측(2026-09-23, 양식명 `근태`)으로 270일이 344건
# = 7쪽이라 20쪽이면 넉넉하다. 이 상한에 닿으면 조회 조건이 잘못된 것으로 본다.
MAX_PAGES = 20
# 한 회차에 **본문을 새로 열** 문서 수 상한. 화면 버튼이 기다리는 동기 요청이라
# 시간을 묶어 둔다(문서 하나가 왕복 0.3~0.5초). 기본 창 60일이 실측 96건이라 한 번에
# 끝나고, 창을 270일로 넓혀도 이 상한이 회차를 나눠 준다 — 남은 수는 결과에 실어
# 보내므로 버튼을 다시 누르면 이어서 받는다. 이미 저장된 문서는 세지 않는다.
MAX_BODIES = 120
# '열어 봤지만 저장할 수 없던 문서' 기억의 상한. 창 안의 그런 문서 수만큼만 커지므로
# 보통 수십 건이다. 터무니없이 커지면 잘라 낸다(잘린 건은 다음 회차에 다시 열릴 뿐이다).
MAX_SKIP_MEMO = 500


def portal_date(day: _dt.date) -> str:
    """포털 검색 칸 형식(`YYYY.MM.DD`)."""
    return f"{day.year:04d}.{day.month:02d}.{day.day:02d}"


def row_signature(row: dict[str, Any]) -> str:
    """목록 행 하나의 지문 — 이 값이 그대로면 문서가 안 바뀐 것이다.

    저장 항목의 `doc_hash` 와 **같은 값**이라, 이미 저장된 문서의 해시와 비교하는
    것만으로 "본문을 다시 열 필요가 있는가"를 판단할 수 있다. 결재가 다시 완료되면
    완료일이 바뀌어 지문도 바뀐다.
    """
    return parser.content_hash(
        [
            row.get("doc_no") or "",
            row.get("title") or "",
            row.get("drafter") or "",
            row.get("dept") or "",
            row.get("end_date") or "",
        ]
    )


def collect(
    *,
    client: PortalClient,
    window_days: int = 60,
    form_name: str = "근태허가원",
    known: dict[str, str] | None = None,
    skipped_before: dict[str, dict[str, Any]] | None = None,
    max_bodies: int | None = None,
    today: _dt.date | None = None,
) -> dict[str, Any]:
    """목록을 훑어 **새로 생기거나 바뀐 문서만** 본문까지 읽고 저장 항목을 만든다.

    Args:
        form_name: 양식명 부분일치 검색어. 문서제목이 아니라 양식명으로 찾는다 —
            제목에 '근태허가원'이 없는 평범한 휴가 결재가 훨씬 많기 때문이다(§2).
        known: 이미 저장된 `{문서번호: doc_hash}`. 목록 행 지문이 같으면 본문을
            열지 않고 `skipped_unchanged` 로 센다. 한 회차의 비용은 본문 왕복이
            전부라, 이 건너뛰기가 두 번째 회차부터를 몇 초로 줄인다.
        skipped_before: 지난 회차에 **열어 봤지만 저장할 수 없던** 문서
            `{문서번호: {"signature", "reason", "missing"}}`. 권한이 없거나 필수 값을
            못 읽은 문서는 표에 남지 않아 `known` 으로는 걸러지지 않는다. 이것이 없으면
            그런 문서가 회차마다 예산을 먹어 **밀린 문서가 영영 안 들어온다**
            (2026-09-23 브라우저 실연에서 실제로 막혔다).
        max_bodies: 이 회차에 새로 열 본문 수 상한. 넘친 만큼은 `remaining` 으로
            알린다 — 다시 부르면 이어서 받는다.

    반환:
        items             저장 항목(진단 키 없음)
        rows              목록에서 읽은 문서 수(문서번호 기준 중복 제거 후)
        pages             읽은 쪽수
        fetched           본문을 새로 연 문서 수
        skipped_unchanged 이미 저장돼 있고 지문이 같아 건너뛴 문서 수
        skipped_unreadable 지난번에 못 읽어 다시 열지 않은 문서 수
        skip_memo         다음 회차에 넘길 '못 읽은 문서' 기억(호출자가 저장한다)
        remaining         상한에 걸려 이번에 못 연 문서 수
        forbidden         권한이 없어 건너뛴 문서 수(`읽을 수 없음`, 기억분 포함)
        unresolved        못 읽은 값이 있는 문서 [{doc_no, fields, title_raw}]
        incomplete        필수 항목이 비어 저장하지 않은 문서 [{doc_no, missing}]
        errors            본문 하나가 실패했을 때의 사유 목록(수집 자체는 계속)

    한 문서가 실패해도 나머지는 계속한다. 로그인 실패만 예외로 올린다 —
    그때는 가져올 것이 하나도 없다.
    """
    reference = today or _dt.date.today()
    span = max(1, int(window_days))
    start = portal_date(reference - _dt.timedelta(days=span))
    end = portal_date(reference + _dt.timedelta(days=1))
    stored = known or {}
    remembered = skipped_before or {}
    # 호출 시점에 읽는다 — 기본값 인자로 묶으면 테스트가 상한을 낮출 수 없다.
    limit = max(1, int(MAX_BODIES if max_bodies is None else max_bodies))

    seen_appr_ids: set[str] = set()
    by_doc_no: dict[str, dict[str, Any]] = {}
    pages = 0

    for page_index in range(1, MAX_PAGES + 1):
        html = client.fetch_list_page(
            start_date=start,
            end_date=end,
            page_index=page_index,
            form_name=form_name,
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
    fetched = 0
    skipped_unchanged = 0
    skipped_unreadable = 0
    remaining = 0
    skip_memo: dict[str, dict[str, Any]] = {}

    # 최신 문서부터 연다 — 상한에 걸려 회차가 나뉘어도 오늘 것이 먼저 들어온다.
    for doc_no, row in sorted(by_doc_no.items(), reverse=True):
        signature = row_signature(row)
        if stored.get(doc_no) == signature:
            # 이미 저장돼 있고 목록 행이 그대로다 — 본문을 열 이유가 없다.
            skipped_unchanged += 1
            continue
        memo = remembered.get(doc_no)
        if isinstance(memo, dict) and memo.get("signature") == signature:
            # 지난번에 열어 봤지만 저장할 수 없던 문서다(권한 없음·값 부족).
            # 다시 열어도 결과가 같으므로 건너뛰되, 건수는 그대로 알린다.
            skipped_unreadable += 1
            skip_memo[doc_no] = memo
            if memo.get("reason") == "forbidden":
                forbidden += 1
            else:
                incomplete.append(
                    {"doc_no": doc_no, "missing": list(memo.get("missing") or [])}
                )
            continue
        if fetched >= limit:
            remaining += 1
            continue
        body: dict[str, Any] | None = None
        appr_id = row.get("appr_id") or ""
        if appr_id:
            fetched += 1
            try:
                body = parser.parse_body(client.fetch_body(appr_id))
            except PortalPermissionDenied:
                # 포털 권한 — 제목만 보고 값을 지어내지 않는다.
                forbidden += 1
                skip_memo[doc_no] = {"signature": signature, "reason": "forbidden"}
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
            doc_hash=signature,
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
            skip_memo[doc_no] = {
                "signature": signature,
                "reason": "incomplete",
                "missing": missing,
            }
            continue
        items.append(parser.strip_diagnostics(item))

    return {
        "items": items,
        "rows": len(by_doc_no),
        "pages": pages,
        "fetched": fetched,
        "skipped_unchanged": skipped_unchanged,
        "skipped_unreadable": skipped_unreadable,
        "skip_memo": dict(sorted(skip_memo.items())[:MAX_SKIP_MEMO]),
        "remaining": remaining,
        "forbidden": forbidden,
        "unresolved": unresolved,
        "incomplete": incomplete,
        "errors": errors,
        "window_days": span,
        "form_name": form_name,
        "search_from": start,
        "search_to": end,
    }
