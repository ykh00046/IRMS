# 발주 입고·검수 — Design

| 항목 | 값 |
|------|------|
| Feature | `purchase-order-receiving` |
| Phase | Design |
| 작성일 | 2026-06-06 |
| 선행 | `docs/01-plan/features/purchase-order-receiving.plan.md` |

## 1. 아키텍처 개요

기존 2-tier(FastAPI + SQLite + Jinja2)를 따른다. 입고는 **기존 두 서비스를 오케스트레이션**하는
얇은 레이어다 — LOT 생성/재고 증가 로직을 재구현하지 않고 `lot_service`·`stock_service`를 호출한다.

```
발주서(purchase_orders, status='sent')
        │  발주 항목(order_qty>0)을 입고 폼에 자동 채움
        ▼
src/services/receiving_service.py        (입고 오케스트레이션, 신규)
        │  항목별로:
        ├─▶ lot_service.register_lot()    → material_lots (LOT/유통기한)   ─┐ 한
        ├─▶ stock_service.restock()       → materials.stock_quantity ↑     ─┤ 트랜
        └─▶ po_receipt_items 기록(lot_id, stock_log_id)                     ─┘ 잭션
        ▼
po_receipts / po_receipt_items            (SQLite, 신규 2테이블)
purchase_orders.receipt_status            (신규 컬럼: pending/partial/received)
purchase_order_items.received_qty         (신규 컬럼: 입고 누적량)
        ▼
src/routers/receiving_routes.py           (manager scope, 신규)
        POST /orders/{order_id}/receipts          입고 등록
        GET  /orders/{order_id}/receipts          입고 이력 조회
        ▼
templates/management.html  "발주서" 탭 → 입고 모달 + 이력 + 상태 배지
static/js/orders.js  입고 UI 로직 확장
```

### 설계 원칙
- **무재구현**: LOT·재고 변경은 `lot_service.register_lot`·`stock_service.restock` 재사용. receiving_service는 검증·조립·상태전이만.
- **원자성**: 라우터가 `with get_connection()` 한 블록에서 모든 항목 처리 후 단일 `commit()`. 예외 시 전체 롤백.
- **직교 상태**: ERP `status`(draft/sent/failed/cancelled)는 불변. 입고는 독립 `receipt_status`.

## 2. 데이터 모델 (마이그레이션 + 스키마)

`migrations.py` `_ALLOWED_TABLES`에 `po_receipts`, `po_receipt_items` 추가.
`apply_schema_migrations()`에 `ensure_column` 2개 + `CREATE TABLE` 2개 + 인덱스.
`schema.py` 신규 DB 초기화 경로에도 동일 정의(2테이블 + 2컬럼).

### 2.1 `purchase_orders` 컬럼 추가 (ensure_column)

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `receipt_status` | `TEXT NOT NULL DEFAULT 'pending'` | pending / partial / received (앱 enforce, CHECK 미적용 — ALTER 호환) |

### 2.2 `purchase_order_items` 컬럼 추가 (ensure_column)

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `received_qty` | `REAL NOT NULL DEFAULT 0` | 해당 항목 입고 누적량(g) |

### 2.3 `po_receipts` (입고 헤더, 신규)

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `receipt_no` | TEXT NOT NULL UNIQUE | `RC-YYYYMMDD-NNN` 채번 |
| `order_id` | INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE | 대상 발주 |
| `note` | TEXT | 입고 비고 |
| `item_count` | INTEGER NOT NULL DEFAULT 0 | 입고 항목 수 |
| `total_qty` | REAL NOT NULL DEFAULT 0 | 입고 총량(g) |
| `received_by` | TEXT NOT NULL | 입고 실행자 표시명 |
| `received_at` | TEXT NOT NULL | UTC ISO |

### 2.4 `po_receipt_items` (입고 항목, 신규)

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `receipt_id` | INTEGER NOT NULL REFERENCES po_receipts(id) ON DELETE CASCADE | |
| `order_item_id` | INTEGER NOT NULL REFERENCES purchase_order_items(id) | 어느 발주 항목 입고분 |
| `material_id` | INTEGER NOT NULL | 자재(비정규화) |
| `material_name` | TEXT NOT NULL | 자재명 스냅샷 |
| `received_qty` | REAL NOT NULL | 이번 입고 수량(g) |
| `lot_no` | TEXT | LOT 번호(선택) |
| `expiry_date` | TEXT | 유통기한(선택) |
| `lot_id` | INTEGER | 생성된 material_lots.id (추적) |
| `stock_log_id` | INTEGER | 생성된 material_stock_logs.id (추적) |
| `note` | TEXT | 항목 비고 |

### 2.5 인덱스

```sql
CREATE INDEX IF NOT EXISTS idx_po_receipts_order ON po_receipts(order_id);
CREATE INDEX IF NOT EXISTS idx_po_receipt_items_receipt ON po_receipt_items(receipt_id);
```

## 3. 서비스 — `src/services/receiving_service.py`

```python
class ReceivingStateError(Exception): ...   # sent 아닌 발주 입고 시도

def generate_receipt_no(connection, today=None) -> str
    # RC-YYYYMMDD-NNN, 같은 날 마지막 +1 (order_service.generate_order_no 패턴)

def receive_order(connection, *, order_id, lines, received_by, actor, note=None, now=None) -> dict
    # lines: [{order_item_id, received_qty, lot_no?, expiry_date?, note?}, ...]
    # 1) 발주 로드. 없으면 None. status != 'sent' → ReceivingStateError
    # 2) 유효 order_item_id 집합 검증(다른 발주 항목 거부)
    # 3) received_qty>0 인 라인만 처리. 0/음수 라인 skip
    # 4) 처리할 라인 0건 → ValueError("입고할 수량이 없습니다.")
    # 5) po_receipts 헤더 INSERT (receipt_no 채번)
    # 6) 각 라인:
    #      lot = lot_service.register_lot(connection, material_id, lot_no,
    #                quantity=received_qty, received_at=now_date,
    #                expiry_date, actor, note)
    #      stock = stock_service.restock(connection, material_id=…,
    #                amount=received_qty, actor=actor, note="발주 입고: {receipt_no}")
    #      po_receipt_items INSERT (lot_id=lot["lot_id"], stock_log_id=stock["log_id"])
    #      purchase_order_items.received_qty += received_qty
    # 7) 헤더 item_count/total_qty 갱신
    # 8) receipt_status 재계산 → purchase_orders 갱신
    # 9) caller commits
    # 반환: {receipt_no, receipt_id, order_id, receipt_status, item_count, total_qty, lines:[…]}

def _recompute_receipt_status(connection, order_id) -> str
    # 발주 항목 전체에 대해:
    #   any(received_qty>0) == False → 'pending'
    #   all(received_qty >= order_qty) for order_qty>0 items → 'received'
    #   else → 'partial'

def list_receipts(connection, order_id) -> list[dict]
    # 입고 헤더 + 항목 묶어 반환(발주 상세 이력용)
```

### 3.1 핵심 호출 계약(기존 서비스)
- `lot_service.register_lot(connection, *, material_id, lot_no, quantity, received_at, expiry_date, actor, note)` → `{lot_id, …}` · caller commits
- `stock_service.restock(connection, *, material_id, amount, actor, note=None)` → `{log_id, balance_before, balance_after, delta}` · caller commits
- 두 서비스 모두 `material_id` 검증/로깅 내장 → receiving_service는 자재 존재만 보장(발주 항목이 보증)

## 4. 라우터 — `src/routers/receiving_routes.py`

`build_router() -> APIRouter` (manager scope, `order_routes` 패턴). `api.py`에 등록.

| Method | Path | 동작 |
|--------|------|------|
| POST | `/orders/{order_id}/receipts` | 입고 등록. body=`ReceiptCreateBody`. 201. 감사로그 `order_receive` |
| GET | `/orders/{order_id}/receipts` | 입고 이력 조회 `{receipts:[…]}` |

오류 매핑: 발주 없음→404, `ReceivingStateError`→400, `ValueError`→400.
감사로그: `action="order_receive"`, target_type=`purchase_order`, details={receipt_no, item_count, total_qty, receipt_status}.

### 4.1 모델 — `src/routers/models.py`

```python
class ReceiptLineBody(BaseModel):
    order_item_id: int = Field(gt=0)
    received_qty: float = Field(ge=0)
    lot_no: str | None = Field(default=None, max_length=100)
    expiry_date: str | None = None
    note: str | None = None

class ReceiptCreateBody(BaseModel):
    note: str | None = None
    lines: list[ReceiptLineBody] = Field(default_factory=list)
```

## 5. UI — management.html / orders.js

### 5.1 발주서 목록
- 기존 발주 목록 행에 `receipt_status` 배지 추가: 미입고(회색)/부분입고(주황)/입고완료(녹색).
- 상태 라벨: `pending→미입고`, `partial→부분입고`, `received→입고완료`.

### 5.2 발주 상세(기존 모달/패널)
- `status='sent'`일 때 **"입고" 버튼** 노출(draft/cancelled에는 미노출).
- 입고 모달: 발주 항목 표 `자재명 | 발주량 | 기입고 | 잔여 | [입고수량] | [LOT번호] | [유통기한]`.
  - 잔여 = `order_qty - received_qty`. 입고수량 기본값 = 잔여(0이면 빈칸).
- "입고 확정" → `POST /orders/{id}/receipts`. **CSRF: `x-csrftoken` 헤더 직접 부착**([[project_management_csrf]]).
- 입고 후: 발주 상세/목록 새로고침, LOT 탭·재고 탭에도 반영(다음 조회 시).
- 발주 상세 하단 "입고 이력" 표: 입고번호·일자·항목수·총량.

### 5.3 CSS
- 신규 class 금지. 기존 `.input` / `.filter-label` / `.btn` / 배지 패턴 재사용([[feedback_common_form_css]]).
- 모달은 `hidden` 속성 토글([[feedback_css_hidden]]).

## 6. 구현 순서

1. `migrations.py`: `_ALLOWED_TABLES` += 2, `ensure_column` 2, `CREATE TABLE` 2 + 인덱스.
2. `schema.py`: 신규 DB 경로에 동일 2테이블 + receipt_status/received_qty 컬럼 반영.
3. `services/receiving_service.py` 작성.
4. `models.py`: `ReceiptLineBody`, `ReceiptCreateBody`.
5. `routers/receiving_routes.py` 작성 + `api.py` 등록.
6. `order_service.list_orders`/`get_order`에 `receipt_status` 포함(SELECT * 라 get_order는 자동, list_orders는 컬럼 추가).
7. `templates/management.html` + `static/js/orders.js` 입고 UI.
8. `tests/test_purchase_order_receiving.py`.

## 7. 테스트 설계 — `tests/test_purchase_order_receiving.py`

| # | 케이스 | 기대 |
|---|--------|------|
| 1 | sent 발주 전량 입고 | receipt_status='received', LOT 생성, 재고 += 입고량 |
| 2 | 부분 입고(일부 항목/수량) | receipt_status='partial', 잔여 남음 |
| 3 | 분할 입고 2회 → 누적 충족 | 1회차 partial → 2회차 received, received_qty 누적 |
| 4 | LOT/재고 동시 반영 검증 | material_lots 1행 + materials.stock_quantity 증가 동시 |
| 5 | received_qty=0 라인 skip | LOT/재고 미생성 |
| 6 | draft/cancelled 발주 입고 시도 | ReceivingStateError(400) |
| 7 | 다른 발주의 order_item_id 혼입 | 무시/거부 |
| 8 | 입고 가능 수량 0건(전부 0) | ValueError(400) |
| 9 | 유통기한/LOT 미입력 입고 | 정상(no_expiry LOT 생성) |
| 10 | 입고번호 채번 RC-YYYYMMDD-NNN | 같은 날 2건 → 001,002 |
| 11 | 입고 이력 조회 | 헤더+항목 반환, lot_id/stock_log_id 연결 |
| 12 | 초과 입고(잔여 초과) 허용 | received 전이 + 재고 반영 |
| 13 | 권한: 비인증 입고(POST/GET) | 401/403 |
| 14 | 마이그레이션: po_receipts/po_receipt_items + 컬럼 | 테이블 2 + receipt_status/received_qty |
| (R6 확장) | draft/cancelled/**failed** 입고 거부 | status 파라미터 ×3 |
| (R10) | 없는 발주 입고 | None 반환 |

> 실제 구현은 위 14개 함수 + R6의 status 파라미터(draft/cancelled/failed) 확장으로 **pytest 16개 수집·전부 통과**. 감사 로그(order_receive)는 라이브 HTTP 스모크(로그인→입고→LOT/재고/이력)로 추가 검증.

## 8. 회귀·리스크

| 리스크 | 완화 |
|--------|------|
| 부분 실패(LOT만/재고만) | 단일 트랜잭션 + 예외 시 롤백. 라우터가 commit 소유 |
| 기존 발주 CHECK 제약 충돌 | `receipt_status`는 CHECK 없는 신규 컬럼. `status` 미변경 |
| 음수/초과 입고 | received_qty≤0 skip, 초과는 허용(restock note 패턴) |
| CSRF 403 | orders.js 쓰기 요청에 `x-csrftoken` 직접 부착 |
| forecast 영향 | 재고 증가는 정상 경로(restock). forecast가 입고분 인식 → 의도된 개선 |

## 9. 설계 검증 반영 (design-validator, 2026-06-06)

design-validator 88/100(Critical 0, Major 2) 결과를 다음과 같이 확정 반영한다.

| # | 지적 | 확정 결정 |
|---|------|----------|
| M-1 | schema.py 컬럼 중복 정의 모호 | **신규 2컬럼(`receipt_status`/`received_qty`)은 migration `ensure_column`에만 정의**. schema.py CREATE에는 넣지 않음. **신규 2테이블(`po_receipts`/`po_receipt_items`)은 schema.py executescript + migrations 양쪽**에 `CREATE TABLE IF NOT EXISTS`로 정의(purchase_orders 선례) |
| M-2 | `received` 빈 시퀀스 경계 | `_recompute_receipt_status`: ① 어떤 항목도 `received_qty>0` 아니면 `pending` ② `order_qty>0` 항목이 존재하고 그 전부 `received_qty>=order_qty`면 `received` ③ 그 외 `partial`. sent 발주는 send 시 `order_qty>0` ≥1건 보장(order_routes:121) |
| m-3 | `now`(datetime) → `register_lot.received_at`(date) 변환 | `register_lot(received_at=None)`로 호출 → lot_service가 `date.today()` 기본 적용(datetime ISO를 date.fromisoformat에 넘기지 않음). po_receipts.received_at 헤더만 `utc_now_text()` 사용 |
| m-4 | `po_receipt_items.material_name` 출처 | **발주 항목 `purchase_order_items.material_name` 스냅샷 복사**(발주-입고 일관성) |
| m-1 | api.py 등록 위치 | `receiving_router`를 `order_router` 등록부 인접에 추가 |
| m-2 | SC1/잔여 계산 테스트 | 테스트 #11에 `received_qty` 누적·잔여 검증 흡수 + 빈-orderable 경계는 #6/#8로 커버 |
