# Design — lot-expiry-tracking (자재 LOT·유통기한 관리)

> Plan: docs/01-plan/features/lot-expiry-tracking.plan.md · 작성일 2026-06-02

## 1. 아키텍처 개요

```
/management (manager) ─ "유통기한·LOT" 탭 ─▶ lot.js
        │  GET  /api/materials/lots                 (operator) 목록
        │  POST /api/materials/{id}/lots            (manager)  등록
        │  POST /api/lots/{lot_id}/consume|discard  (manager)  소진/폐기
        ▼
   lot_routes.build_router() → (operator_router, manager_router)
        │  lot_service.*
        ▼
   material_lots (idx_material_lots_material / idx_material_lots_expiry)

/dashboard (manager) ─ 만료 임박 카드 ─▶ dashboard.js
        │  GET /api/dashboard/expiry-alert
        ▼
   dashboard_routes → lot_service.expiry_alert(conn, limit=5)
```

가산적(additive) 설계 — `stock_service`/`forecast_service` 패턴을 그대로 답습한다.
**`materials.stock_quantity` 및 계량 차감 경로는 무변경**(Plan §3). LOT은 독립 추적 레이어.

## 2. 데이터 모델 — `material_lots`

```sql
CREATE TABLE IF NOT EXISTS material_lots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL REFERENCES materials(id),
    lot_no TEXT,                       -- LOT 번호(선택). 공급사 표기
    received_quantity REAL NOT NULL,   -- 입고 수량(g)
    remaining_quantity REAL NOT NULL,  -- 잔여 수량(g). 등록 시 = received_quantity
    received_at TEXT NOT NULL,         -- 입고일 YYYY-MM-DD
    expiry_date TEXT,                  -- 유통기한 YYYY-MM-DD (NULL=무기한)
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'depleted', 'discarded')),
    note TEXT,
    actor_id INTEGER,
    actor_name TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_material_lots_material
    ON material_lots(material_id, status);
CREATE INDEX IF NOT EXISTS idx_material_lots_expiry
    ON material_lots(expiry_date) WHERE status = 'active';
```

- 날짜는 `YYYY-MM-DD` 문자열. 만료 비교는 **로컬 date** 기준(Plan §7).
- `status`: active(유효) / depleted(소진) / discarded(폐기). 잔여 0 → 자동 depleted.
- 마이그레이션은 `apply_schema_migrations()` 끝에 append-only + `IF NOT EXISTS`.
- `migrations._ALLOWED_TABLES`에 `"material_lots"` 추가(ensure_column 사용은 없지만 일관성).

## 3. 서비스 계층 — `services/lot_service.py`

`stock_service` 규약 답습: 쓰기는 caller가 commit. 순수 판정 함수는 단위 테스트 직격.

### 3.1 상태 판정 (순수 함수)

```python
DEFAULT_ALERT_DAYS = 30          # 임박 판정 창
VALID_STATUSES = {"active", "depleted", "discarded"}

def expiry_state(expiry_date: str | None, today: date, alert_days: int = DEFAULT_ALERT_DAYS) -> str:
    """'expired' | 'expiring_soon' | 'ok' | 'no_expiry' (순수)."""
    if not expiry_date:
        return "no_expiry"
    exp = date.fromisoformat(expiry_date)
    if exp < today:
        return "expired"
    if exp <= today + timedelta(days=alert_days):
        return "expiring_soon"
    return "ok"

def days_until(expiry_date: str | None, today: date) -> int | None:
    return None if not expiry_date else (date.fromisoformat(expiry_date) - today).days
```

### 3.2 쓰기 연산

```python
def register_lot(conn, *, material_id, lot_no, quantity, received_at, expiry_date, actor, note) -> dict
    # 검증: quantity>0, received_at/expiry_date는 ISO date(또는 None), expiry>=received 권장(경고만)
    # INSERT remaining=quantity, status='active'. lastrowid 반환 dict.

def consume_lot(conn, *, lot_id, amount, actor, note) -> dict
    # active 만. amount>0, amount<=remaining. remaining-=amount.
    # remaining<=0 → status='depleted'. 갱신 후 dict 반환.

def discard_lot(conn, *, lot_id, actor, note) -> dict
    # active 만. note 필수. status='discarded', remaining=0.
```

검증 실패는 `ValueError`(라우터가 400 변환). 대상 없음도 `ValueError`.

### 3.3 조회 / 집계

```python
def list_lots(conn, *, material_id=None, include_inactive=False, alert_days=30, today=None) -> list[dict]
    # material_id None이면 전체. include_inactive=False면 status='active'만.
    # 각 row에 expiry_state / days_until 부가. 정렬: 만료 위험 우선 → expiry_date 오름차순.

def expiry_alert(conn, *, alert_days=30, limit=5, today=None) -> dict
    # active & remaining>0 중 expired/expiring_soon 만. 만료→임박, 임박일 오름차순.
    # {alert_days, expired, expiring_soon, total_alert, shown, items[]} 반환.
```

정렬 키: `(_STATE_ORDER[state], days_until ?? inf)` — forecast_service 패턴과 동형.

## 4. API — `routers/lot_routes.py`

`stock_routes`처럼 `(operator_router, manager_router)` 튜플 반환.

| Method | Path | Scope | 설명 |
|--------|------|-------|------|
| GET  | `/materials/lots` | operator | 전체 active LOT + 상태 |
| GET  | `/materials/{material_id}/lots` | operator | 자재별 LOT(include_inactive 옵션) |
| POST | `/materials/{material_id}/lots` | manager | LOT 등록 |
| POST | `/lots/{lot_id}/consume` | manager | 소진 기록 |
| POST | `/lots/{lot_id}/discard` | manager | 폐기 |
| GET  | `/lots/export` | manager | CSV(수식 인젝션 방어, forecast_export 패턴) |

- 쓰기는 `write_audit_log`로 감사 기록(action: `material_lot_register|consume|discard`).
- `ensure_material`로 자재 검증. `ValueError → HTTPException(400)`.
- 대시보드 알림은 `dashboard_routes`에 추가:
  `GET /api/dashboard/expiry-alert` (manager) → `lot_service.expiry_alert(conn, limit=5)`.

### 4.1 Pydantic 모델 (`routers/models.py`)

```python
class LotCreateBody(BaseModel):
    lot_no: str | None = Field(default=None, max_length=100)
    quantity: float = Field(gt=0)
    received_at: str | None = None          # None → 서버 오늘
    expiry_date: str | None = None
    note: str | None = None

class LotConsumeBody(BaseModel):
    amount: float = Field(gt=0)
    note: str | None = None

class LotDiscardBody(BaseModel):
    note: str = Field(min_length=1)
```

날짜 문자열 형식은 서비스에서 `date.fromisoformat`으로 검증(잘못되면 400).

## 5. 프런트엔드

### 5.1 management.html — "유통기한·LOT" 탭
- 탭 버튼 추가: `<button class="mgmt-tab" data-tab="lots">유통기한·LOT</button>` (stock 다음).
- `#tab-lots` 패널: 안내 문구(“유통기한 추적용 — 재고 수량과 별도 관리”) + 검색 + 목록 표
  (자재 / LOT번호 / 잔여(g) / 입고일 / 유통기한 / D-day / 상태 / 작업[등록·소진·폐기]).
- 모달 2종(`hidden`): LOT 등록(수량/입고일/유통기한/LOT번호/비고), 소진·폐기 입력.
- 공통 CSS 재사용: `.panel` `.table-wrap` `.input` `.btn` `.stock-status`(상태 배지) `.ss-modal-overlay`.

### 5.2 static/js/lot.js (신규, stock.js와 동형 IIFE)
- `management.html` 스크립트 목록 끝(`forecast.js` 다음)에 `<script src="/static/js/lot.js">`.
- 쓰기 시 CSRF: **`csrftoken` 쿠키 → `x-csrftoken` 헤더**(forecast.js/admin_users.js 패턴, 메모리 `project_management_csrf`).
- 상태 배지 클래스 매핑: expired→`stock-negative`(빨강), expiring_soon→`stock-low`(주황), ok→`stock-ok`, no_expiry→중립.
- 한국어 라벨: 만료 / 임박 / 정상 / 무기한.
- `escapeHtml`로 XSS 방지(stock.js와 동일 헬퍼).

### 5.3 dashboard — 만료 임박 카드
- `dashboard.html`: 발주 임박 카드(`#forecast-alert`) 다음에 `#expiry-alert` 섹션(`hidden`), 동일 구조.
- `dashboard.js`: `loadExpiryAlert()` 추가(읽기 전용 GET → CSRF 불필요). 0건이면 `hidden` 유지.
  상태 라벨 만료/임박, `IRMS.escapeHtml` 사용. 표 컬럼: 자재 / LOT / 상태 / 유통기한 / D-day / 잔여(g).

## 6. 권한·보안

- 조회 operator / 쓰기·대시 manager — 라우터 dependency 강제.
- GET은 부작용 없음(CSRF 무관). 쓰기는 x-csrftoken 필수.
- CSV·HTML 출력 모두 수식 인젝션/XSS 방어(forecast_export·stock.js 패턴).

## 7. 테스트 (`tests/test_lot_expiry_tracking.py`)

in-memory SQLite + 자체 스키마(test_forecast_dashboard_alert 패턴).

| ID | 시나리오 | 기대 |
|----|----------|------|
| L1 | `expiry_state` 경계 | 과거→expired, 오늘+0/+alert→expiring_soon, +alert+1→ok, None→no_expiry |
| L2 | register_lot | remaining=quantity, status=active, lastrowid |
| L3 | consume 부분/전량 | 부분→active, 전량→remaining 0 & depleted |
| L4 | consume 초과 | ValueError |
| L5 | discard | status=discarded, remaining=0, note 필수(빈 note→ValueError) |
| L6 | list_lots 정렬/필터 | expired 먼저, include_inactive=False면 active만 |
| L7 | expiry_alert | expired+expiring_soon만, 만료 먼저, ok/무기한 제외, limit 적용 |
| L8 | 라우트 권한 | 비인증 `/api/materials/lots`·`/api/dashboard/expiry-alert` → 401/403 |
| L9 | 마이그레이션 | apply 후 `material_lots` 테이블 + 인덱스 2종 존재 |

## 8. 회귀 방지

- 기존 테이블/엔드포인트 무변경(추가만). stock/forecast 시그니처 불변.
- migrations append-only + IF NOT EXISTS → 재실행/운영 DB 안전.
- 라우터 조립: api.py에 `lot_op_router`/`lot_mgr_router` include 추가만.
