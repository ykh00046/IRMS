"""포털 근태허가원 파싱 — 순수 함수만(네트워크 없음).

제목·기간·종류 해석 규칙은 별도 수집기
``C:\\X\\Server_API\\webcloring-pdf\\src\\services\\attendance_approval_parser.py``
에서 **옮겨 왔다**(그 프로젝트를 import 하지 않는다. 2026-09-22 실측 10건으로
검증된 규칙이라 다시 유도하지 않고 그대로 가져온다).

옮겨 온 규칙의 근거(원본 docstring 요약):

목록 행 컬럼
    No. | 문서번호 | 유형 | 분류 | 그룹사 여부 | 문서 제목 | 기안자 | 기안부서 | 완료일
    - 문서번호 앞 8자리가 기안일이다(관측 6건 모두 본문 기안일자와 일치).
    - 결재상태·기안일 칸은 없다. 완료일이 있으면 완료된 문서다(부서공개함).
    - **기안자 != 대상자**: 팀장이 팀원 몫을 기안한 건이 있다. 그래서 대상자는
      목록으로 채우지 못하고 본문을 열어야 한다.

문서 제목 형식은 고정이 아니다 — 관측된 7가지
    근태허가원/원료생산팀/박용재/반차/26.08.07
    원료생산팀/임현규/26.03.12/근태허가원/훈련
    원료생산팀/근태허가원/송보란/25.12.19~26.01.03/병가
    원료생산팀/강도윤/근태허가원              <- 종류·날짜 없음(파싱 불가)
    그래서 위치가 아니라 '토큰의 생김새'로 날짜·종류·이름을 찾는다.

본문(2026-09-22 조사로 바뀐 부분)
    브라우저 없이 받으면 innerText 가 아니라 HTML 이고, 그 안에 HTML 이스케이프된
    JSON 두 덩이가 들어 있다 — 서식(`{"name":"text6","title":"기간"}`)과
    값(`{"name":"text6","value":"26년08월07일 13시부터 ~ …"}`). `html.unescape`
    후 `name` 으로 맞물려 라벨→값을 만든다. 나머지 해석(기간·사유·오전/오후)은
    옮겨 온 규칙 그대로다.
"""

from __future__ import annotations

import hashlib
import html as html_mod
import json
import re
from typing import Any, Optional

# 해석 규칙 판 번호. doc_hash 에 섞여 들어가므로, 제목·본문 해석을 고쳤을 때 이 값을
# 올리면 이미 적재된 문서도 수정본으로 보여 다시 읽고 다시 저장한다.
PARSER_VERSION = "3"

# 제목에서 지우고 보는 말머리(양식 이름 자체).
_FORM_WORDS = ("근태허가원", "근태 허가원", "허가원")

# 종류 후보 토큰 — 관측값 + 근태 서식이 예시로 드는 말들. 순서가 곧 우선순위이며
# '반반차'가 '반차'보다 앞이어야 한다(부분 문자열 충돌).
_KIND_WORDS = (
    "반반차", "반차", "연차", "예비군", "훈련", "병가", "경조", "공가",
    "출장", "외근", "조퇴", "지각", "외출", "대체휴무", "보건휴가", "특별휴가",
    "휴가", "결근", "철야",
)

# 종류를 못 찾았을 때 쓰는 값. '그 밖'의 버킷이며 추측이 아니라 '모른다'는 뜻이다 —
# unresolved 에 kind 를 함께 남긴다.
UNKNOWN_KIND = "기타"

_DATE_SEP = r"[.\-/]"
# 26.08.07 / 2026.08.07 / 2026-08-07 / 26/08/07
_DATE_RE = re.compile(
    rf"(?<!\d)(\d{{2}}|\d{{4}}){_DATE_SEP}(\d{{1,2}}){_DATE_SEP}(\d{{1,2}})(?!\d)"
)
# 26년08월07일 13시30분 / 26년 06월08일 09시
_KOREAN_DT_RE = re.compile(
    r"(\d{2,4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
    r"(?:\s*(\d{1,2})\s*시)?(?:\s*(\d{1,2})\s*분)?"
)
_HANGUL_NAME_RE = re.compile(r"^[가-힣]{2,4}$")

# 사유 칸 뒤에 늘 따라붙는 서식 안내문. 여기에 '구분(연차,반차,반반차 등등)'이
# 들어 있어 그대로 두면 종류를 잘못 집는다(실측: 철야 근무 건이 '반반차'로 잡힘).
_BOILERPLATE_MARKERS = (
    "문서 제목 작성시",
    "문서제목 작성시",
    "제목 작성 양식",
    "구분(연차",
    "사이에 근태",
)
# 부서로 보이는 토큰(이름 후보에서 제외)
_DEPT_HINT_RE = re.compile(r"(팀|탐|부서|파트|실|과|본부|공장|생산)$")

# 세션이 끊기면 200 으로 로그인 화면이 돌아온다 — 본문·목록 양쪽에서 이걸로 가린다.
_LOGIN_MARKERS = ('id="loginForm"', 'name="j_password"')
# 남의 부서 문서는 500 + 이 문구다(전송 문제가 아니라 포털 권한).
PERMISSION_MARKERS = ("접근 권한이 없습니다", "접근권한이 없습니다")


# ==========================================================
# 날짜
# ==========================================================
def expand_year(raw: str) -> int:
    """'26' -> 2026, '2026' -> 2026. 두 자리 연도는 2000년대로 본다."""
    value = int(raw)
    return value if value >= 1000 else 2000 + value


def _fmt(year: int, month: int, day: int) -> Optional[str]:
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def normalize_date(token: str) -> Optional[str]:
    """토큰에서 첫 날짜 하나를 YYYY-MM-DD 로 뽑는다. 없으면 None."""
    if not token:
        return None
    match = _DATE_RE.search(token)
    if match:
        year, month, day = match.groups()
        return _fmt(expand_year(year), int(month), int(day))
    match = _KOREAN_DT_RE.search(token)
    if match:
        year, month, day = match.group(1), match.group(2), match.group(3)
        if int(month) == 0 or int(day) == 0:
            return None
        return _fmt(expand_year(year), int(month), int(day))
    return None


def normalize_date_range(token: str) -> tuple[Optional[str], Optional[str]]:
    """'25.12.19~26.01.03' -> (2025-12-19, 2026-01-03). 하나면 (start, start)."""
    if not token:
        return None, None
    dates = [
        _fmt(expand_year(y), int(m), int(d))
        for y, m, d in _DATE_RE.findall(token)
    ]
    dates = [d for d in dates if d]
    if not dates:
        single = normalize_date(token)
        return (single, single) if single else (None, None)
    return dates[0], dates[-1]


def strip_boilerplate(text: str) -> str:
    """서식 안내문을 잘라낸다. 사유에서 종류 낱말을 찾기 전에 반드시 거친다."""
    value = (text or "").strip()
    cut = len(value)
    for marker in _BOILERPLATE_MARKERS:
        found = value.find(marker)
        if found != -1:
            cut = min(cut, found)
    return value[:cut].strip()


def drafted_at_from_doc_no(doc_no: str) -> Optional[str]:
    """문서번호 앞 8자리(20260805P227-0040)를 기안일로 읽는다."""
    digits = (doc_no or "").strip()[:8]
    if len(digits) != 8 or not digits.isdigit():
        return None
    return _fmt(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))


# ==========================================================
# 종류 / 오전·오후
# ==========================================================
def detect_kind_token(text: str) -> Optional[str]:
    """문자열에 근태 종류 낱말이 있으면 그 낱말을 반환한다(원문 판정용)."""
    if not text:
        return None
    for word in _KIND_WORDS:
        if word in text:
            return word
    return None


def normalize_kind(raw: str, context: str = "") -> str:
    """최종 5종으로 접는다. '훈련'은 사유에 예비군이 있을 때만 예비군(추측 금지).

    BRM 저장 측(services/attendance_approvals.normalize_kind)도 같은 표를 쓰지만,
    여기서는 오전/오후 판정과 unresolved 계산에 필요해 함께 둔다.
    """
    text = (raw or "").strip()
    if "반반차" in text:
        return "반반차"
    if "반차" in text:
        return "반차"
    if "연차" in text:
        return "연차"
    if "예비군" in text:
        return "예비군"
    if "훈련" in text and "예비군" in (context or ""):
        return "예비군"
    return UNKNOWN_KIND


def detect_half(*texts: str) -> Optional[str]:
    """글에 '오전'/'오후'가 적혀 있으면 그것을 그대로 쓴다."""
    for text in texts:
        if not text:
            continue
        if "오전" in text:
            return "오전"
        if "오후" in text:
            return "오후"
    return None


def half_from_hour(kind: str, start_hour: Optional[int]) -> Optional[str]:
    """반차·반반차의 시작 시각으로 오전/오후를 읽는다(13시부터 -> 오후).

    문서에 사실로 적힌 시각을 읽는 것이며, 연차·예비군 등 하루 단위 종류에는
    적용하지 않는다.
    """
    if start_hour is None:
        return None
    if normalize_kind(kind) not in ("반차", "반반차"):
        return None
    return "오후" if start_hour >= 12 else "오전"


# ==========================================================
# 제목
# ==========================================================
def split_title_parts(title: str) -> list[str]:
    """'/' 로 끊고 공백을 정리한다. 양식 이름(근태허가원)은 남겨둔다."""
    if not title:
        return []
    return [part.strip() for part in title.split("/") if part.strip()]


def parse_title(title: str) -> dict[str, Any]:
    """제목에서 읽히는 것만 뽑는다. 못 읽은 것은 unresolved 에 남긴다.

    제목 형식이 사람마다 달라(관측 7종) 위치를 믿지 않는다. 날짜처럼 생긴 토큰,
    종류 낱말을 포함한 토큰, 남은 한글 2~4자 토큰(이름) 순으로 찾는다.
    """
    raw = (title or "").strip()
    parts = split_title_parts(raw)

    date_idx, start_date, end_date = None, None, None
    for idx, part in enumerate(parts):
        if any(word in part for word in _FORM_WORDS):
            continue  # '근태허가원' 자체는 날짜가 아니다
        first, last = normalize_date_range(part)
        if first:
            date_idx, start_date, end_date = idx, first, last
            break

    kind_idx, kind_raw = None, None
    for idx, part in enumerate(parts):
        if idx == date_idx:
            continue
        if any(word in part for word in _FORM_WORDS):
            continue
        if detect_kind_token(part):
            kind_idx, kind_raw = idx, part
            break

    emp_name = None
    for idx, part in enumerate(parts):
        if idx in (date_idx, kind_idx):
            continue
        if any(word in part for word in _FORM_WORDS):
            continue
        if _DEPT_HINT_RE.search(part):
            continue
        if _HANGUL_NAME_RE.match(part):
            emp_name = part
            break

    unresolved = []
    if not emp_name:
        unresolved.append("emp_name")
    if not kind_raw:
        unresolved.append("kind")
    if not start_date:
        unresolved.append("start_date")

    return {
        "title_raw": raw,
        "emp_name": emp_name,
        "kind_raw": kind_raw,
        "half": detect_half(raw),
        "start_date": start_date,
        "end_date": end_date,
        "unresolved": unresolved,
    }


# ==========================================================
# HTML
# ==========================================================
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.S | re.I)


def text_of(fragment: str) -> str:
    """HTML 조각에서 사람이 읽는 글자만 남긴다(태그 제거 + 엔티티 복원 + 공백 정리)."""
    without_script = _SCRIPT_RE.sub(" ", fragment or "")
    plain = _TAG_RE.sub(" ", without_script)
    return re.sub(r"\s+", " ", html_mod.unescape(plain)).strip()


def looks_like_login_page(html: str) -> bool:
    """세션이 끊겨 로그인 화면이 돌아왔는가(200 이라도 내용이 로그인 폼)."""
    body = html or ""
    return any(marker in body for marker in _LOGIN_MARKERS)


def looks_like_permission_denied(html: str) -> bool:
    """남의 부서 문서 — 포털 권한 문제지 전송 문제가 아니다."""
    body = html or ""
    return any(marker in body for marker in PERMISSION_MARKERS)


# 행은 여는 태그까지 통째로 잡는다 — apprId 가 `<tr onclick="…">` 속성에 있어서,
# 안쪽 내용만 캡처하면 본문 조회 키를 통째로 놓친다.
_ROW_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_APPR_ID_RE = re.compile(r"getApprDetail\(\s*['\"]([^'\"]+)['\"]")
_DOC_NO_RE = re.compile(r"\d{8}[A-Za-z][A-Za-z0-9]*-\d+")

# 목록 열 순서(2026-09-22 실측). 자리를 믿되, 문서번호는 생김새로 다시 확인한다 —
# 포털이 열을 하나 끼워 넣어도 조용히 엉뚱한 칸을 문서번호로 읽지 않게.
_COL_DOC_NO = 1
_COL_TITLE = 5
_COL_DRAFTER = 6
_COL_DEPT = 7
_COL_END_DATE = 8


def parse_list_rows(html: str) -> list[dict[str, Any]]:
    """목록 HTML 의 `<table id="listTable">` 에서 행을 읽는다.

    반환: [{"no", "doc_no", "title", "drafter", "dept", "end_date", "appr_id",
            "cells"}]. `appr_id` 가 본문 조회 키이고 `doc_no` 와 다르다.
    머리글 행(문서번호처럼 생긴 칸이 없는 행)은 건너뛴다.
    """
    body = html or ""
    start = body.find('id="listTable"')
    if start != -1:
        end = body.find("</table>", start)
        body = body[start: end if end != -1 else len(body)]

    rows: list[dict[str, Any]] = []
    for row_html in _ROW_RE.findall(body):
        cells = [text_of(cell) for cell in _CELL_RE.findall(row_html)]
        if not cells:
            continue
        doc_no = ""
        if len(cells) > _COL_DOC_NO and _DOC_NO_RE.fullmatch(cells[_COL_DOC_NO]):
            doc_no = cells[_COL_DOC_NO]
        else:  # 열이 밀렸을 때의 안전판 — 생김새로 찾는다
            for cell in cells:
                found = _DOC_NO_RE.search(cell)
                if found:
                    doc_no = found.group(0)
                    break
        if not doc_no:
            continue  # 머리글·빈 행
        appr_match = _APPR_ID_RE.search(row_html)
        rows.append(
            {
                "no": cells[0] if cells else "",
                "doc_no": doc_no,
                "title": cells[_COL_TITLE] if len(cells) > _COL_TITLE else "",
                "drafter": cells[_COL_DRAFTER] if len(cells) > _COL_DRAFTER else "",
                "dept": cells[_COL_DEPT] if len(cells) > _COL_DEPT else "",
                "end_date": cells[_COL_END_DATE] if len(cells) > _COL_END_DATE else "",
                "appr_id": appr_match.group(1) if appr_match else "",
                "cells": cells,
            }
        )
    return rows


_JSON_OBJ_RE = re.compile(r'\{[^{}]*"name"\s*:\s*"[^"]*"[^{}]*\}')


def parse_body_labels(html: str) -> dict[str, str]:
    """본문 HTML 의 서식·값 JSON 두 덩이를 `name` 으로 맞물려 라벨→값으로 만든다.

    페이지 어디에 담겨 있든(숨은 input·스크립트 변수) 찾을 수 있도록, 통째로
    `html.unescape` 한 뒤 `"name"` 을 가진 평평한 JSON 객체를 모두 훑는다.
    """
    unescaped = html_mod.unescape(html or "")
    titles: dict[str, str] = {}
    values: dict[str, str] = {}
    for chunk in _JSON_OBJ_RE.findall(unescaped):
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name") or "").strip()
        if not name:
            continue
        title = obj.get("title")
        if isinstance(title, str) and title.strip():
            titles.setdefault(name, title.strip())
        value = obj.get("value")
        if isinstance(value, (str, int, float)) and str(value).strip():
            values.setdefault(name, str(value).strip())

    labels: dict[str, str] = {}
    for name, title in titles.items():
        if name in values:
            labels.setdefault(title, values[name])
    return labels


_HEADER_LABELS = ("문서번호", "기안일자", "기안자", "문서제목", "문서 제목")


def parse_header_fields(html: str) -> dict[str, str]:
    """머리글에 그려진 문서번호·기안일자·기안자를 글자에서 읽는다(JSON 밖)."""
    plain = text_of(html)
    found: dict[str, str] = {}
    for label in _HEADER_LABELS:
        match = re.search(rf"{re.escape(label)}\s*[:：]?\s*(\S+)", plain)
        if match:
            found.setdefault(label.replace(" ", ""), match.group(1))
    return found


def parse_period(period_text: str) -> dict[str, Any]:
    """'26년08월07일 13시부터 ~ 26년08월07일17시30분까지(0.5일간)' 를 읽는다.

    서식에는 늘 빈 두 번째 칸('00년 00월 00일 …')이 붙어 있어 0 값은 버린다.
    """
    text = period_text or ""
    stamps = []
    for year, month, day, hour, _minute in _KOREAN_DT_RE.findall(text):
        if int(month) == 0 or int(day) == 0 or int(year) == 0:
            continue
        date = _fmt(expand_year(year), int(month), int(day))
        if not date:
            continue
        stamps.append((date, int(hour) if hour else None))

    days = None
    days_match = re.search(r"\(\s*(\d+(?:\.\d+)?)\s*일간\s*\)", text)
    if days_match and float(days_match.group(1)) > 0:
        days = float(days_match.group(1))

    if not stamps:
        return {"start_date": None, "end_date": None,
                "start_hour": None, "end_hour": None, "days": days}
    return {
        "start_date": stamps[0][0],
        "end_date": stamps[-1][0],
        "start_hour": stamps[0][1],
        "end_hour": stamps[-1][1],
        "days": days,
    }


def parse_body(html: str) -> dict[str, Any]:
    """본문 HTML 에서 대상자·사번·기간·사유를 읽는다.

    목록으로는 채울 수 없는 값(대상자·사번·기간·오전/오후)이 여기 있다.
    """
    labels = parse_body_labels(html)
    header = parse_header_fields(html)

    emp_id = (labels.get("사번") or "").strip()
    if emp_id and not re.fullmatch(r"\d{4,}", emp_id):
        emp_id = ""

    emp_name = (labels.get("성명") or "").strip()
    if emp_name and not _HANGUL_NAME_RE.match(emp_name):
        emp_name = ""

    period_text = labels.get("기간") or ""
    period = parse_period(period_text)
    # 안내문을 떼어내야 종류 판정이 서식 예시('구분(연차,반차,반반차 등등)')에
    # 오염되지 않는다.
    reason = strip_boilerplate(labels.get("사유") or "")

    return {
        "emp_name": emp_name or None,
        "emp_id": emp_id or None,
        "dept": labels.get("소속"),
        "position": labels.get("직위"),
        "drafted_at": normalize_date(header.get("기안일자") or ""),
        "doc_no": header.get("문서번호"),
        "title": header.get("문서제목"),
        "reason": reason,
        "period_text": period_text or None,
        **{f"period_{key}": value for key, value in period.items()},
    }


# ==========================================================
# 항목 조립
# ==========================================================
def content_hash(fields: list[Any]) -> str:
    """수정본 감지 해시 — 목록 행 값으로 계산한다.

    결재가 다시 완료되면 완료일이 바뀌어 해시가 바뀌고, 그때 다시 저장된다.
    PARSER_VERSION 을 함께 넣어, 해석 규칙을 고치면 이미 적재된 문서도 해시가
    달라져 다시 읽힌다 — 그러지 않으면 고친 해석이 영원히 반영되지 않는다.
    """
    content = str(
        sorted([str(field) for field in fields] + [f"parser={PARSER_VERSION}"])
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(content).hexdigest()[:16]}"


def build_item(
    *,
    doc_no: str,
    title_raw: str,
    doc_hash: str,
    status: Optional[str] = None,
    drafted_at: Optional[str] = None,
    body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """목록 값 + (있으면) 본문 값을 저장 항목 하나로 합친다.

    목록에서 읽히는 값(문서번호·제목·기안일)을 먼저 채우고, 목록에 없는 값
    (대상자·사번·기간·오전/오후)만 본문에서 채운다. 대상자는 목록의 기안자와 다를
    수 있으므로 본문 성명 > 제목 이름 순이다.

    반환에는 진단용 ``unresolved`` 가 함께 들어 있다(저장 전에 떼어낸다).
    """
    parsed_title = parse_title(title_raw)
    body = body or {}

    emp_name = body.get("emp_name") or parsed_title["emp_name"]
    start_date = body.get("period_start_date") or parsed_title["start_date"]
    end_date = (
        body.get("period_end_date")
        or parsed_title["end_date"]
        or start_date
    )

    kind_from_title = parsed_title["kind_raw"]
    # 제목에 종류가 없으면 사유에서 낱말을 찾는다(원문 보존). 찾아 쓰더라도
    # '제목이 종류를 말하지 않았다'는 사실은 unresolved 로 그대로 남긴다.
    kind_raw = kind_from_title or detect_kind_token(body.get("reason") or "")
    kind_out = kind_raw or UNKNOWN_KIND

    half = detect_half(title_raw, body.get("period_text") or "")
    if not half:
        half = half_from_hour(kind_out, body.get("period_start_hour"))

    unresolved: list[str] = []
    if not emp_name:
        unresolved.append("emp_name")
    if not kind_from_title:
        unresolved.append("kind")
    if not start_date:
        unresolved.append("start_date")
    if normalize_kind(kind_out, body.get("reason") or "") in ("반차", "반반차") and not half:
        unresolved.append("half")

    return {
        "doc_no": doc_no,
        "emp_name": emp_name,
        "emp_id": body.get("emp_id"),
        "kind": kind_out,
        "half": half,
        "start_date": start_date,
        "end_date": end_date,
        "status": status,
        "drafted_at": body.get("drafted_at") or drafted_at,
        "title_raw": (title_raw or "").strip(),
        "doc_hash": doc_hash,
        "unresolved": unresolved,
    }


REQUIRED_FIELDS = ("doc_no", "emp_name", "kind", "start_date")


def missing_required(item: dict[str, Any]) -> list[str]:
    """저장 필수 항목 검사. 빈 리스트면 저장 가능."""
    missing = []
    for field in REQUIRED_FIELDS:
        value = item.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(field)
    return missing


def strip_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    """진단용 키(unresolved)를 떼고 저장 필드만 남긴다."""
    return {key: value for key, value in item.items() if key != "unresolved"}
