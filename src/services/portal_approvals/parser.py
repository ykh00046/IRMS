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

import datetime as _dt
import hashlib
import html as html_mod
import json
import re
from typing import Any, Optional

# 해석 규칙 판 번호. doc_hash 에 섞여 들어가므로, 제목·본문 해석을 고쳤을 때 이 값을
# 올리면 이미 적재된 문서도 수정본으로 보여 다시 읽고 다시 저장한다.
PARSER_VERSION = "5"

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
# 기간 칸의 시각 하나 — 연도는 있을 수도 없을 수도 있다. 한 문서 안에서 섞여 나온다:
# '09월03일09시부터~26년09월04일18시까지'(2026-09-28 실측). 연도 있는 것만 읽으면
# 앞쪽 날짜가 통째로 사라져 이틀 휴가가 하루로 저장된다.
_KOREAN_MD_RE = re.compile(
    r"(?:(?<!\d)(\d{2,4})\s*년\s*)?(?<!\d)(\d{1,2})\s*월\s*(\d{1,2})\s*일"
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
        # 양식 이름이 낀 토막이라도 그 말만 지우고 나머지에서 종류를 찾는다.
        # '반반차 허가원 - 원료생산 김**' 처럼 구분자 없이 한 토막인 제목이 실제로
        # 있고(2026-09-23 실측), 통째로 건너뛰면 바로 옆의 '반반차'를 놓친다.
        cleaned = part
        for word in _FORM_WORDS:
            cleaned = cleaned.replace(word, " ")
        cleaned = cleaned.strip()
        if not cleaned:
            continue
        found = detect_kind_token(cleaned)
        if found:
            # 토막이 원래 모양 그대로면 그 토막을 원문으로 둔다('오후반차' 처럼 앞뒤가
            # 뜻을 갖는다). 양식 이름을 걷어낸 토막은 찌꺼기가 남으므로 찾은 낱말만 쓴다.
            kind_idx = idx
            kind_raw = part if cleaned == part else found
            break

    if kind_raw is None and date_idx is not None:
        # 날짜 칸 안에 종류가 함께 적힌 형식 — `원료생산팀/김**/2026.09.23(반차)`.
        # 양식명으로 찾기 시작하면서(2026-09-23) 이 모양이 다수가 됐다. 날짜 칸을
        # 통째로 건너뛰면 그 전부가 '기타'로 접힌다. 여기서는 토큰 전체가 아니라
        # **찾은 낱말만** 원문으로 삼는다(날짜까지 종류 원문에 넣으면 읽기 나쁘다).
        found = detect_kind_token(parts[date_idx])
        if found:
            kind_raw = found

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
    """머리글에 그려진 문서번호·기안일자·기안자를 글자에서 읽는다(JSON 밖).

    본문 앞쪽에는 작성 요령 안내문이 먼저 나온다("문서 제목 작성시 ➡ 부서 / 성명 / …").
    글 전체에서 라벨을 찾으면 그 안내문이 먼저 걸려 문서제목이 '작성시' 가 된다
    (2026-09-23 실제 문서로 확인). 그래서 **머리글 표가 시작되는 '문서번호' 뒤**에서만 찾는다.
    기안자는 '박용재/ 원료생산팀' 처럼 슬래시와 공백을 포함하므로 줄 끝까지 읽는다.
    """
    plain = text_of(html)
    start = plain.find("문서번호")
    region = plain[start:] if start >= 0 else plain
    found: dict[str, str] = {}
    for label in _HEADER_LABELS:
        pattern = rf"{re.escape(label)}\s*[:：]?\s*(\S+)"
        if label == "기안자":
            # '박용재/ 원료생산팀' 처럼 이름 뒤에 부서가 붙는다. 평문은 칸이 하나로 눌리므로
            # 칸 수로는 끝을 못 찾는다. 슬래시 뒤 한 덩어리까지만 더 읽는다.
            pattern = re.escape(label) + r"\s*[:：]?\s*([^\s/]+(?:\s*/\s*\S+)?)"
        match = re.search(pattern, region)
        if match:
            found.setdefault(label.replace(" ", ""), match.group(1).strip())
    return found


# '(0.5일간)' · '(2일간)' · '(총 2일간)' — 괄호 안 말머리가 붙는 문서가 있다.
_DAYS_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*일\s*간")


def _fill_years(stamps: list[tuple[int | None, int, int, int | None]]) -> list[str | None]:
    """연도를 안 적은 시각에 가까운 연도를 채운다. 없으면 날짜를 비운 채 둔다.

    한 문서가 두 해에 걸치는 경우는 연말뿐이다. 채우고 나서 순서가 거꾸로 가면,
    **연도를 직접 적지 않은 쪽만** 한 해 밀어 바로잡는다 — 문서에 적힌 연도는
    건드리지 않는다.
    """
    years = [index for index, stamp in enumerate(stamps) if stamp[0] is not None]
    if not years:
        # 문서 어디에도 연도가 없다 — 지어내지 않는다(날짜는 제목에서 채운다).
        return [None] * len(stamps)

    filled: list[tuple[int, int, int, bool]] = []
    for index, (year, month, day, _hour) in enumerate(stamps):
        if year is not None:
            filled.append((year, month, day, True))
            continue
        nearest = min(years, key=lambda other: (abs(other - index), other < index))
        filled.append((stamps[nearest][0] or 0, month, day, False))

    for index in range(1, len(filled)):
        before = filled[index - 1]
        current = filled[index]
        if (current[0], current[1], current[2]) >= (before[0], before[1], before[2]):
            continue
        if not current[3]:      # 12월 → 1월: 뒤쪽이 연도를 안 적었으면 한 해 뒤로
            filled[index] = (current[0] + 1, current[1], current[2], False)
        elif not before[3]:     # 앞쪽이 연도를 안 적었으면 한 해 앞으로
            filled[index - 1] = (before[0] - 1, before[1], before[2], False)

    return [_fmt(year, month, day) for year, month, day, _written in filled]


def resolve_period_range(
    dates: list[str], days: float | None
) -> tuple[str | None, str | None, bool]:
    """기간의 첫날·마지막날을 정한다. 신청하지 않은 날을 만들지 않는다.

    반환 `(시작일, 종료일, 확인필요)`.

    - 적힌 일수가 1 이하인데 이틀에 걸쳐 있으면 **야간 근무**다
      ('19시부터 ~ 익일 07시까지(1일간)') — 첫날 하루로 본다.
    - 적힌 일수보다 첫날~마지막날 간격이 넓으면 날짜가 띄엄띄엄한 것이다
      ('9월 2일, 9월 5일 … (총 2일간)') — **첫날만** 남기고 확인 대상으로 올린다.
      가운데 날을 채우면 신청하지도 않은 휴가를 만들어 낸다.
    """
    known = [date for date in dates if date]
    if not known:
        return None, None, False
    first, last = known[0], known[-1]
    if first == last:
        return first, last, False
    span = (
        _dt.date.fromisoformat(last) - _dt.date.fromisoformat(first)
    ).days + 1
    if days is None:
        return first, last, False
    if days <= 1:
        return first, first, False
    if span > days:
        return first, first, True
    return first, last, False


def parse_period(period_text: str) -> dict[str, Any]:
    """'26년08월07일 13시부터 ~ 26년08월07일17시30분까지(0.5일간)' 를 읽는다.

    연도를 적은 시각과 안 적은 시각이 한 문서에 섞여 나온다 — **둘 다 순서대로**
    읽는다. 연도 있는 것만 읽으면 앞쪽 날짜가 사라져 이틀 휴가가 하루가 된다
    (2026-09-28 실측 88건 중 7건). 시작 시각은 **첫 시각**이다 — 마지막 시각을
    쓰면 반차의 오전/오후가 뒤집힌다.

    서식에는 늘 빈 두 번째 칸('00년 00월 00일 …')이 붙어 있어 0 값은 버린다.
    """
    text = period_text or ""
    raw: list[tuple[int | None, int, int, int | None]] = []
    for year, month, day, hour, _minute in _KOREAN_MD_RE.findall(text):
        if int(month) == 0 or int(day) == 0:
            continue
        if year and int(year) == 0:
            continue
        raw.append(
            (
                expand_year(year) if year else None,
                int(month),
                int(day),
                int(hour) if hour else None,
            )
        )

    days = None
    days_match = _DAYS_RE.search(text)
    if days_match and float(days_match.group(1)) > 0:
        days = float(days_match.group(1))

    if not raw:
        return {"start_date": None, "end_date": None, "start_hour": None,
                "end_hour": None, "days": days, "dates": [], "needs_review": False}

    stamp_dates = _fill_years(raw)
    ordered: list[str] = []
    for value in stamp_dates:
        if value and value not in ordered:
            ordered.append(value)
    start_date, end_date, needs_review = resolve_period_range(ordered, days)
    return {
        "start_date": start_date,
        "end_date": end_date,
        "start_hour": raw[0][3],
        "end_hour": raw[-1][3],
        "days": days,
        "dates": ordered,
        "needs_review": needs_review,
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
    period_review = bool(body.get("period_needs_review"))
    # 본문이 하루로 읽혔는데 제목이 범위를 말하고 적힌 일수도 2일 이상이면 제목을
    # 믿는다 — 본문 기간 칸의 앞쪽 시각이 지워진 문서가 실제로 있었다(2026-09-28).
    # 확인 대상으로 올린 기간(띄엄띄엄한 날짜)은 넓히지 않는다.
    stated_days = body.get("period_days")
    if (
        not period_review
        and start_date
        and start_date == end_date
        and parsed_title["start_date"]
        and parsed_title["end_date"]
        and parsed_title["end_date"] > parsed_title["start_date"]
        and (stated_days or 0) >= 2
    ):
        start_date = parsed_title["start_date"]
        end_date = parsed_title["end_date"]

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
    if period_review:
        # 날짜가 띄엄띄엄해 가운데 날을 알 수 없다 — 첫날만 저장하고 사람이 본다.
        unresolved.append("period")

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
    """저장 항목으로 접는다 — `unresolved` 목록을 한 줄 문자열로 바꾼다.

    못 읽은 칸이 무엇인지는 **저장해 둬야** 책임자 화면이 '확인 필요'로 띄울 수 있다
    (종류 미확인·기간 확인 필요). 수집 회차 요약에만 남기면 그 회차에 들어온 문서만
    보이고, 어제 들어온 문서는 화면에서 사라진다.
    """
    stored = dict(item)
    fields = stored.get("unresolved") or []
    stored["unresolved"] = ",".join(fields) if fields else None
    return stored
