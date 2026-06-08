# 발주 입고·검수 — 완료 보고서 (Report)

| 항목 | 값 |
|------|------|
| Feature | `purchase-order-receiving` (발주 입고·검수) |
| Level | Dynamic |
| 완료일 | 2026-06-06 |
| **Match Rate** | **99%** (Critical 0 / Major 0 / Minor 1·해소) |
| 테스트 | **180 passed** (신규 16 + 기존 164, 회귀 0) |
| PDCA | Plan → Design → Do → Check → (Act 불필요) → Report ✅ |

## 1. 한 줄 요약

ERP로 전송된 발주서(`sent`)의 물품이 실제 도착하면 **한 번의 입고 등록으로 LOT/유통기한과 원재료 재고를 동시에 반영**하고, 발주서의 입고 진행 상태를 추적한다. `material-forecast → order-sheet-erp → [입고] → lot-expiry / stock` 공급망 루프를 완결했다.

## 2. 해결한 문제

| Before | After |
|--------|-------|
| 발주서가 `sent`에서 끝나 입고 사건이 시스템에 없음 | `sent` 발주에 입고 등록 → 발주 항목 자동 채움 |
| LOT 등록·재고 입고를 **두 탭에 따로 수기 입력** | 입고수량 입력 한 번으로 LOT + 재고 **동시 반영(원자적)** |
| 발주-입고 연결 부재("다 왔나?") | `receipt_status` 미입고/부분입고/입고완료 추적 |
| forecast가 입고분 모르고 중복 발주 유발 | 정상 `restock` 경로로 재고 반영 → forecast 정확화 |
| 추적성 단절 | 입고 이력(`RC-…`)이 `lot_id`/`stock_log_id`로 완전 연결 |

## 3. 산출물

### 신규
- `src/services/receiving_service.py` — 입고 오케스트레이션(LOT+재고+이력+상태, caller commits)
- `src/routers/receiving_routes.py` — `POST/GET /api/orders/{id}/receipts` (manager)
- `tests/test_purchase_order_receiving.py` — 16 테스트(서비스 통합·권한·마이그레이션)
- `docs/01-plan|02-design|03-analysis|04-report/...purchase-order-receiving.*`

### 변경
- `src/db/migrations.py` — `_ALLOWED_TABLES`+2, `ensure_column`(receipt_status/received_qty), `po_receipts`/`po_receipt_items` + 인덱스
- `src/db/schema.py` — 신규 DB 경로에 2테이블 미러
- `src/routers/models.py` — `ReceiptLineBody`, `ReceiptCreateBody`
- `src/routers/api.py` — `receiving_router` 등록
- `src/services/order_service.py` — `list_orders`/`get_order`에 `receipt_status(_label)`
- `templates/management.html` — 입고 컬럼/입고 버튼/입고 모달/입고 이력
- `static/js/orders.js` — 입고 배지·모달·확정·이력 로딩

## 4. 핵심 설계 결정

1. **직교 상태 분리** — ERP `status`(draft/sent/failed/cancelled)는 불변. 입고는 별도 `receipt_status`(CHECK 미적용 신규 컬럼)로 기존 흐름 무회귀. (lot-expiry "분리 레이어" 철학 계승)
2. **무재구현** — LOT/재고 변경은 검증된 `lot_service.register_lot` + `stock_service.restock` 재사용. receiving_service는 검증·조립·상태전이만.
3. **원자성** — 라우터가 단일 `with get_connection()` + `commit()` 소유. 예외 시 전체 롤백 → LOT만/재고만 반영되는 부분실패 차단.
4. **부분/분할 입고** — 누적 `received_qty`로 분할 납품 지원. 초과 입고 허용(현장 현실, `restock` note 관용 패턴).

## 5. 검증

- **design-validator**(착수 전): 88/100, Critical 0 → M-1/M-2/m-3/m-4 설계 반영 후 구현.
- **gap-detector**(Check): Match Rate **99%**, Critical/Major 0.
- **단위·통합 테스트**: 신규 16 + 전체 **180 passed**(회귀 0).
- **라이브 HTTP 스모크**: manager 로그인 → `sent` 발주 입고 → LOT 생성·재고 +500·이력 1건·`received` 전이·비전송 거부(400) 전부 통과. (개발 DB 오염분 정리 완료)

## 6. 잔여/후속 (Out of Scope)

- 입고 취소·되돌리기(reverse 경로는 stock/lot에 존재, 본 기능 미노출)
- 검수 반려/불량 처리, 공급사·납품서 매칭·단가·정산
- LOT 기반 FIFO 계량 차감(입고가 선행 조건 — 차기 후보)

## 7. 배운 점

- **직교 상태축 분리**가 기존 CHECK 제약·전송 흐름을 건드리지 않고 새 라이프사이클을 안전하게 얹는 핵심이었다(SQLite CHECK ALTER 불가 우회).
- **standalone 스모크는 `IRMS_DATA_DIR` 미지정 시 개발 DB(`data/irms.db`)를 오염**시킨다. 반드시 임시 DATA_DIR을 지정해야 함([[feedback_browser_smoke_pattern]] 재확인).
- 검증된 서비스를 **재사용**하면 gap이 줄고 회귀가 거의 없다(Match 99%).
