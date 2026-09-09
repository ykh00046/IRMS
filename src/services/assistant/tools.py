"""AI 도우미가 부르는 읽기 전용 도구 함수.

이 파일은 **새 SQL 을 쓰지 않는다**. 화면이 이미 쓰는 서비스 함수를 그대로 부르고
결과만 작게 추린다. 조회 경로가 두 벌이 되면 화면과 도우미의 숫자가 갈라지기 때문이다.

규칙:
- 인자는 평범한 타입(str/int)만 쓴다. Gemini SDK 가 **런타임 타입 힌트**를 읽어
  함수 호출 스키마를 만들기 때문에 ``from __future__ import annotations`` 를 쓰면 안 된다.
- 반환은 JSON 으로 바로 나가는 dict 다. 키는 한국어, 무게는 g/kg 를 이름에 적는다.
- 예외를 밖으로 던지지 않는다. 실패는 ``{"error": "..."}`` 로 돌려준다.
- 같은 (이름, 인자) 조회는 300초 동안 캐시한다. 되돌려 줄 때는 깊은 사본이라
  호출한 쪽이 값을 고쳐도 캐시가 오염되지 않는다.
"""

import copy
import logging
import sqlite3
import threading
import time
from datetime import date
from typing import Any

from ...db import get_connection
from .. import blend_service, erp_lot_service, lot_history_service, viscosity_service
from . import guide as guide_book

_logger = logging.getLogger(__name__)

CACHE_TTL_SEC = 300
_cache: dict[tuple, tuple[float, Any]] = {}
_cache_lock = threading.Lock()

# 도구 실행을 화면에 알리는 갈고리. 스트림 계층이 요청마다 꽂고 뺀다.
# 도구는 LLM SDK 와 같은 작업 스레드에서 돌기 때문에 thread-local 이면 충분하다.
_local = threading.local()

TOOL_LABELS = {
    "get_blend_summary": "배합 요약",
    "get_recent_records": "최근 배합 기록",
    "get_record": "배합 기록 상세",
    "get_viscosity_status": "점도 현황",
    "get_lot_changes": "자재 LOT 교체",
    "get_material_usage": "자재 사용량",
    "get_recipe": "레시피",
    "get_attention": "지금 조치",
    "get_usage_guide": "사용법 안내",
}


def set_emitter(emitter) -> None:
    """도구 실행 알림 갈고리 등록/해제. ``emitter(name, status)`` 꼴."""
    _local.emitter = emitter


def _notify(name: str, status: str) -> None:
    emitter = getattr(_local, "emitter", None)
    if emitter is None:
        return
    try:
        emitter(name, status)
    except Exception:  # noqa: BLE001 - 알림 실패가 조회를 막으면 안 된다
        _logger.debug("[assistant] tool notify 실패 name=%s", name, exc_info=True)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cache_get(key: tuple) -> Any:
    with _cache_lock:
        cached = _cache.get(key)
        if cached is None:
            return None
        stored_at, value = cached
        if time.time() - stored_at > CACHE_TTL_SEC:
            _cache.pop(key, None)
            return None
        return copy.deepcopy(value)


def _cache_set(key: tuple, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), copy.deepcopy(value))


def _guard(name: str, key_args: tuple, worker) -> dict:
    """캐시 → 실행 → 알림 → 예외 삼킴을 한 자리에 모은다."""
    key = (name,) + key_args
    _notify(name, "running")
    cached = _cache_get(key)
    if cached is not None:
        _notify(name, "done")
        return cached
    try:
        with get_connection() as connection:
            result = worker(connection)
    except Exception as exc:  # noqa: BLE001 - 도구는 절대 밖으로 던지지 않는다
        _logger.warning("[assistant] %s 실패: %s", name, exc, exc_info=True)
        _notify(name, "error")
        return {"error": f"{TOOL_LABELS.get(name, name)}을 읽지 못했습니다."}
    if isinstance(result, dict) and result.get("error"):
        _notify(name, "error")
        return result
    _cache_set(key, result)
    _notify(name, "done")
    return result


# ============================================================
# 공통 헬퍼
# ============================================================
def _today() -> str:
    return date.today().isoformat()


def _clean_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


def _range(date_from: str, date_to: str) -> tuple[str, str]:
    """비면 오늘로 채운다. 뒤집혀 오면 바로잡는다."""
    start = _clean_date(date_from) or _today()
    end = _clean_date(date_to) or start
    if start > end:
        start, end = end, start
    return start, end


def _g_to_kg(value: Any) -> float:
    try:
        return round(float(value or 0) / 1000.0, 2)
    except (TypeError, ValueError):
        return 0.0


def _num(value: Any, digits: int = 2) -> float:
    try:
        return round(float(value or 0), digits)
    except (TypeError, ValueError):
        return 0.0


def _match_product(needle: str, candidates: list[str]) -> str:
    """대소문자·공백을 무시하고 제품 이름을 맞춘다. 없으면 빈 문자열."""
    target = (needle or "").strip().casefold()
    if not target:
        return ""
    for name in candidates:
        if (name or "").strip().casefold() == target:
            return name
    for name in candidates:
        if target in (name or "").strip().casefold():
            return name
    return ""


# ============================================================
# 1. 배합 요약
# ============================================================
def get_blend_summary(date_from: str = "", date_to: str = "") -> dict:
    """기간별 배합 실적 요약을 돌려준다.

    건수, 총 배합량(kg), 제품별 건수, 작업자별 건수를 한 번에 본다.
    "이번 주 몇 건 했나", "오늘 총량", "제품별로 얼마나" 같은 물음에 쓴다.

    Args:
        date_from: 시작일 YYYY-MM-DD. 비우면 오늘.
        date_to: 종료일 YYYY-MM-DD. 비우면 시작일과 같은 날.
    """
    start, end = _range(date_from, date_to)

    def _work(connection: sqlite3.Connection) -> dict:
        data = blend_service.analysis(connection, start_date=start, end_date=end)
        summary = data.get("summary") or {}
        products = [
            {
                "제품": row.get("product_name"),
                "건수": int(row.get("batch_count") or 0),
                "총량_kg": _g_to_kg(row.get("total_amount")),
            }
            for row in (data.get("products") or [])[:15]
        ]
        workers = [
            {
                "작업자": row.get("worker"),
                "건수": int(row.get("records") or 0),
                "총량_kg": _g_to_kg(row.get("total_amount")),
            }
            for row in (data.get("workers") or [])[:15]
        ]
        return {
            "기간": {"시작일": start, "종료일": end},
            "배합_건수": int(summary.get("records") or 0),
            "총_배합량_kg": _g_to_kg(summary.get("total_weight_g")),
            "제품_수": int(summary.get("product_count") or 0),
            "자재_수": int(summary.get("material_count") or 0),
            "제품별": products,
            "작업자별": workers,
        }

    return _guard("get_blend_summary", (start, end), _work)


# ============================================================
# 2. 최근 배합 기록
# ============================================================
def get_recent_records(product: str = "", limit: int = 10) -> dict:
    """최근 배합 기록 목록을 돌려준다.

    LOT, 작업일, 작업자, 총량, 점도 측정 여부를 본다.
    "최근에 뭐 했나", "PB 마지막 배합" 같은 물음에 쓴다.

    Args:
        product: 반제품 이름. 비우면 전체.
        limit: 최대 건수. 1~30, 기본 10.
    """
    try:
        count = max(1, min(30, int(limit or 10)))
    except (TypeError, ValueError):
        count = 10
    needle = (product or "").strip()

    def _work(connection: sqlite3.Connection) -> dict:
        rows = blend_service.list_blend_records(
            connection, product=needle or None, limit=count
        )
        items = []
        for row in rows[:count]:
            readings = viscosity_service.list_readings_for_blend(
                connection, int(row.get("id") or 0)
            )
            items.append(
                {
                    "LOT": row.get("product_lot"),
                    "제품": row.get("product_name"),
                    "작업일": row.get("work_date"),
                    "작업자": row.get("worker"),
                    "총량_g": _num(row.get("total_amount")),
                    "점도_측정": bool(readings),
                    "상태": row.get("status"),
                    "반응기": row.get("reactor"),
                }
            )
        return {
            "조회_제품": needle or "전체",
            "건수": len(items),
            "기록": items,
        }

    return _guard("get_recent_records", (needle, count), _work)


# ============================================================
# 3. 기록 상세
# ============================================================
def get_record(product_lot: str) -> dict:
    """배합 LOT 하나의 상세를 돌려준다.

    자재별 LOT, 이론량, 실제량, 편차와 점도 측정값을 본다.
    "이 LOT 뭐 들어갔나", "편차 얼마" 같은 물음에 쓴다.

    Args:
        product_lot: 배합 LOT 번호. 예 "PB-260901-1".
    """
    lot = (product_lot or "").strip()
    if not lot:
        return {"error": "LOT 번호가 필요합니다."}

    def _work(connection: sqlite3.Connection) -> dict:
        candidates = blend_service.list_blend_records(
            connection, search=lot, limit=20, include_canceled=True
        )
        target = None
        for row in candidates:
            if (row.get("product_lot") or "").strip().casefold() == lot.casefold():
                target = row
                break
        if target is None and candidates:
            target = candidates[0]
        if target is None:
            return {"error": f"LOT {lot} 을 찾지 못했습니다."}

        record = blend_service.get_blend_record(connection, int(target["id"]))
        if record is None:
            return {"error": f"LOT {lot} 을 찾지 못했습니다."}

        readings = viscosity_service.list_readings_for_blend(
            connection, int(target["id"])
        )
        variance = record.get("variance") or {}
        materials = [
            {
                "자재": item.get("material_name"),
                "자재_LOT": item.get("material_lot"),
                "이론량_g": _num(item.get("theory_amount")),
                "실제량_g": _num(item.get("actual_amount")),
                "편차_g": _num(item.get("variance")),
            }
            for item in (record.get("details") or [])
        ]
        return {
            "LOT": record.get("product_lot"),
            "제품": record.get("product_name"),
            "작업일": record.get("work_date"),
            "작업자": record.get("worker"),
            "총량_g": _num(record.get("total_amount")),
            "상태": record.get("status"),
            "자재": materials,
            "편차_합계_g": _num(variance.get("net_variance")),
            "점도": [
                {
                    "점도": _num(r.get("viscosity")),
                    "측정일": r.get("measured_date"),
                    "자재_LOT": r.get("material_lot"),
                }
                for r in readings
            ],
        }

    return _guard("get_record", (lot,), _work)


# ============================================================
# 4. 점도 현황
# ============================================================
def get_viscosity_status(product: str) -> dict:
    """반제품 하나의 점도 현황을 돌려준다.

    최근값, 중심선과 관리한계, 이상·경고 건수, 규격과 경고 문턱을 본다.
    "PB 점도 괜찮나", "요즘 점도 어떤가" 같은 물음에 쓴다.

    Args:
        product: 반제품 코드나 이름. 예 "PB", "APB", "APB17", "CSPB".
    """
    code = (product or "").strip()
    if not code:
        return {"error": "반제품 이름이 필요합니다."}

    def _work(connection: sqlite3.Connection) -> dict:
        target = viscosity_service.get_product_by_code(connection, code)
        if target is None:
            products = viscosity_service.list_products(connection)
            names = [p.get("code") or "" for p in products] + [
                p.get("name") or "" for p in products
            ]
            matched = _match_product(code, names)
            for candidate in products:
                if matched and matched in (candidate.get("code"), candidate.get("name")):
                    target = candidate
                    break
        if target is None:
            return {"error": f"{code} 반제품을 찾지 못했습니다."}

        data = viscosity_service.analyze_product(connection, target)
        stats = data.get("stats") or {}
        counts = data.get("counts") or {}
        info = data.get("product") or {}
        readings = data.get("readings") or []
        latest = None
        for row in readings:
            if latest is None or (row.get("measured_date") or "") >= (
                latest.get("measured_date") or ""
            ):
                latest = row

        return {
            "반제품": info.get("code") or target.get("code"),
            "이름": info.get("name") or target.get("name"),
            "최근값": _num(latest.get("viscosity")) if latest else None,
            "최근_측정일": latest.get("measured_date") if latest else None,
            "측정_건수": int(stats.get("n") or 0),
            "중심": _num(stats.get("center")),
            "표준편차": _num(stats.get("std")),
            "관리상한": _num(stats.get("ucl")),
            "관리하한": _num(stats.get("lcl")),
            "이상_건수": int(counts.get("anomaly") or 0),
            "경고_건수": int(counts.get("warn") or 0),
            "규격": {
                "목표": info.get("target"),
                "하한": info.get("lower_limit"),
                "상한": info.get("upper_limit"),
            },
            "경고_문턱": {
                "낮음": info.get("warn_low"),
                "높음": info.get("warn_high"),
            },
        }

    return _guard("get_viscosity_status", (code,), _work)


# ============================================================
# 5. 자재 LOT 교체
# ============================================================
def get_lot_changes(
    recipe: str = "", material: str = "", date_from: str = "", date_to: str = ""
) -> dict:
    """자재 LOT 이 바뀐 지점 목록을 돌려준다.

    언제, 어느 배합에서 어떤 자재 LOT 이 새 LOT 으로 넘어갔는지 본다.
    "이 자재 언제 바뀌었나", "LOT 교체 이력" 같은 물음에 쓴다.

    Args:
        recipe: 레시피나 반제품 이름. 비우면 전체.
        material: 자재 이름. 비우면 전체.
        date_from: 시작일 YYYY-MM-DD. 비우면 제한 없음.
        date_to: 종료일 YYYY-MM-DD. 비우면 제한 없음.
    """
    recipe_name = (recipe or "").strip()
    material_name = (material or "").strip()
    start = _clean_date(date_from)
    end = _clean_date(date_to)

    def _work(connection: sqlite3.Connection) -> dict:
        family_key = None
        if not recipe_name and not material_name:
            # 이력 조회는 레시피나 자재 하나를 골라야 한다(서비스 제약). 오류 대신
            # 고를 수 있는 목록과 화면을 돌려줘 모델이 되묻거나 메뉴로 안내하게 한다.
            families = lot_history_service.list_families(connection)
            labels = [item.get("label") or "" for item in families.get("items") or []]
            return {
                "안내": "레시피나 자재를 정하면 LOT 교체 목록을 보여 드립니다.",
                "레시피_목록": [x for x in labels if x][:12],
                "화면": {"메뉴": "LOT 이력", "경로": "/lot-history",
                         "설명": "레시피나 자재를 고르면 LOT 교체 시점과 역추적을 볼 수 있습니다."},
            }
        if recipe_name:
            families = lot_history_service.list_families(connection)
            labels = [item.get("label") or "" for item in families.get("items") or []]
            matched = _match_product(recipe_name, labels)
            for item in families.get("items") or []:
                if matched and item.get("label") == matched:
                    family_key = item.get("key")
                    break
            if family_key is None:
                return {"error": f"{recipe_name} 레시피를 찾지 못했습니다."}

        data = lot_history_service.lot_history(
            connection,
            family=family_key,
            material=material_name or None,
            start_date=start or None,
            end_date=end or None,
        )
        changes = [
            {
                "작업일": row.get("work_date"),
                "레시피": row.get("family_label"),
                "자재": row.get("material_name"),
                "이전_LOT": row.get("prev_lot"),
                "새_LOT": row.get("new_lot"),
                "배합_LOT": row.get("product_lot"),
                "작업자": row.get("worker"),
            }
            for row in (data.get("changes") or [])[:40]
        ]
        return {
            "레시피": recipe_name or "전체",
            "자재": material_name or "전체",
            "기간": {"시작일": start or None, "종료일": end or None},
            "교체_건수": int(data.get("change_count") or 0),
            "교체": changes,
        }

    return _guard(
        "get_lot_changes", (recipe_name, material_name, start, end), _work
    )


# ============================================================
# 6. 자재 사용량
# ============================================================
def get_material_usage(
    date_from: str = "", date_to: str = "", material: str = ""
) -> dict:
    """기간별 자재 사용량을 돌려준다.

    자재별 실제 투입량(kg)과 사용 횟수를 본다.
    "이번 달 자재 얼마나 썼나", "PB 사용량" 같은 물음에 쓴다.

    Args:
        date_from: 시작일 YYYY-MM-DD. 비우면 오늘.
        date_to: 종료일 YYYY-MM-DD. 비우면 시작일과 같은 날.
        material: 자재 이름. 비우면 전체.
    """
    start, end = _range(date_from, date_to)
    needle = (material or "").strip().casefold()

    def _work(connection: sqlite3.Connection) -> dict:
        data = blend_service.material_usage(
            connection, start_date=start, end_date=end
        )
        rows = data.get("items") or []
        if needle:
            rows = [
                row
                for row in rows
                if needle in (row.get("material_name") or "").casefold()
            ]
        items = [
            {
                "자재": row.get("material_name"),
                "실제_사용량_kg": _g_to_kg(row.get("total_actual")),
                "이론량_kg": _g_to_kg(row.get("total_theory")),
                "사용_횟수": int(row.get("usage_count") or 0),
            }
            for row in rows[:30]
        ]
        return {
            "기간": {"시작일": start, "종료일": end},
            "조회_자재": material.strip() or "전체",
            "배합_건수": int(data.get("record_count") or 0),
            "총_배합량_kg": _g_to_kg(data.get("total_weight")),
            "자재별": items,
        }

    return _guard("get_material_usage", (start, end, needle), _work)


# ============================================================
# 7. 레시피
# ============================================================
def get_recipe(product: str) -> dict:
    """반제품의 현재 레시피를 돌려준다.

    자재 구성, 비율, 기본 총량, 허용 편차를 본다.
    "이거 레시피 뭐야", "비율 알려줘" 같은 물음에 쓴다.

    Args:
        product: 반제품 이름. 예 "PB", "APB17".
    """
    name = (product or "").strip()
    if not name:
        return {"error": "반제품 이름이 필요합니다."}

    def _work(connection: sqlite3.Connection) -> dict:
        recipes = blend_service.list_blend_recipes(connection)
        labels = [row.get("product_name") or "" for row in recipes]
        matched = _match_product(name, labels)
        target = None
        for row in recipes:
            if matched and row.get("product_name") == matched:
                target = row
                break
        if target is None:
            return {"error": f"{name} 레시피를 찾지 못했습니다."}

        data = blend_service.get_recipe_for_blend(connection, int(target["id"]))
        if data is None:
            return {"error": f"{name} 레시피를 읽지 못했습니다."}

        info = data.get("recipe") or {}
        items = [
            {
                "자재": item.get("material_name"),
                "비율_퍼센트": _num((item.get("ratio") or 0) * 100, 3),
                "기본량_g": _num(item.get("theory_amount_base")),
                "로스_보정_g": _num(item.get("loss_comp_g")),
            }
            for item in (data.get("items") or [])
        ]
        return {
            "제품": info.get("product_name"),
            "상태": info.get("status"),
            "기본_총량_g": _num(data.get("base_total")),
            "허용_편차_g": info.get("tolerance_g"),
            "자재": items,
        }

    return _guard("get_recipe", (name,), _work)


# ============================================================
# 8. 지금 조치
# ============================================================
def get_attention() -> dict:
    """지금 봐야 할 것을 모아서 돌려준다.

    오늘 점도를 아직 안 넣은 반제품, 점도 이상 건수, ERP 자재 파일이 며칠 됐는지,
    아직 확인하지 않은 증량과 수기 입력 건수를 본다.
    "지금 뭐 봐야 하나", "오늘 할 일" 같은 물음에 쓴다.
    """
    today = _today()

    def _work(connection: sqlite3.Connection) -> dict:
        reminders = viscosity_service.daily_reading_reminders(
            connection, target_date=today
        )
        overview = viscosity_service.overview(connection)
        unacked = blend_service.count_blend_records(connection, only_unacked=True)

        file_summary = erp_lot_service.latest_file_summary() or {}
        stale_days = None
        file_date = file_summary.get("file_date")
        if file_date:
            try:
                stale_days = (date.today() - date.fromisoformat(file_date)).days
            except ValueError:
                stale_days = None

        return {
            "기준일": today,
            "점도_미입력": [
                {
                    "반제품": row.get("code"),
                    "이름": row.get("name"),
                    "대기_건수": int(row.get("pending_count") or 0),
                }
                for row in reminders
            ],
            "점도_이상_건수": int(overview.get("total_anomaly") or 0),
            "자재_파일": {
                "파일명": file_summary.get("file_name"),
                "파일일자": file_date,
                "경과일": stale_days,
                "있음": bool(file_summary.get("found")),
            },
            "미확인_증량_수기_건수": int(unacked or 0),
        }

    return _guard("get_attention", (today,), _work)


# ============================================================
# 9. 사용법 안내
# ============================================================
def get_usage_guide(question: str = "") -> dict:
    """화면 사용법과 문제 해결 절차를 돌려준다.

    "어디서 하나요", "안 돼요", "저장이 안 된 것 같아요", "무슨 뜻이야" 같은
    물음에 쓴다. 손으로 쓴 안내 글 묶음에서 가장 가까운 세 개와 전체 화면 지도를
    돌려준다. 맞는 안내가 없어도 실패가 아니라 빈 목록과 화면 지도를 준다.

    Args:
        question: 사용자가 물은 그대로의 문장.
    """
    text = (question or "").strip()

    def _work(_connection) -> dict:
        return {
            "질문": text,
            "entries": guide_book.search(text),
            "screen_map": guide_book.screen_map(),
        }

    # DB 를 보지 않는 유일한 도구다. 캐시·알림 흐름은 같게 두려고 _guard 를 쓰되
    # 연결은 열지 않는다(안내 글은 파일에 박혀 있다).
    _notify("get_usage_guide", "running")
    key = ("get_usage_guide", text.casefold())
    cached = _cache_get(key)
    if cached is not None:
        _notify("get_usage_guide", "done")
        return cached
    result = _work(None)
    _cache_set(key, result)
    _notify("get_usage_guide", "done")
    return result


# ============================================================
# 등록
# ============================================================
ASSISTANT_TOOLS = [
    get_blend_summary,
    get_recent_records,
    get_record,
    get_viscosity_status,
    get_lot_changes,
    get_material_usage,
    get_recipe,
    get_attention,
    get_usage_guide,
]

TOOL_REGISTRY = {fn.__name__: fn for fn in ASSISTANT_TOOLS}
TOOL_NAMES = tuple(TOOL_REGISTRY)

_TOOL_PARAMS = {
    "get_blend_summary": ("date_from", "date_to"),
    "get_recent_records": ("product", "limit"),
    "get_record": ("product_lot",),
    "get_viscosity_status": ("product",),
    "get_lot_changes": ("recipe", "material", "date_from", "date_to"),
    "get_material_usage": ("date_from", "date_to", "material"),
    "get_recipe": ("product",),
    "get_attention": (),
    "get_usage_guide": ("question",),
}


def tool_catalog() -> list[tuple[str, tuple, str]]:
    """(이름, 인자 이름들, 한 줄 설명) — Groq 계획 단계 프롬프트에 쓴다."""
    catalog = []
    for name, fn in TOOL_REGISTRY.items():
        doc = (fn.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else TOOL_LABELS.get(name, name)
        catalog.append((name, _TOOL_PARAMS.get(name, ()), summary))
    return catalog


def call_tool(name: str, params: dict) -> dict:
    """이름으로 도구를 부른다. 허용 목록 밖이거나 인자가 이상하면 error dict."""
    fn = TOOL_REGISTRY.get(name)
    if fn is None:
        return {"error": f"{name} 은 쓸 수 없는 조회입니다."}
    allowed = set(_TOOL_PARAMS.get(name, ()))
    kwargs = {k: v for k, v in (params or {}).items() if k in allowed}
    try:
        return fn(**kwargs)
    except TypeError as exc:
        _logger.warning("[assistant] %s 인자 오류: %s", name, exc)
        return {"error": f"{TOOL_LABELS.get(name, name)} 인자가 맞지 않습니다."}
