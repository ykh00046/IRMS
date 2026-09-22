"""근태허가원(결재 문서) 수집·적재·대조 서비스.

계약: ``docs/attendance-approvals.md``. BRM 이 **직접** 포털에 로그인해 부서공개함의
근태허가원을 읽어(``services/portal_approvals``) ``attendance_approvals`` 표에 멱등
적재하고(§3·§4), 책임자 월 화면이 쓰는 대조 세 목록(§5)을 만든다.

수집 경로는 하나뿐이다. 별도 수집기 앱이 파일이나 HTTP 로 넘기던 길은 2026-09-22 에
걷어냈다 — 그 앱이 BRM 운영 서버와 다른 PC 에서 돌아 넘길 방법이 없었기 때문이다(§1.2).

원칙(같은 문서 §1):
  1. 근태 판정의 근거는 계속 ERP 월 엑셀이다. 허가원은 보강이라 수집이 멈추면
     종전대로 동작한다 — 이 모듈은 판정·알림을 건드리지 않는다.
  2. 엑셀 파싱은 ``services/attendance_excel`` 가 계속 소유한다. 여기서는 그 결과를
     읽기 전용으로 받아(명단 = ``employee_list``, 월 행 = ``month_employee_rows``)
     맞추기와 대조만 한다.
  3. 개인 사정(종류·사유)은 책임자 화면 전용이다. 이 모듈의 반환값을 공개(트레이)
     응답에 싣지 않는다. **포털 자격증명은 반환값·감사·로그 어디에도 넣지 않는다.**
"""

import json
import logging
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

from ..db.time_utils import utc_now_text
from . import settings_service
# 사번 비교축은 하나뿐이다 — 엑셀 셀이 숫자형(171013.0)으로 나오는 문제를 이미
# 이 헬퍼가 흡수한다(BUG-2). 여기서 다시 손으로 깎으면 축이 둘로 갈린다.
from .attendance_excel.models import normalize_emp_id

logger = logging.getLogger(__name__)

# 마지막 수집 실행 시각. 새로 적재된 건이 0 이어도 "수집은 돌았다"를 남겨야 하므로
# 표의 MAX(collected_at) 대신 실행 마커를 따로 둔다(app_settings 키-값).
LAST_RUN_SETTING_KEY = "attendance_approvals_last_run_at"

# 마지막 회차의 결과 요약(JSON)과, 동시 실행을 막는 잠금(시작 시각).
LAST_RUN_RESULT_KEY = "attendance_approvals_last_run"
RUN_LOCK_KEY = "attendance_approvals_run_lock"
# 잠금이 이보다 오래되면 죽은 회차로 보고 무시한다(프로세스가 중간에 꺼진 경우).
RUN_LOCK_STALE_MINUTES = 10

# 수집 회차 결과 코드. 한글 문구는 화면(attendance.js)이 소유한다.
RUN_OK = "ok"
RUN_NOT_CONFIGURED = "not_configured"
RUN_LOGIN_FAILED = "login_failed"
RUN_ERROR = "error"
RUN_BUSY = "busy"

# 화면에 실어 보내는 목록 상한. 전체 건수는 따로 센다.
_MAX_REPORTED_REJECTS = 20

# 수집 상태 한 줄이 "오래됨"으로 바뀌는 문턱(§5.4). 수집기는 하루 1회 도는 전제다.
STALE_AFTER_DAYS = 2

# 정규화된 종류 5종(§4). 표기 순서는 화면 정렬용이 아니라 문서의 나열 순서다.
KINDS = ("연차", "반차", "반반차", "예비군", "기타")
OTHER_KIND = "기타"

# 원문 → 정규화. "반반차"가 "반차"의 부분문자열이므로 **반드시 반반차를 먼저** 본다
# (anomaly._partial_leave_shift 와 같은 함정).
_KIND_PATTERNS = (
    ("반반차", "반반차"),
    ("반차", "반차"),
    ("연차", "연차"),
    ("예비군", "예비군"),
    # 실제 포털 문서의 종류 칸은 '훈련' 한 단어다(2026-09-22 실측 10건). 사유에 예비군이
    # 적혀 있어도 종류는 '훈련'이라, 이 줄이 없으면 예비군 건이 전부 '기타'로 접힌다.
    ("훈련", "예비군"),
)

HALVES = ("오전", "오후")

# ERP 엑셀에서 "휴가 표시"로 읽을 키워드. 허가원이 뒷받침하는 종류만 넣는다 —
# 휴직(장기 부재)·결근은 허가원 대상이 아니고, 교육은 결재 종류가 따로 있다.
ERP_LEAVE_KEYWORDS = (
    "연차",
    "월차",
    "휴가",
    "반차",
    "반반차",
    "유급",
    "공가",
    "예비군",
    "훈련",
)

# 대조는 평일 근무일만 본다. 주휴·무휴·유휴에는 휴가 표시가 없는 것이 정상이라
# 그대로 세면 토·일이 전부 "허가원만 있고 엑셀엔 없음"으로 뜬다.
RECONCILE_DAY_TYPES = ("평일", "평일2")

# 한 문서가 덮는 날짜를 펼칠 때의 상한(수집 오류로 end_date 가 몇 년 뒤인 경우 방어).
_MAX_SPAN_DAYS = 400

_TEXT_CAPS = {
    "doc_no": 120,
    "emp_name": 60,
    "emp_id": 20,
    "kind_raw": 60,
}
_TRUNCATED_CAPS = {
    "status": 40,
    "drafted_at": 20,
    "title_raw": 500,
    "doc_hash": 200,
    "source": 40,
}

_FIELDS = (
    "doc_no",
    "emp_name",
    "emp_id",
    "kind_raw",
    "kind",
    "half",
    "start_date",
    "end_date",
    "status",
    "drafted_at",
    "title_raw",
    "doc_hash",
    "source",
)

# 수정본 판정용 비교 대상. doc_hash 가 비었을 때(수집기가 해시를 못 준 경우)
# 내용 비교로 갈음한다.
_COMPARE_FIELDS = (
    "emp_name",
    "emp_id",
    "kind_raw",
    "kind",
    "half",
    "start_date",
    "end_date",
    "status",
    "drafted_at",
    "title_raw",
)


# ── 값 다듬기 ────────────────────────────────────────────────────────────────


def normalize_kind(kind_raw: str) -> str:
    """문서 종류 원문을 5종으로 접는다. 모르는 표현은 `기타`(원문은 kind_raw 에 남는다)."""
    text = str(kind_raw or "")
    for needle, kind in _KIND_PATTERNS:
        if needle in text:
            return kind
    return OTHER_KIND


def normalize_half(half: Any) -> str | None:
    """`오전`/`오후` 만 살린다. 그 밖의 표현은 None — 한 값 때문에 문서를 버리지 않는다."""
    text = str(half or "").strip()
    return text if text in HALVES else None


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _is_ymd(text: str) -> bool:
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        return False
    try:
        date.fromisoformat(text)
    except ValueError:
        return False
    return True


def month_bounds(year_month: str) -> tuple[str, str]:
    """`YYYY-MM` → (그 달 1일, 그 달 마지막 날) 문자열. 형식이 나쁘면 ValueError."""
    year = int(year_month[:4])
    month = int(year_month[5:7])
    first = date(year, month, 1)
    last = date(year + (month == 12), (month % 12) + 1, 1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()


def covered_dates(start_date: str, end_date: str) -> list[str]:
    """문서가 덮는 날짜 전부(양끝 포함). 범위가 뒤집혔거나 너무 길면 시작일만."""
    if not (_is_ymd(start_date) and _is_ymd(end_date)):
        return [start_date] if start_date else []
    begin = date.fromisoformat(start_date)
    finish = date.fromisoformat(end_date)
    if finish < begin or (finish - begin).days > _MAX_SPAN_DAYS:
        return [start_date]
    return [
        (begin + timedelta(days=offset)).isoformat()
        for offset in range((finish - begin).days + 1)
    ]


def has_erp_leave(day_type: str, attendance_code: str, note: str) -> bool:
    """ERP 행에 휴가 표시가 있는가 — 구분·근태코드·비고를 합쳐 본다."""
    text = f"{day_type or ''} {attendance_code or ''} {note or ''}"
    return any(keyword in text for keyword in ERP_LEAVE_KEYWORDS)


# ── 적재(§3·§4) ──────────────────────────────────────────────────────────────


def validate_item(raw: Any, *, source: str) -> tuple[dict[str, Any] | None, str, str]:
    """한 건을 검사·정규화한다. 반환 `(정제값|None, doc_no, 거절사유)`.

    거절 사유는 고정 문구만 쓴다 — 수집기 로그에 남는 값이라 이름·종류 같은 개인
    사정을 되돌려 실으면 안 된다(§1.3).
    """
    if not isinstance(raw, dict):
        return None, "", "항목 형식 오류"

    doc_no = _text(raw.get("doc_no"))
    if not doc_no:
        return None, "", "문서번호 없음"

    emp_name = _text(raw.get("emp_name"))
    kind_raw = _text(raw.get("kind"))
    start_date = _text(raw.get("start_date"))
    end_date = _text(raw.get("end_date")) or start_date

    if not emp_name:
        return None, doc_no, "대상자 이름 없음"
    if not kind_raw:
        return None, doc_no, "종류 없음"
    if not start_date:
        return None, doc_no, "시작일 없음"
    if not _is_ymd(start_date):
        return None, doc_no, "시작일 형식 오류"
    if not _is_ymd(end_date):
        return None, doc_no, "종료일 형식 오류"
    if end_date < start_date:
        return None, doc_no, "종료일이 시작일보다 앞섬"

    emp_id = normalize_emp_id(raw.get("emp_id"))

    values: dict[str, Any] = {
        "doc_no": doc_no,
        "emp_name": emp_name,
        "emp_id": emp_id or None,
        "kind_raw": kind_raw,
        "kind": normalize_kind(kind_raw),
        "half": normalize_half(raw.get("half")),
        "start_date": start_date,
        "end_date": end_date,
        "status": _text(raw.get("status")) or None,
        "drafted_at": _text(raw.get("drafted_at")) or None,
        "title_raw": _text(raw.get("title_raw")) or None,
        "doc_hash": _text(raw.get("doc_hash")) or None,
        "source": source,
    }

    for field, cap in _TEXT_CAPS.items():
        if len(_text(values.get(field))) > cap:
            return None, doc_no, "값이 너무 김"
    for field, cap in _TRUNCATED_CAPS.items():
        current = values.get(field)
        if isinstance(current, str) and len(current) > cap:
            values[field] = current[:cap]

    return values, doc_no, ""


def _row_needs_update(existing: sqlite3.Row, values: dict[str, Any]) -> bool:
    """수정본인가. 해시가 양쪽에 있으면 해시가, 없으면 내용이 판단 기준이다."""
    incoming_hash = values.get("doc_hash")
    existing_hash = existing["doc_hash"]
    if incoming_hash and existing_hash:
        return incoming_hash != existing_hash
    if incoming_hash != existing_hash:
        return True
    return any(
        (existing[field] or None) != (values.get(field) or None)
        for field in _COMPARE_FIELDS
    )


def upsert_batch(
    connection: sqlite3.Connection,
    *,
    items: list[Any],
    source: str = "portal",
    collected_at: str | None = None,
) -> dict[str, Any]:
    """배치 적재. `doc_no` 가 멱등 키이고 `doc_hash` 가 갱신 여부를 정한다.

    한 건이 나빠도 나머지는 적재한다 — 거절은 `rejected` 목록으로 돌려준다(§3).
    커밋은 호출자 책임(다른 서비스와 같은 규약).
    """
    now = utc_now_text()
    stamp = _text(collected_at) or now
    clean_source = (_text(source) or "portal")[: _TRUNCATED_CAPS["source"]]

    created = updated = unchanged = 0
    rejected: list[dict[str, str]] = []
    seen_doc_nos: set[str] = set()

    for raw in items:
        values, doc_no, reason = validate_item(raw, source=clean_source)
        if values is None:
            rejected.append({"doc_no": doc_no, "reason": reason})
            continue
        if doc_no in seen_doc_nos:
            rejected.append({"doc_no": doc_no, "reason": "같은 배치에 문서번호 중복"})
            continue
        seen_doc_nos.add(doc_no)

        existing = connection.execute(
            "SELECT doc_hash, "
            + ", ".join(_COMPARE_FIELDS)
            + " FROM attendance_approvals WHERE doc_no = ?",
            (doc_no,),
        ).fetchone()

        if existing is None:
            columns = ", ".join((*_FIELDS, "collected_at", "updated_at"))
            placeholders = ", ".join("?" for _ in range(len(_FIELDS) + 2))
            connection.execute(
                f"INSERT INTO attendance_approvals ({columns}) VALUES ({placeholders})",
                (*(values[field] for field in _FIELDS), stamp, now),
            )
            created += 1
            continue

        if not _row_needs_update(existing, values):
            unchanged += 1
            continue

        assignments = ", ".join(f"{field} = ?" for field in _FIELDS if field != "doc_no")
        connection.execute(
            f"UPDATE attendance_approvals SET {assignments}, updated_at = ? "
            "WHERE doc_no = ?",
            (
                *(values[field] for field in _FIELDS if field != "doc_no"),
                now,
                doc_no,
            ),
        )
        updated += 1

    settings_service.set_setting(connection, LAST_RUN_SETTING_KEY, stamp)

    return {
        "received": len(items),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "rejected": rejected,
    }


# ── 포털 수집 한 회차(§3) ────────────────────────────────────────────────────


def _store_last_run(connection: sqlite3.Connection, result: dict[str, Any]) -> None:
    """마지막 회차 결과를 남긴다. 같은 값이면 쓰지 않는다."""
    encoded = json.dumps(result, ensure_ascii=False)
    if settings_service.get_setting(connection, LAST_RUN_RESULT_KEY) == encoded:
        return
    settings_service.set_setting(connection, LAST_RUN_RESULT_KEY, encoded)


def last_run(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """저장된 마지막 회차 결과. 없거나 깨졌으면 None."""
    raw = settings_service.get_setting(connection, LAST_RUN_RESULT_KEY)
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _acquire_run_lock(connection: sqlite3.Connection, *, now: str) -> bool:
    """회차 잠금. 이미 도는 회차가 있으면 False.

    화면 버튼과 하루 1회 자동 실행이 겹칠 수 있어, 포털을 두 번 두드리지 않게 막는다.
    프로세스가 중간에 꺼지면 잠금이 남으므로 10분이 지난 잠금은 무시한다.
    """
    current = _parse_stamp(_text(settings_service.get_setting(connection, RUN_LOCK_KEY)))
    if current is not None:
        age = datetime.now(timezone.utc) - current
        if age < timedelta(minutes=RUN_LOCK_STALE_MINUTES):
            return False
    settings_service.set_setting(connection, RUN_LOCK_KEY, now)
    connection.commit()
    return True


def _release_run_lock(connection: sqlite3.Connection) -> None:
    settings_service.set_setting(connection, RUN_LOCK_KEY, "")


def collect_from_portal(
    connection: sqlite3.Connection,
    *,
    client: Any | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """포털에서 한 회차 수집해 적재하고 결과를 남긴다.

    실패해도 예외를 밖으로 내보내지 않는다 — 결재 대조는 보강 자료라, 수집이
    무너져도 근태 화면과 나머지 BRM 은 그대로 돌아야 한다(§1.1 fail-open).
    반환값과 저장되는 결과 요약에는 **자격증명이 들어가지 않는다**(§1.3).

    커밋은 이 함수가 직접 한다(잠금을 쥐고 도는 구간이라 호출자에게 미루지 않는다).
    """
    from .. import config
    from . import portal_approvals

    started = utc_now_text()
    if not config.portal_configured():
        result = {"status": RUN_NOT_CONFIGURED, "at": started}
        _store_last_run(connection, result)
        connection.commit()
        return result

    if not _acquire_run_lock(connection, now=started):
        # 이미 도는 회차가 있다 — 지난 결과는 그대로 두고 지금 것만 알린다.
        return {"status": RUN_BUSY, "at": started}

    try:
        portal_client = client or portal_approvals.PortalClient(
            config.PORTAL_BASE_URL,
            config.PORTAL_USERNAME,
            config.PORTAL_PASSWORD,
            timeout=config.PORTAL_TIMEOUT_SEC,
        )
        harvest = portal_approvals.collect(
            client=portal_client,
            window_days=config.PORTAL_WINDOW_DAYS,
            today=today,
        )
    except portal_approvals.PortalLoginFailed as exc:
        result = {"status": RUN_LOGIN_FAILED, "at": started, "detail": str(exc)}
        _store_last_run(connection, result)
        _release_run_lock(connection)
        connection.commit()
        return result
    except Exception as exc:  # noqa: BLE001 — 수집 실패가 화면을 깨면 안 된다
        logger.warning("근태허가원 수집이 실패했습니다: %s", type(exc).__name__)
        result = {"status": RUN_ERROR, "at": started, "detail": type(exc).__name__}
        _store_last_run(connection, result)
        _release_run_lock(connection)
        connection.commit()
        return result

    outcome = upsert_batch(
        connection, items=harvest["items"], source="portal", collected_at=started
    )
    rejected = outcome["rejected"]
    result = {
        "status": RUN_OK,
        "at": started,
        "rows": harvest["rows"],
        "pages": harvest["pages"],
        "received": outcome["received"],
        "created": outcome["created"],
        "updated": outcome["updated"],
        "unchanged": outcome["unchanged"],
        # 포털 권한이 없어 본문을 못 연 문서 — 제목만 보고 값을 지어내지 않는다.
        "forbidden": harvest["forbidden"],
        # 필수 항목을 못 읽어 저장하지 않은 문서.
        "incomplete": harvest["incomplete"][:_MAX_REPORTED_REJECTS],
        "incomplete_total": len(harvest["incomplete"]),
        # 일부만 못 읽은 문서(저장은 됐다).
        "unresolved": harvest["unresolved"][:_MAX_REPORTED_REJECTS],
        "unresolved_total": len(harvest["unresolved"]),
        "rejected": rejected[:_MAX_REPORTED_REJECTS],
        "rejected_total": len(rejected),
        "errors": sorted(set(harvest["errors"]))[:5],
        "window_days": harvest["window_days"],
    }
    _store_last_run(connection, result)
    _release_run_lock(connection)
    connection.commit()
    return result


# ── 조회·대조(§5) ────────────────────────────────────────────────────────────


def month_approvals(
    connection: sqlite3.Connection, year_month: str
) -> list[dict[str, Any]]:
    """그 달에 걸치는 허가원 전부(시작일 순). 범위가 달을 넘나드는 문서도 잡는다."""
    first, last = month_bounds(year_month)
    rows = connection.execute(
        """
        SELECT doc_no, emp_name, emp_id, kind_raw, kind, half,
               start_date, end_date, status, drafted_at, title_raw,
               source, collected_at, updated_at
        FROM attendance_approvals
        WHERE start_date <= ? AND end_date >= ?
        ORDER BY start_date ASC, emp_name ASC, doc_no ASC
        """,
        (last, first),
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def _roster_indexes(
    roster: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_emp_id: dict[str, dict[str, Any]] = {}
    by_name: dict[str, list[dict[str, Any]]] = {}
    for person in roster:
        emp_id = normalize_emp_id(person.get("emp_id"))
        if emp_id and emp_id not in by_emp_id:
            by_emp_id[emp_id] = person
        name = _text(person.get("name"))
        if name:
            by_name.setdefault(name, []).append(person)
    return by_emp_id, by_name


def match_person(
    approval: dict[str, Any],
    by_emp_id: dict[str, dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, str]:
    """사람 맞추기(§4) — 사번이 있으면 사번, 없으면 이름 + 그 달 명단.

    동명이인은 맞추지 않고 미매칭으로 남긴다. 조용히 아무나에게 붙이면 그 사람의
    근태가 남의 결재로 해소된 것처럼 보인다.
    """
    emp_id = normalize_emp_id(approval.get("emp_id"))
    if emp_id:
        person = by_emp_id.get(emp_id)
        if person is not None:
            return person, "사번"
    candidates = by_name.get(_text(approval.get("emp_name")), [])
    if len(candidates) == 1:
        return candidates[0], "이름"
    if len(candidates) > 1:
        return None, "동명이인"
    return None, "명단에 없음"


def collection_status(
    connection: sqlite3.Connection, *, now: str | None = None
) -> dict[str, Any]:
    """수집 상태 한 줄(§5.4) — 마지막 수집 시각·총 건수·오래됐는지 + 지난 회차 결과.

    `configured` 는 포털 자격증명이 설정됐는지만 알린다. **아이디·비밀번호 자체는
    절대 싣지 않는다**(§1.3).
    """
    from .. import config

    last_at = _text(settings_service.get_setting(connection, LAST_RUN_SETTING_KEY))
    row = connection.execute(
        "SELECT COUNT(*) AS n, MAX(collected_at) AS newest FROM attendance_approvals"
    ).fetchone()
    total = int(row["n"] or 0)
    last_collected = last_at or _text(row["newest"])
    return {
        "last_collected_at": last_collected or None,
        "total": total,
        "stale": _is_stale(last_collected, now=now),
        "stale_after_days": STALE_AFTER_DAYS,
        "configured": config.portal_configured(),
        "window_days": config.PORTAL_WINDOW_DAYS,
        "last_run": last_run(connection),
    }


def _parse_stamp(text: str) -> datetime | None:
    value = _text(text)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_stale(last_collected: str, *, now: str | None = None) -> bool:
    """수집이 끊겼는가. 한 번도 안 돌았으면 '오래됨'으로 본다(있어야 할 것이 없다)."""
    stamp = _parse_stamp(last_collected)
    if stamp is None:
        return True
    reference = _parse_stamp(now or "") or datetime.now(timezone.utc)
    return (reference - stamp) > timedelta(days=STALE_AFTER_DAYS)


def build_month_view(
    connection: sqlite3.Connection,
    year_month: str,
    *,
    roster: list[dict[str, Any]],
    erp_rows: list[dict[str, Any]],
    now: str | None = None,
) -> dict[str, Any]:
    """책임자 월 화면(§5)이 쓰는 묶음 — 목록 + 대조 두 갈래 + 미매칭 + 수집 상태.

    `roster` 는 `attendance_excel.employee_list`, `erp_rows` 는
    `attendance_excel.month_employee_rows` 의 결과를 그대로 받는다(엑셀을 다시 읽지
    않는다). 엑셀을 못 읽은 달은 호출자가 빈 리스트를 넘기고, 그때 대조는 목록만
    보여준다 — 허가원이 있는데 엑셀이 없다고 "휴가 표시 없음"을 쏟아내면 안 된다.
    """
    first, last = month_bounds(year_month)
    approvals = month_approvals(connection, year_month)
    by_emp_id, by_name = _roster_indexes(roster)
    has_erp = bool(erp_rows)

    # ERP 월 행 색인: (사번, 날짜) → 휴가 표시 여부. 여러 소스 파일에 흩어진 같은
    # 날짜는 하나라도 휴가면 휴가로 본다.
    erp_leave: dict[tuple[str, str], bool] = {}
    erp_leave_text: dict[tuple[str, str], str] = {}
    erp_people: dict[str, dict[str, Any]] = {}
    for row in erp_rows:
        if _text(row.get("day_type")) not in RECONCILE_DAY_TYPES:
            continue
        emp_id = normalize_emp_id(row.get("emp_id"))
        row_date = _text(row.get("date"))
        if not emp_id or not row_date:
            continue
        key = (emp_id, row_date)
        leave = has_erp_leave(
            _text(row.get("day_type")),
            _text(row.get("attendance_code")),
            _text(row.get("note")),
        )
        erp_leave[key] = erp_leave.get(key, False) or leave
        if leave and key not in erp_leave_text:
            erp_leave_text[key] = " ".join(
                part
                for part in (
                    _text(row.get("attendance_code")),
                    _text(row.get("note")),
                )
                if part
            )
        erp_people.setdefault(
            emp_id,
            {
                "emp_id": emp_id,
                "name": _text(row.get("name")),
                "department": _text(row.get("department")),
            },
        )

    items: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    missing_in_erp: list[dict[str, Any]] = []
    approved_days: set[tuple[str, str]] = set()

    for approval in approvals:
        person, match_by = match_person(approval, by_emp_id, by_name)
        month_days = [
            day for day in covered_dates(approval["start_date"], approval["end_date"])
            if first <= day <= last
        ]
        item = {
            **approval,
            "matched_emp_id": normalize_emp_id(person.get("emp_id")) if person else None,
            "matched_name": (person or {}).get("name"),
            "department": (person or {}).get("department"),
            "match_by": match_by if person else None,
            "unmatched_reason": None if person else match_by,
            "month_dates": month_days,
        }
        items.append(item)

        if person is None:
            unmatched.append(
                {
                    "doc_no": approval["doc_no"],
                    "emp_name": approval["emp_name"],
                    "emp_id": approval["emp_id"],
                    "kind": approval["kind"],
                    "half": approval["half"],
                    "start_date": approval["start_date"],
                    "end_date": approval["end_date"],
                    "reason": match_by,
                }
            )
            continue

        matched_id = normalize_emp_id(person.get("emp_id"))
        for day in month_days:
            approved_days.add((matched_id, day))

        if not has_erp:
            continue
        # 엑셀에 행이 아예 없는 날(미래 날짜·비근무일)은 판정하지 않는다 — 대조는
        # '평일 근무일로 찍힌 날에 휴가 표시가 없다'만 센다.
        gaps = [
            day
            for day in month_days
            if (matched_id, day) in erp_leave and not erp_leave[(matched_id, day)]
        ]
        if gaps:
            missing_in_erp.append(
                {
                    "doc_no": approval["doc_no"],
                    "emp_id": matched_id,
                    "emp_name": person.get("name") or approval["emp_name"],
                    "department": person.get("department"),
                    "kind": approval["kind"],
                    "half": approval["half"],
                    "status": approval["status"],
                    "dates": gaps,
                }
            )

    missing_approval: list[dict[str, Any]] = []
    for (emp_id, row_date), leave in sorted(erp_leave.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        if not leave or (emp_id, row_date) in approved_days:
            continue
        person = erp_people.get(emp_id, {})
        missing_approval.append(
            {
                "emp_id": emp_id,
                "emp_name": person.get("name") or "",
                "department": person.get("department") or "",
                "date": row_date,
                "leave_text": erp_leave_text.get((emp_id, row_date), ""),
            }
        )

    return {
        "month": year_month,
        "items": items,
        "missing_in_erp": missing_in_erp,
        "missing_approval": missing_approval,
        "unmatched": unmatched,
        "erp_available": has_erp,
        "collection": collection_status(connection, now=now),
    }
