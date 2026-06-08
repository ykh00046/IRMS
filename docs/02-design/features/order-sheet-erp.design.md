# 발주서 생성·ERP 연동 — Design

| 항목 | 값 |
|------|------|
| Feature | `order-sheet-erp` |
| Phase | Design |
| 작성일 | 2026-06-02 |
| 선행 | `docs/01-plan/features/order-sheet-erp.plan.md` |

## 1. 아키텍처 개요

기존 2-tier(FastAPI + SQLite + Jinja2) 패턴을 따른다. forecast 산출물을 입력으로
받아 발주서를 스냅샷 저장하고, 출력(Excel/인쇄)·전송(ERP)을 담당한다.

```
forecast_service.compute_forecast()  ─(권장량·긴급도)─┐
                                                      ▼
                          src/services/order_service.py      (발주서 생성/조회/수정/스냅샷/Excel)
                          src/services/erp_client.py         (HTTP POST + Mock 폴백)
                                                      ▼
                          src/routers/order_routes.py        (manager scope)
                              POST   /orders
                              GET    /orders
                              GET    /orders/{id}
                              PATCH  /orders/{id}
                              POST   /orders/{id}/send
                              POST   /orders/{id}/cancel
                              GET    /orders/{id}/export.xlsx
                              GET    /orders/{id}/print          (HTMLResponse → 브라우저 PDF)
                                                      ▼
                          purchase_orders / purchase_order_items   (SQLite, 신규 2테이블)
                          templates/management.html  탭 "발주서"
                          templates/order_print.html (인쇄 전용)
                          static/js/orders.js
```

## 2. 데이터 모델 (마이그레이션 + 스키마)

`migrations.py`의 `_ALLOWED_TABLES`에 `purchase_orders`, `purchase_order_items` 추가.
`apply_schema_migrations()`에 `CREATE TABLE IF NOT EXISTS` 2개 + 인덱스 추가.
`schema.py`에도 동일 정의(신규 DB 초기화 경로 일치).

### 2.1 `purchase_orders`

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `order_no` | TEXT NOT NULL UNIQUE | `PO-YYYYMMDD-NNN` 채번 |
| `status` | TEXT NOT NULL DEFAULT 'draft' | draft / sent / failed / cancelled |
| `window_days` | INTEGER NOT NULL | 생성 시 forecast 분석기간 |
| `note` | TEXT | 발주서 비고 |
| `item_count` | INTEGER NOT NULL DEFAULT 0 | 항목 수(스냅샷) |
| `total_qty` | REAL NOT NULL DEFAULT 0 | 발주 총수량(g) |
| `created_by` | TEXT NOT NULL | 작성자 표시명 |
| `created_at` | TEXT NOT NULL | UTC ISO |
| `updated_at` | TEXT | 마지막 수정 |
| `sent_at` | TEXT | ERP 전송 시각 |
| `sent_by` | TEXT | 전송 실행자 |
| `erp_mode` | TEXT | 'http' / 'mock' |
| `erp_status_code` | INTEGER | HTTP 응답 코드(mock=200) |
| `erp_response` | TEXT | 응답 본문(앞 1000자) |

### 2.2 `purchase_order_items`

| 컬럼 | 정의 | 의미 |
|------|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `order_id` | INTEGER NOT NULL → purchase_orders(id) | |
| `material_id` | INTEGER NOT NULL | 참조용(스냅샷이라 FK 강제 안 함) |
| `material_name` | TEXT NOT NULL | 생성 시점 자재명(스냅샷) |
| `category` | TEXT | 스냅샷 |
| `unit` | TEXT NOT NULL DEFAULT 'g' | |
| `stock_quantity` | REAL | 생성 시점 현재고 |
| `avg_daily` | REAL | 생성 시점 일평균 소모 |
| `days_remaining` | REAL | 생성 시점 잔여일수 |
| `predicted_stockout_date` | TEXT | 생성 시점 예상 소진일 |
| `urgency_status` | TEXT | urgent / soon |
| `recommended_qty` | REAL NOT NULL | forecast 권장량(불변) |
| `order_qty` | REAL NOT NULL | 실제 발주량(편집 가능, 초기=권장량) |
| `note` | TEXT | 항목 비고 |

인덱스: `idx_po_items_order ON purchase_order_items(order_id)`,
`idx_po_status_created ON purchase_orders(status, created_at DESC)`.

## 3. 서비스 (order_service.py)

순수 DB/계산 로직. 라우터는 `def`(스레드풀) — [[project_async_db_threadpool]] 정합.

```python
def generate_order_no(connection, today) -> str
    # PO-YYYYMMDD-NNN. 같은 날짜 prefix의 MAX(seq)+1, 001부터. today는 호출자가 주입(테스트성).

def create_order_from_forecast(connection, *, window_days, created_by, today) -> dict
    # forecast_service.compute_forecast → urgent/soon 항목만 스냅샷.
    # 0건이면 ValueError("발주 권장 자재가 없습니다.").
    # purchase_orders(draft) + items insert, item_count/total_qty 집계. 생성된 order dict 반환.

def list_orders(connection, *, limit=100) -> list[dict]
    # status, created_at DESC 정렬 목록(헤더만).

def get_order(connection, order_id) -> dict | None
    # 헤더 + items[] 포함. 없으면 None.

def update_order(connection, order_id, *, note, items, now) -> dict
    # draft만 허용(아니면 OrderStateError). items=[{id, order_qty, note}].
    # order_qty<0 거부. 0이면 제외표시(삭제는 아니고 order_qty=0 → 출력/전송에서 필터).
    # item_count/total_qty 재집계(order_qty>0만), updated_at 갱신.

def cancel_order(connection, order_id, *, now) -> dict
    # draft/failed만 취소 가능. sent는 거부(OrderStateError).

def mark_sent(connection, order_id, *, result, now, sent_by) -> dict
    # result(ErpResult) 반영: status=sent/failed, erp_* 컬럼 기록.

def build_workbook(order) -> bytes
    # openpyxl. 헤더 메타(발주번호/일자/작성자/비고) + 품목 표 + 합계행.
    # _xlsx_safe(): '=','+','-','@' 시작 문자열은 "'" 접두(수식 인젝션 방어).

def order_payload(order) -> dict
    # ERP 전송/인쇄 공용 직렬화. order_qty>0 항목만 포함.
```

예외: `OrderStateError(Exception)` — 상태 전이 위반(400 매핑).

## 4. ERP 클라이언트 (erp_client.py)

```python
@dataclass
class ErpResult:
    ok: bool
    mode: str          # 'http' | 'mock'
    status_code: int
    body: str

def send_order(payload: dict) -> ErpResult
    # IRMS_ERP_ENDPOINT 미설정 → ErpResult(ok=True, mode='mock', 200, '{"mock": true}').
    # 설정 시 httpx.post(endpoint, json=payload, headers={Authorization: Bearer KEY?}, timeout).
    #   2xx → ok=True mode='http'. 그 외/예외 → ok=False, body=에러요약(앞 1000자).
```

config.py 추가:
```python
ERP_ENDPOINT = os.getenv("IRMS_ERP_ENDPOINT", "").strip()
ERP_API_KEY = os.getenv("IRMS_ERP_API_KEY", "").strip()
ERP_TIMEOUT = float(os.getenv("IRMS_ERP_TIMEOUT", "10"))
```

## 5. API (order_routes.py)

`require_access_level("manager")` 의존성으로 라우터 전체 보호. `api.py`에 등록.
쓰기 라우트는 `get_current_user` + `write_audit_log` + `commit`.

| Method | Path | 설명 | Body/응답 |
|--------|------|------|-----------|
| POST | `/orders` | forecast 스냅샷으로 발주서 생성 | `OrderCreateBody{window_days}` → 201 order |
| GET | `/orders` | 발주서 목록 | → `{orders:[...]}` |
| GET | `/orders/{id}` | 발주서 상세(+items) | → order |
| PATCH | `/orders/{id}` | 수량/비고 수정(draft) | `OrderUpdateBody{note?, items:[{id,order_qty,note?}]}` |
| POST | `/orders/{id}/send` | ERP 전송 | → `{status, erp_mode, erp_status_code}` |
| POST | `/orders/{id}/cancel` | 발주서 취소 | → order |
| GET | `/orders/{id}/export.xlsx` | Excel 다운로드 | StreamingResponse(xlsx) |
| GET | `/orders/{id}/print` | 인쇄용 HTML | HTMLResponse |

models.py 추가:
```python
class OrderCreateBody(BaseModel):
    window_days: int = Field(default=30, ge=7, le=365)

class OrderItemEditBody(BaseModel):
    id: int = Field(gt=0)
    order_qty: float = Field(ge=0)
    note: str | None = None

class OrderUpdateBody(BaseModel):
    note: str | None = None
    items: list[OrderItemEditBody] = Field(default_factory=list)
```

상태 전이 위반(`OrderStateError`)·빈 발주(`ValueError`) → `HTTPException(400)`.
없는 id → 404. 이미 sent 재전송 → 400("이미 전송된 발주서입니다.").

`send`는 라우트 안에서 `order_service.order_payload` → `erp_client.send_order` →
`order_service.mark_sent` 순서. httpx 호출은 라우트가 `def`라 스레드풀에서 안전.

Excel/print 응답 헤더: `Content-Disposition: attachment; filename="{order_no}.xlsx"`.

## 6. UI (management.html + orders.js + order_print.html)

### 6.1 탭 추가
forecast 탭 옆: `<button class="mgmt-tab" data-tab="orders">발주서</button>`,
패널 `<div class="tab-panel" id="tab-orders">`.

### 6.2 패널 구성 (기존 .panel/.input/.filter-label/.btn 재사용)
- 상단: 분석기간 select(30/60/90) + "발주 권장에서 생성" 버튼 + 새로고침.
- 목록 표: 발주번호 / 일자 / 항목수 / 총수량(g) / 상태 배지 / 작성자 / 작업(상세).
- 상세(모달 `#order-modal`, `hidden` 속성 토글): 헤더 메타 + 항목 표(품목/권장량/발주량 input/비고) +
  비고 textarea + 버튼(저장/Excel/인쇄/ERP 전송/취소). 상태가 draft가 아니면 입력·전송·저장 비활성.
- 상태 배지: draft=작성중, sent=전송됨, failed=실패, cancelled=취소.

### 6.3 orders.js (stock.js/forecast.js와 동일 IIFE 구조)
- `IRMS.notify`, escapeHtml, fmt 재사용.
- **CSRF**: 쓰기(POST/PATCH)는 `x-csrftoken` 헤더 직접 부착 — [[project_management_csrf]] 준수(IRMS.request 미로드 환경).
- Excel/인쇄: `window.open('/api/orders/{id}/export.xlsx')` / `/print`.
- 탭 클릭(`[data-tab="orders"]`) 시 목록 로드.

### 6.4 order_print.html
인쇄 전용 최소 템플릿(@media print 친화). 발주번호·일자·작성자·품목표·합계·비고.
`order_routes`가 `BASE_DIR/templates` 기준 `Jinja2Templates`로 렌더(HTMLResponse).

### 6.5 스크립트 등록
`<script src="/static/js/orders.js">`를 forecast.js 옆에 추가.

## 7. 권한·보안

- 모든 order 엔드포인트 manager 이상(operator 403).
- 쓰기(POST/PATCH/send/cancel) audit log: `order_create`/`order_update`/`order_send`/`order_cancel`.
- Excel/CSV 수식 인젝션 방어(`_xlsx_safe`).
- 쓰기 CSRF: 미들웨어 보호 + JS가 헤더 부착.
- ERP API 키는 환경변수, 응답 본문 저장 시 1000자 제한(로그 비대 방지).
- 멱등: sent 상태 재전송/수정 차단.

## 8. 테스트 설계 (tests/test_order_sheet_erp.py)

in-memory SQLite + 시드. forecast 테스트 픽스처 패턴 재사용.

| # | 시나리오 | 기대 |
|---|---------|------|
| 1 | urgent/soon 자재 존재 → 발주서 생성 | draft, item_count>0, order_qty==recommended_qty |
| 2 | 발주 권장 0건 | ValueError("발주 권장 자재가 없습니다.") |
| 3 | 발주번호 채번 | 같은 날 2건 → PO-...-001, -002 |
| 4 | draft 수량 수정 | order_qty/total_qty 갱신, 0 입력은 합계 제외 |
| 5 | sent 상태 수정 시도 | OrderStateError |
| 6 | send (mock 모드) | status=sent, erp_mode=mock, erp_status_code=200 |
| 7 | sent 재전송 | 400/거부 |
| 8 | cancel (draft) | status=cancelled |
| 9 | cancel (sent) | 거부 |
| 10 | operator 권한 | 403 |
| 11 | Excel 생성 | 비어있지 않은 xlsx 바이트, 수식 인젝션 접두 |
| 12 | order_payload | order_qty>0 항목만, 필수 키 포함 |

## 9. 변경 파일 목록

| 파일 | 변경 |
|------|------|
| `src/db/schema.py` | 신규 테이블 2개 CREATE |
| `src/db/migrations.py` | _ALLOWED_TABLES 2개 + CREATE TABLE/인덱스 |
| `src/config.py` | ERP_ENDPOINT/API_KEY/TIMEOUT |
| `src/services/order_service.py` | **신규** |
| `src/services/erp_client.py` | **신규** |
| `src/routers/order_routes.py` | **신규** |
| `src/routers/api.py` | order 라우터 등록 |
| `src/routers/models.py` | OrderCreateBody/OrderItemEditBody/OrderUpdateBody |
| `templates/management.html` | 탭 버튼 + 패널 + 모달 + script |
| `templates/order_print.html` | **신규** 인쇄 전용 |
| `static/js/orders.js` | **신규** |
| `tests/test_order_sheet_erp.py` | **신규** |

## 10. 회귀 방지

- forecast/재고/계량은 **읽기만**(compute_forecast 호출). 기존 로직 무변경.
- 신규 테이블만 추가, 기존 테이블 스키마 무변경.
- 라우터 추가만 → 기존 엔드포인트 영향 없음.
- ERP 미설정 시 Mock 폴백으로 외부 호출 0(테스트/운영 초기 안전).
