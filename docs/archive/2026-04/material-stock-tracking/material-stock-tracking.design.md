# Material Stock Tracking Design

> Plan 문서: `docs/01-plan/features/material-stock-tracking.plan.md`

## 0. Resolved Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | 음수 재고 허용 | **허용 + 경고만** — 계량은 진행, 빨간 경고 노출 |
| Q2 | 초기 재고 입력 | **모두 0으로 시작** — 책임자가 입고 처리로 채움 |
| Q3 | 단위 | **모두 g 고정** — stock_quantity 단위 g |

## 1. Data Model

### 1.1 `materials` 컬럼 추가

```sql
ALTER TABLE materials ADD COLUMN stock_quantity REAL NOT NULL DEFAULT 0;
ALTER TABLE materials ADD COLUMN stock_threshold REAL NOT NULL DEFAULT 0;
```

- `ensure_column(connection, "materials", "stock_quantity", "REAL NOT NULL DEFAULT 0")`
- `ensure_column(connection, "materials", "stock_threshold", "REAL NOT NULL DEFAULT 0")`
- 기존 행은 0으로 초기화 → Q2 결정 준수
- 단위: g (Q3) — UI 표시에만 "g" 붙임, 저장은 순수 숫자

### 1.2 `material_stock_logs` 신설

```sql
CREATE TABLE IF NOT EXISTS material_stock_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id INTEGER NOT NULL,
    delta REAL NOT NULL,              -- 양수=입고/보정증, 음수=차감/폐기
    balance_after REAL NOT NULL,      -- 차감/입고 후 잔량 스냅샷
    reason TEXT NOT NULL,             -- 'measurement'|'restock'|'adjust'|'discard'
    actor_id INTEGER,                 -- users.id, NULL 가능 (시스템 차감)
    actor_name TEXT,                  -- 감사용 스냅샷
    recipe_id INTEGER,                -- measurement 시 연결
    recipe_item_id INTEGER,           -- measurement 시 연결
    note TEXT,                        -- 조정 사유 메모
    created_at TEXT NOT NULL,
    FOREIGN KEY (material_id) REFERENCES materials(id)
);
CREATE INDEX IF NOT EXISTS idx_stock_logs_material ON material_stock_logs(material_id, created_at DESC);
```

## 2. Auto-Deduction Logic

### 2.1 차감 시점

- **트리거**: 계량 확정 엔드포인트(`POST /api/recipes/{id}/items/{item_id}/measure` 계열)
- **조건**: `value_weight`가 숫자이고 `measured_at` 신규 기록 시 1회만
- **멱등성**: 같은 `recipe_item_id`가 이미 `material_stock_logs`에 reason='measurement'로 있으면 스킵 (재계량 대비)

### 2.2 트랜잭션

```python
def deduct_stock(connection, recipe_id, recipe_item_id, material_id, weight, actor):
    with connection:  # 자동 BEGIN/COMMIT
        existing = connection.execute(
            "SELECT 1 FROM material_stock_logs WHERE recipe_item_id = ? AND reason = 'measurement'",
            (recipe_item_id,)
        ).fetchone()
        if existing:
            return  # 멱등

        row = connection.execute(
            "SELECT stock_quantity FROM materials WHERE id = ?",
            (material_id,)
        ).fetchone()
        current = row["stock_quantity"] if row else 0.0
        new_balance = current - weight  # 음수 허용 (Q1)

        connection.execute(
            "UPDATE materials SET stock_quantity = ? WHERE id = ?",
            (new_balance, material_id)
        )
        connection.execute(
            """INSERT INTO material_stock_logs
               (material_id, delta, balance_after, reason, actor_id, actor_name,
                recipe_id, recipe_item_id, created_at)
               VALUES (?, ?, ?, 'measurement', ?, ?, ?, ?, ?)""",
            (material_id, -weight, new_balance, actor["id"], actor["display_name"],
             recipe_id, recipe_item_id, utc_now_text())
        )
```

- `value_text`만 있고 `value_weight`가 없는 항목(예: "APB", `=SUM(...)` 미평가)은 **차감 대상 아님**
- 수식(`=`) 항목은 현재 value_weight에 평가된 숫자가 들어있으면 차감, 원본 텍스트만 있으면 차감 스킵

### 2.3 음수 재고 처리 (Q1)

- 차감 후 `new_balance < 0`이면 감사 로그에 warning 추가
- `material_stock_logs.note`에 "음수 재고 발생" 자동 기록
- UI 측에서 빨간색 음수값으로 명시 표시

## 3. Restock / Adjustment UI

### 3.1 위치

- `/management/materials` (기존 원재료 관리 페이지) 확장
- 각 원재료 행에 현재 재고 / 임계치 / [입고] [조정] 버튼

### 3.2 엔드포인트

```
POST /api/materials/{id}/stock/restock
  body: { amount: float, note?: string }
  -> delta=+amount, reason='restock'

POST /api/materials/{id}/stock/adjust
  body: { new_quantity: float, note: string }  -- 절대값 보정
  -> delta = new_quantity - current, reason='adjust'

POST /api/materials/{id}/stock/discard
  body: { amount: float, note: string }
  -> delta=-amount, reason='discard'

PATCH /api/materials/{id}/stock-threshold
  body: { threshold: float }
```

- 권한: manager 이상 (`require_access_level("manager")`)
- 모든 응답은 업데이트된 stock_quantity + 새 log entry 반환

### 3.3 모달 UX

- **입고**: "얼마나 입고되었나요?" — 숫자 입력 (g), 선택 사항으로 메모
- **조정**: "실제 재고량을 입력해주세요" — 절대값, 메모 필수
- **폐기**: "폐기량" — 숫자, 메모 필수
- **임계치 설정**: 원재료 편집 모달에 필드 추가

## 4. Low-Stock Warning

### 4.1 판정 규칙

- `stock_threshold > 0 AND stock_quantity <= stock_threshold` → **LOW**
- `stock_quantity < 0` → **NEGATIVE** (Q1 경고)
- threshold=0이면 판정 안 함 (Opt-in)

### 4.2 노출 지점

| 위치 | 내용 | 대상 |
|---|---|---|
| Management 대시보드 상단 | "재고 주의: N개 원재료 임계치 미달" 배너 + 목록 링크 | manager |
| Status 페이지 상단 | 동일 배너 (컴팩트) | operator, manager |
| 계량 작업 시작 시 | 해당 레시피에 LOW 원재료 포함 시 작업 카드에 노란 띠 | operator |
| 원재료 관리 행 | LOW는 노란색, NEGATIVE는 빨간색 배경 | manager |

### 4.3 API

```
GET /api/materials/stock
  -> [{ id, name, stock_quantity, stock_threshold, status: 'ok'|'low'|'negative' }, ...]

GET /api/materials/{id}/stock-log?limit=50
  -> [{ id, delta, balance_after, reason, actor_name, note, created_at }, ...]
```

- Status/Management 페이지가 10초 갱신 주기에 포함시켜 호출

## 5. Implementation Order

1. **DB**: `src/database.py`에 컬럼/테이블 마이그레이션 추가 → 서버 재시작으로 적용
2. **차감 서비스**: `src/services/stock_service.py` 신설 (`deduct_stock`, `apply_stock_change`)
3. **계량 라우터 훅**: `recipe_routes.py` 계량 확정 경로에 `deduct_stock` 호출 삽입 (트랜잭션 내)
4. **재고 API**: `src/routers/stock_routes.py` 또는 기존 `material_routes.py` 확장 (4개 엔드포인트 + list + log)
5. **Management UI**: materials 페이지에 재고 컬럼/버튼/모달 추가
6. **경고 배너**: Management/Status 페이지 상단 재고 경고 컴포넌트 추가
7. **계량 카드 경고**: work 페이지에서 LOW 재료 포함 시 노란 띠

## 6. Files to Create / Modify

**신규**
- `src/services/stock_service.py` — 차감/입고/조정/폐기 공통 로직
- `src/routers/stock_routes.py` — 재고 API (선택: `material_routes`로 통합 가능)

**수정**
- `src/database.py` — 컬럼/테이블 마이그레이션
- `src/routers/recipe_routes.py` — 계량 확정 시 `deduct_stock` 호출
- `src/routers/material_routes.py` — GET /api/materials에 stock 필드 추가
- `templates/management/materials.html` — 재고 컬럼/버튼
- `templates/status.html`, `templates/management/dashboard.html` — 경고 배너
- `static/js/materials.js` — 입고/조정/폐기 모달
- `static/js/status.js`, `static/js/work.js` — 경고 표시

## 7. Testing Plan

1. **마이그레이션**: 기존 DB에서 서버 시작 → 모든 materials의 stock=0 확인
2. **입고**: materials 페이지에서 100g 입고 → DB+로그 확인
3. **계량 차감**: 30g 레시피 계량 확정 → stock=70, log=-30 확인
4. **재계량 멱등**: 같은 item 재확정 시도 → 추가 차감 없음
5. **음수**: stock=10인데 50g 차감 → stock=-40, 경고 표시
6. **임계치**: threshold=20, stock=15 → LOW 배너 노출
7. **조정**: 실재고 80g으로 보정 → delta=+40 로그
8. **권한**: operator가 /stock/restock 시도 → 403

## 8. Risks

| 위험 | 완화 |
|------|------|
| 기존 계량 기록 소급 미반영 | 신기능 시점 이후 계량만 차감 (과거 데이터 재처리 안 함, 운영상 감안) |
| `value_text`-only 항목 누락 | 차감 스킵 명시, UI에 안내 문구 |
| 동시 계량 경합 | `with connection:` 트랜잭션 + SQLite 쓰기 직렬화로 충분 (소규모 현장) |
| 재고 통계 부정확 | Q2 결정(0 시작) 감안, 입고 이력이 쌓이기 전까지 배너 문구에 "초기화 단계" 표시 고려 |
