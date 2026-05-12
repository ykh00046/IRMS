# split-large-files Design Document (Phase 1 — Python)

> **Summary**: `recipe_routes.py` (1,132 lines) → 5개 라우터 + 1개 헬퍼 모듈 + Pydantic 모델 통합. URL·응답 스키마·인증 정책 100% 보존.
>
> **Project**: IRMS
> **Version**: 2.0.0 (post `b808920`)
> **Author**: ykh00046
> **Date**: 2026-05-12
> **Status**: Draft
> **Planning Doc**: [split-large-files.plan.md](../../01-plan/features/split-large-files.plan.md)
> **Phase Scope**: Phase 1 (Python recipe_routes.py 분리)
> **Out of Phase**: Phase 2~4(JS)는 후속 `/pdca design` 사이클로 분리

---

## 1. Overview

### 1.1 Design Goals

1. `recipe_routes.py` 단일 파일(1,132줄)을 도메인별 5개 라우터 모듈 + 1개 헬퍼 모듈로 분리
2. 22개 엔드포인트의 URL·메서드·응답 스키마·인증(`require_access_level`) 정책 보존
3. 중첩 헬퍼 함수(`_find_chain_root`, `_ensure_material` 등)를 `services/recipe_helpers.py`로 추출하여 `weighing_routes.py`의 import 라인(`from .recipe_routes import _format_display_value`) 깨짐 방지
4. Pydantic body 모델 4개(`_StockAmountBody` 등)를 `models.py`로 통합
5. `api.py`의 `include_router` 등록 순서·prefix 보존

### 1.2 Design Principles

- **순수 분리 원칙** — 기능 변경·성능 개선 금지. `git diff` 기준 코드 라인 이동만 발생
- **단일 책임** — 각 라우터 파일은 하나의 도메인(operator-read / manager-write / stock / import / stats)만 담당
- **권한 경계 명시** — 파일명에 `operator` / `manager` 명시 (`recipe_operator_routes.py`, `recipe_manager_routes.py`)
- **헬퍼는 services 계층** — 라우터 간 공유되는 closure 함수는 `src/services/`로 이전, public symbol(언더스코어 제거)
- **import 일관성** — 모든 신규 모듈은 동일한 import 순서(stdlib → fastapi → pydantic → 내부 절대 → 내부 상대)

---

## 2. Architecture

### 2.1 Component Diagram (Before vs After)

```
BEFORE (현재):
┌──────────────────────────────────────────────────┐
│ src/routers/recipe_routes.py (1,132 LOC)        │
│   build_router() → (operator_router,            │
│                     manager_router)              │
│   ├ operator: notifications, materials, recipes,│
│   │           recipes/{id}/{detail,history,...},│
│   │           materials/stock, stock-log         │
│   └ manager: recipes/{id} DELETE, stock CUD,    │
│              recipes/progress, import, stats     │
│                                                  │
│   nested helpers: _format_display_value,        │
│     _fetch_recipe_items, _find_chain_root,      │
│     _fetch_chain, _ensure_material              │
│   nested models: _StockAmountBody, etc.         │
└──────────────────────────────────────────────────┘
         ↑ import from weighing_routes.py
         (_format_display_value)


AFTER (목표):
┌─────────────────────────────────────────────────────┐
│ src/services/recipe_helpers.py (~80 LOC)           │
│   format_display_value()                            │
│   fetch_recipe_items()                              │
│   find_chain_root()                                 │
│   fetch_chain()                                     │
│   ensure_material()                                 │
└─────────────────────────────────────────────────────┘
                  ↑↑↑↑↑ 모든 라우터 + weighing_routes 공유
                  
┌──────────────────┬──────────────────┬─────────────┐
│ recipe_operator  │ recipe_manager   │ stock       │
│ _routes.py       │ _routes.py       │ _routes.py  │
│ (~470 LOC, 9 EP) │ (~280 LOC, 3 EP) │ (~210, 6 EP)│
├──────────────────┼──────────────────┼─────────────┤
│ recipe_import    │ recipe_stats     │             │
│ _routes.py       │ _routes.py       │             │
│ (~100 LOC, 2 EP) │ (~95 LOC, 2 EP)  │             │
└──────────────────┴──────────────────┴─────────────┘
                  ↑
┌─────────────────────────────────────────────────────┐
│ src/routers/api.py (수정)                          │
│   build_router() → include_router x 9              │
└─────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────┐
│ src/routers/models.py (확장)                       │
│   StockAmountBody, StockAdjustBody,                │
│   StockDiscardBody, StockThresholdBody             │
└─────────────────────────────────────────────────────┘
```

### 2.2 Data Flow (Unchanged)

```
Browser → /api/{path} → api.py router → 해당 도메인 라우터 → 
  → require_access_level dependency 검증 → 
  → get_connection() → SQLite → row_to_dict → JSON
```

### 2.3 Dependencies

| Component | Depends On | Purpose |
|-----------|-----------|---------|
| `recipe_operator_routes.py` | `services/recipe_helpers.py`, `database`, `auth`, `models` | operator 권한 read 엔드포인트 |
| `recipe_manager_routes.py` | `services/recipe_helpers.py`, `database`, `auth`, `services/stock_service` | manager 권한 recipe 쓰기 + 진행률 |
| `stock_routes.py` | `services/stock_service`, `services/recipe_helpers.ensure_material`, `models` | 재고 read(op) + write(mgr) |
| `recipe_import_routes.py` | `services/import_parser`, `database`, `models.ImportRequest` | 엑셀 import |
| `recipe_stats_routes.py` | `database` | 통계·CSV export |
| `services/recipe_helpers.py` | `database.row_to_dict` | 공유 chain·display 헬퍼 |
| `weighing_routes.py` | `services/recipe_helpers.format_display_value` (변경) | 기존 `from .recipe_routes import _format_display_value` 대체 |

---

## 3. Endpoint Migration Map

22개 엔드포인트 전체 매핑. **URL·메서드·인증·응답 스키마 모두 보존.**

### 3.1 → `recipe_operator_routes.py` (9개)

| Current Line | Method | Path | Auth | Migration Notes |
|---:|---|---|---|---|
| 68 | GET | `/notifications/recipe-imports` | operator | as-is |
| 86 | GET | `/materials` | operator | as-is |
| 122 | GET | `/recipes/products` | operator | as-is |
| 131 | GET | `/recipes/by-product` | operator | as-is |
| 169 | GET | `/recipes/{recipe_id}/detail` | operator | uses `fetch_recipe_items` helper |
| 248 | GET | `/recipes/{recipe_id}/history` | operator | uses `find_chain_root`, `fetch_chain` |
| 281 | GET | `/recipes/history/compare` | operator | uses `find_chain_root`, `fetch_chain`, `fetch_recipe_items` |
| 392 | GET | `/recipes` | operator | uses `fetch_recipe_items` |
| 451 | PATCH | `/recipes/{recipe_id}/status` | operator | as-is |

### 3.2 → `recipe_manager_routes.py` (3개)

| Current Line | Method | Path | Auth | Migration Notes |
|---:|---|---|---|---|
| 548 | DELETE | `/recipes/{recipe_id}` | manager | as-is |
| 721 | GET | `/recipes/progress` | manager | as-is |
| 815 | GET | `/recipes/operator-progress` | manager | as-is |

### 3.3 → `stock_routes.py` (6개, 권한 혼합)

| Current Line | Method | Path | Auth | Migration Notes |
|---:|---|---|---|---|
| 606 | GET | `/materials/stock` | operator | uses `stock_service.list_stock` |
| 612 | GET | `/materials/{material_id}/stock-log` | operator | uses `ensure_material` helper |
| 619 | POST | `/materials/{material_id}/stock/restock` | manager | body: `StockAmountBody` |
| 646 | POST | `/materials/{material_id}/stock/adjust` | manager | body: `StockAdjustBody` |
| 673 | POST | `/materials/{material_id}/stock/discard` | manager | body: `StockDiscardBody` |
| 700 | PATCH | `/materials/{material_id}/stock-threshold` | manager | body: `StockThresholdBody` |

**권한 혼합 처리**: `stock_routes.py`는 `build_router()`에서 `(operator_router, manager_router)` 튜플을 반환. `recipe_routes.py`의 기존 패턴 그대로 차용.

### 3.4 → `recipe_import_routes.py` (2개)

| Current Line | Method | Path | Auth | Migration Notes |
|---:|---|---|---|---|
| 948 | POST | `/recipes/import/preview` | manager | uses `parse_import_text` |
| 954 | POST | `/recipes/import` | manager | uses `parse_import_text`, `actor_name`, `write_audit_log` |

### 3.5 → `recipe_stats_routes.py` (2개)

| Current Line | Method | Path | Auth | Migration Notes |
|---:|---|---|---|---|
| 1042 | GET | `/stats/consumption` | manager | as-is |
| 1107 | GET | `/stats/export` | manager | `stats_export` 내부에서 `stats_consumption` 호출 → 동일 모듈 내 참조로 유지 |

---

## 4. Helper & Model Extraction

### 4.1 `src/services/recipe_helpers.py` (신규)

```python
"""Shared helpers for recipe-related routers.

Extracted from former src/routers/recipe_routes.py as part of Phase 1
of the split-large-files PDCA cycle (2026-05).
"""

from typing import Any

from ..database import row_to_dict


def format_display_value(weight, text) -> str:
    """Combine weight and text into a display string."""
    if weight is not None and text:
        return f"{weight} ({text})"
    if weight is not None:
        return str(weight)
    if text:
        return text
    return ""


def fetch_recipe_items(connection, recipe_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    """Shared helper to fetch recipe items with material info."""
    # (현재 _fetch_recipe_items 본문 그대로 이동, format_display_value 호출만 변경)
    ...


def find_chain_root(connection, recipe_id: int) -> int:
    """Walk revision_of upward to find the root recipe of a revision chain."""
    # (현재 _find_chain_root 본문 그대로 이동)
    ...


def fetch_chain(connection, root_id: int) -> list[dict[str, Any]]:
    """Walk revision_of downward to fetch all revisions in a chain."""
    # (현재 _fetch_chain 본문 그대로 이동)
    ...


def ensure_material(connection, material_id: int) -> dict:
    """Return active material row or raise 404."""
    # (현재 _ensure_material 본문 그대로 이동)
    ...
```

**Symbol rename**: 언더스코어 제거 (모듈 외부 공개 의도 명시).

### 4.2 `src/routers/models.py` (확장)

```python
# 기존 ImportRequest, StatusUpdateRequest 등 유지
# + 신규 추가:

class StockAmountBody(BaseModel):
    amount: float
    note: str | None = None


class StockAdjustBody(BaseModel):
    new_quantity: float
    note: str


class StockDiscardBody(BaseModel):
    amount: float
    note: str


class StockThresholdBody(BaseModel):
    threshold: float
```

### 4.3 `src/routers/weighing_routes.py` (import 경로 수정)

총 **4줄** 편집 필요:

| Line | 현재 | 변경 후 |
|---:|---|---|
| 8 | `from .recipe_routes import _format_display_value` (모듈 top-level import) | `from ..services.recipe_helpers import format_display_value` |
| 66 | `from .recipe_routes import _format_display_value` (함수 내 shadow import — 중복) | 삭제 (top-level import로 충분) |
| 74 | `_format_display_value(...)` 호출 | `format_display_value(...)` |
| 199 | `item_payload["target_value"] = _format_display_value(...)` 호출 | `... = format_display_value(...)` |

> **주의**: line 66의 함수 내부 `from .recipe_routes import ...`는 line 8의 top-level import와 중복된 shadow import로, 삭제해도 동작에 영향 없음. 분리 작업 중 함께 정리.

---

## 5. File Layout (Final)

```
src/
├── routers/
│   ├── api.py                       ← MODIFIED (include_router 추가)
│   ├── models.py                    ← MODIFIED (Stock*Body 4개 추가)
│   ├── recipe_routes.py             ← DELETED (분리 완료 후)
│   ├── recipe_operator_routes.py    ← NEW (~470 LOC, 9 EP)
│   ├── recipe_manager_routes.py     ← NEW (~280 LOC, 3 EP)
│   ├── stock_routes.py              ← NEW (~210 LOC, 6 EP)
│   ├── recipe_import_routes.py     ← NEW (~100 LOC, 2 EP)
│   ├── recipe_stats_routes.py      ← NEW (~95 LOC, 2 EP)
│   ├── weighing_routes.py          ← MODIFIED (import 경로 2곳)
│   └── ... (other routers unchanged)
└── services/
    ├── recipe_helpers.py            ← NEW (~80 LOC)
    └── ... (other services unchanged)
```

총 변경: **신규 6개 / 수정 3개 / 삭제 1개**

---

## 6. Routing Registration (`src/routers/api.py`)

### 6.1 Before (현재, `api.py` 전체)

```python
from fastapi import APIRouter

from ..database import utc_now_text
from . import (
    admin_routes,
    attendance_routes,
    auth_routes,
    chat_routes,
    dashboard_routes,
    public_attendance_alert_routes,
    recipe_routes,
    spreadsheet_routes,
    weighing_routes,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    public_router, auth_me_router = auth_routes.build_router()
    admin_router = admin_routes.build_router()
    chat_router = chat_routes.build_router()
    public_attendance_alert_router = public_attendance_alert_routes.build_router()
    attendance_router = attendance_routes.build_router()
    recipe_op_router, recipe_mgr_router = recipe_routes.build_router()
    weighing_router = weighing_routes.build_router()
    ss_router = spreadsheet_routes.build_router()
    dashboard_router = dashboard_routes.build_router()

    @public_router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    router.include_router(public_router)
    router.include_router(public_attendance_alert_router)
    router.include_router(attendance_router)
    router.include_router(auth_me_router)
    router.include_router(recipe_op_router)
    router.include_router(chat_router)
    router.include_router(weighing_router)
    router.include_router(recipe_mgr_router)
    router.include_router(admin_router)
    router.include_router(ss_router, prefix="/spreadsheet")
    router.include_router(dashboard_router)
    return router
```

### 6.2 After (목표, `api.py` 전체)

```python
from fastapi import APIRouter

from ..database import utc_now_text
from . import (
    admin_routes,
    attendance_routes,
    auth_routes,
    chat_routes,
    dashboard_routes,
    public_attendance_alert_routes,
    recipe_operator_routes,
    recipe_manager_routes,
    recipe_import_routes,
    recipe_stats_routes,
    stock_routes,
    spreadsheet_routes,
    weighing_routes,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    public_router, auth_me_router = auth_routes.build_router()
    admin_router = admin_routes.build_router()
    chat_router = chat_routes.build_router()
    public_attendance_alert_router = public_attendance_alert_routes.build_router()
    attendance_router = attendance_routes.build_router()
    recipe_op_router = recipe_operator_routes.build_router()
    recipe_mgr_router = recipe_manager_routes.build_router()
    stock_op_router, stock_mgr_router = stock_routes.build_router()
    import_router = recipe_import_routes.build_router()
    stats_router = recipe_stats_routes.build_router()
    weighing_router = weighing_routes.build_router()
    ss_router = spreadsheet_routes.build_router()
    dashboard_router = dashboard_routes.build_router()

    @public_router.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "time": utc_now_text()}

    router.include_router(public_router)
    router.include_router(public_attendance_alert_router)
    router.include_router(attendance_router)
    router.include_router(auth_me_router)
    router.include_router(recipe_op_router)
    router.include_router(stock_op_router)         # operator stock reads
    router.include_router(chat_router)
    router.include_router(weighing_router)
    router.include_router(recipe_mgr_router)       # manager recipe writes
    router.include_router(stock_mgr_router)        # manager stock writes
    router.include_router(import_router)
    router.include_router(stats_router)
    router.include_router(admin_router)
    router.include_router(ss_router, prefix="/spreadsheet")
    router.include_router(dashboard_router)
    return router
```

**등록 순서 원칙**: 인증 정책 순 (public → operator → manager → admin). 라우터 prefix 없음(기존과 동일, 각 엔드포인트 절대 경로). `weighing_router`/`chat_router`/`ss_router`/`dashboard_router` 등 본 분리와 무관한 라우터의 상대 순서는 보존.

---

## 7. Error Handling (Unchanged)

기존 HTTPException 발생 패턴 그대로 보존. 예시:

| Code | Path | Cause |
|---|---|---|
| 400 | `/recipes/history/compare?ids=...` | `INVALID_IDS`, `NEED_AT_LEAST_TWO`, `TOO_MANY_IDS`, `DIFFERENT_CHAINS` |
| 400 | `/recipes/{id}` DELETE | `CANNOT_DELETE_ACTIVE_RECIPE` |
| 400 | `/materials/{id}/stock/*` | `ValueError` from `stock_service` → 400 detail |
| 404 | `/recipes/{id}/history` | `Recipe not found` |
| 404 | `/materials/{id}/...` | `MATERIAL_NOT_FOUND` |
| 409 | `/recipes/import` | `DUPLICATE_IMPORT` (with existing list) |

---

## 8. Security Considerations

- [x] **인증 정책 보존** — `require_access_level("operator")` / `("manager")` 각 라우터 데코레이터로 전부 이전
- [x] **CSRF** — 기존 글로벌 CSRF 미들웨어(`starlette_csrf`)가 모든 mutate 메서드 보호, 분리 후에도 그대로 적용
- [x] **Audit log** — `write_audit_log` 호출 위치 보존 (stock CUD, recipe import, recipe delete)
- [x] **SQL injection** — 기존 placeholder 패턴 그대로, 신규 SQL 생성 없음
- [x] **분리 작업 자체의 보안 영향** — 없음 (코드 이동만)

---

## 9. Test Plan

### 9.1 Test Scope

| Type | Target | Tool |
|------|--------|------|
| 정적 검증 | URL 경로 보존 | `grep` 비교 스크립트 |
| 정적 검증 | import 경로 정합성 | `python -c "import src.main"` |
| 회귀 (Python) | 기존 32 pytest 전부 통과 | `.venv\Scripts\pytest -q` |
| 회귀 (CI) | GitHub Actions `test.yml` 통과 | PR 자동 |
| 수동 스모크 | 관리자 골든패스 | 브라우저 |
| 수동 스모크 | 작업자 골든패스 | 브라우저 |

> **테스트 영향 사전 확인** (2026-05-12 검증 완료):
> `grep -r "recipe_routes\|_format_display_value\|_fetch_recipe_items\|_find_chain_root\|_fetch_chain\|_ensure_material" tests/` 결과 **0건**. 어떤 테스트도 `recipe_routes` 모듈이나 그 private 심볼을 직접 import하지 않으므로 테스트 코드 수정 불필요.

### 9.2 정적 검증 절차

**분리 직전·직후 동일성 확인**:

```bash
# 1. 분리 전: 현재 엔드포인트 목록 추출
grep -nE '@(operator|manager)_router\.(get|post|put|patch|delete)' \
  src/routers/recipe_routes.py \
  | awk -F'"' '{print $2}' | sort > /tmp/endpoints_before.txt

# 2. 분리 후: 신규 5개 라우터 합산
grep -nE '@(operator|manager)_router\.(get|post|put|patch|delete)' \
  src/routers/recipe_operator_routes.py \
  src/routers/recipe_manager_routes.py \
  src/routers/stock_routes.py \
  src/routers/recipe_import_routes.py \
  src/routers/recipe_stats_routes.py \
  | awk -F'"' '{print $2}' | sort > /tmp/endpoints_after.txt

# 3. diff: 0 라인이어야 함
diff /tmp/endpoints_before.txt /tmp/endpoints_after.txt
```

### 9.3 수동 스모크 체크리스트

**관리자 페이지** (`/management`):
- [ ] 레시피 검색·필터 동작
- [ ] 레시피 상세 모달 (재료 목록 표시)
- [ ] 레시피 이력 모달 (`v1`, `v2`, …)
- [ ] 버전 비교 (2개 이상 체크 → 비교 보기)
- [ ] 엑셀 import preview → register
- [ ] 재고 페이지 (입고·조정·폐기·임계치)
- [ ] 통계 조회 + CSV 다운로드
- [ ] 진행률 카드

**작업자 페이지** (`/work`):
- [ ] 계량 큐 표시 (재료 색상 그룹별)
- [ ] 계량 시작 → 완료 → 다음 단계 진행 (`format_display_value` 호출 경로)
- [ ] 되돌리기

**근태 페이지** (`/attendance`):
- [ ] 로그인 → 조회 (레시피 라우터 미영향 확인용)

---

## 10. Clean Architecture Alignment

### 10.1 Layer Structure (IRMS 기준)

| Layer | Responsibility | Location | Phase 1 변경 |
|---|---|---|---|
| **Presentation** | Jinja 템플릿, JS | `templates/`, `static/js/` | 없음 |
| **Application/Routing** | FastAPI 라우터 (HTTP 인터페이스) | `src/routers/` | **5개 신규 + api.py 수정** |
| **Domain/Services** | 비즈니스 로직, 데이터 변환 | `src/services/` | **`recipe_helpers.py` 신규** |
| **Infrastructure** | SQLite, 외부 자원 | `src/database.py` | 없음 |

### 10.2 Dependency Rules

```
Routers (recipe_*_routes, stock_routes, ...) 
    → Services (recipe_helpers, stock_service, import_parser)
    → Database (row_to_dict, get_connection)
```

- 라우터 간 직접 import 금지 (현재 `weighing_routes`가 `recipe_routes`를 import 하는 안티패턴을 헬퍼 분리로 해소)
- 헬퍼는 라우터를 import 하지 않음

---

## 11. Coding Convention

### 11.1 신규 파일 헤더 템플릿

**범용 템플릿**:

```python
"""<도메인 한 줄 설명>.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05). See docs/01-plan/features/split-large-files.plan.md.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel  # only if local models exist

from ..auth import get_current_user, require_access_level
from ..database import get_connection, row_to_dict, utc_now_text, write_audit_log
from ..services.recipe_helpers import <needed_helpers>
from .models import <needed_models>
```

**구체 예시 — `recipe_operator_routes.py`**:

```python
"""Operator-scope read endpoints for recipes, recipe history, and materials.

Provides 9 endpoints accessible to operator-level users for browsing recipes,
viewing version history, comparing revisions, and listing active materials.
All endpoints are read-only (GET) except PATCH /recipes/{id}/status which
operators use to advance recipe workflow state.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05). See docs/01-plan/features/split-large-files.plan.md.

Endpoints:
    GET    /notifications/recipe-imports
    GET    /materials
    GET    /recipes/products
    GET    /recipes/by-product
    GET    /recipes/{recipe_id}/detail
    GET    /recipes/{recipe_id}/history
    GET    /recipes/history/compare
    GET    /recipes
    PATCH  /recipes/{recipe_id}/status
"""
```

**구체 예시 — `stock_routes.py`** (튜플 반환 패턴):

```python
"""Material stock tracking endpoints (operator reads + manager writes).

Returns a tuple of (operator_router, manager_router). See section 11.2 of
the design for why stock_routes is the one router file that combines two
authorization scopes instead of splitting by role.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    GET    /materials/stock                              (operator)
    GET    /materials/{material_id}/stock-log            (operator)
    POST   /materials/{material_id}/stock/restock        (manager)
    POST   /materials/{material_id}/stock/adjust         (manager)
    POST   /materials/{material_id}/stock/discard        (manager)
    PATCH  /materials/{material_id}/stock-threshold      (manager)
"""
```

### 11.2 Naming Conventions

| Target | Rule | Example |
|---|---|---|
| 모듈 파일명 | `<domain>_routes.py` 또는 `<domain>_<role>_routes.py` | `recipe_operator_routes.py` |
| `build_router()` | 단일 라우터: `→ APIRouter` / 권한 혼합: `→ tuple[APIRouter, APIRouter]` | `stock_routes.build_router() → (op, mgr)` |
| 헬퍼 함수 | `snake_case`, 언더스코어 prefix 제거 (공개) | `format_display_value` |
| Pydantic 모델 | `PascalCase`, 언더스코어 prefix 제거 | `StockAmountBody` |

#### `stock_routes.py` 명명 일관성 결정

`recipe_*_routes.py`는 `operator`/`manager` 권한별로 2개 파일로 분리하면서, `stock_routes.py`는 단일 파일에 두 권한 라우터를 함께 두고 튜플로 반환한다. 이는 일관성 위반처럼 보이지만 다음 이유로 의도된 선택:

1. **stock 도메인이 작음** — 6개 엔드포인트(op 2, mgr 4)를 분리하면 각 파일이 ~80/~130 LOC로 지나치게 얇아짐. 600 LOC 한도 대비 응집성 손실이 큼
2. **공유 헬퍼 집중** — `ensure_material`, `_StockAmountBody`/`_StockAdjustBody` 등이 stock 도메인 안에서만 쓰임 → 동일 파일에 두는 것이 자연스러움
3. **튜플 반환 패턴 선례** — 분리 전 `recipe_routes.py` 자체가 이미 `(op, mgr)` 튜플을 반환하므로 새로운 패턴 도입 아님

향후 stock 도메인이 커지면 `stock_operator_routes.py` + `stock_manager_routes.py`로 추가 분리하는 후속 PDCA를 진행한다.

### 11.3 This Feature's Conventions

| Item | Convention Applied |
|---|---|
| 모듈 분리 단위 | 권한 + 도메인 |
| Helper 위치 | `src/services/recipe_helpers.py` |
| 모델 위치 | `src/routers/models.py` 통합 |
| Audit log 호출 | 기존 위치 보존 |
| SQL 변경 | 없음 |

---

## 12. Implementation Guide

### 12.1 작업 순서 (역방향 의존성 우선)

1. **`services/recipe_helpers.py` 신규 생성**
   - `_format_display_value`, `_fetch_recipe_items`, `_find_chain_root`, `_fetch_chain`, `_ensure_material`를 `recipe_routes.py`에서 복사
   - 언더스코어 prefix 제거 (public)
2. **`routers/models.py` 확장**
   - `StockAmountBody` 외 3개 모델 추가
3. **`routers/weighing_routes.py` 수정 (선반영)**
   - `from .recipe_routes import _format_display_value` → `from ..services.recipe_helpers import format_display_value`
   - 호출부 2곳 함수명 교체
4. **신규 5개 라우터 파일 생성**
   - 순서: `recipe_operator_routes.py` → `stock_routes.py` → `recipe_manager_routes.py` → `recipe_import_routes.py` → `recipe_stats_routes.py`
   - 각 파일은 `build_router()` 함수에 해당 영역 엔드포인트를 옮겨 담음
5. **`routers/api.py` 수정**
   - import 라인 교체
   - `include_router` 순서 재구성
6. **`routers/recipe_routes.py` 삭제**
7. **검증**
   - `python -c "from src.main import app"` 임포트 OK 확인
   - `pytest -q` 32개 통과
   - 정적 검증 스크립트(섹션 9.2) 실행
   - 수동 스모크 (섹션 9.3)

### 12.2 PR 전략

- **단일 PR로 묶음**: 위 1~7 단계 전체를 하나의 PR로 (중간 상태는 import 깨짐 → bisect 곤란)
- **커밋은 단계별 분리**: 1~6 각각 별도 커밋, 7은 검증 결과 메모와 함께 마지막 커밋

### 12.3 롤백 시나리오

문제 발생 시 `git revert <PR-merge-commit>` 한 번으로 완전 복귀 가능. DB 변경 없음·외부 영향 없음.

---

## 13. Future Scope (Phase 2~4)

본 design은 **Phase 1(Python)에만 적용**. 후속 phase는 별도 `/pdca` 사이클:

| Phase | 대상 | 예상 작업 | 별도 design 문서 |
|---|---|---|---|
| 2 | `static/js/common.js` (1,218 LOC) | IIFE 11개 모듈로 분리 + `window.IRMS` 호환 | `/pdca plan split-common-js` |
| 3 | `static/js/management.js` (1,006 LOC) | 탭별 5개 모듈 | `/pdca plan split-management-js` |
| 4 | `static/js/work.js` (760 LOC) | 계량/채팅/테이블 3개 모듈 | `/pdca plan split-work-js` |

Phase 2~4는 Phase 1 머지·안정화 후 진행.

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-12 | Initial draft — Phase 1 (Python) 상세 설계 | ykh00046 |
