# 발주 입고·검수 — Plan

> ERP로 전송된 발주서(`sent`)에 대해, 물품이 실제 도착하면 **입고(검수)**를 등록하여
> LOT·유통기한(`lot_service`)과 원재료 재고(`stock_service`)를 **한 번의 작업으로 동시에 반영**하고,
> 발주서의 입고 진행 상태를 추적한다. 발주 → 입고 → 재고/LOT 으로 공급망 루프를 완결한다.

## 1. Overview

| 항목 | 값 |
|------|------|
| Feature | `purchase-order-receiving` |
| Phase | Plan |
| 작성일 | 2026-06-06 |
| Priority | **High** (발주 사이클 완결, 3개 기능 통합) |
| Level | Dynamic |
| Base | `order_service`(발주서 sent), `lot_service.register_lot()`, `stock_service.restock()` |
| Goal | 발주서 항목을 **입고 폼에 자동 채움 → 입고수량·LOT·유통기한 입력 → LOT 생성 + 재고 증가 동시 처리 → 발주서 입고상태 갱신** |
| 선행 완료 | `order-sheet-erp`(2026-06-02), `lot-expiry-tracking`(2026-06-02), `material-forecast`(2026-06-01) |

## 2. Problem Statement

공급망 루프의 현재 단절:

```
material-forecast ─▶ order-sheet-erp ─▶  ❌ [단절]  ─▶ lot-expiry-tracking (수동)
 (무엇을 얼마나)      (발주 draft→sent)                 + stock_service.restock (수동)
```

`order_service`의 상태머신은 `draft → sent/failed → cancelled`로 끝난다.
ERP에 발주를 전송한 뒤 **물품이 실제 입고되는 사건이 시스템에 존재하지 않는다.**

### Pain Points

1. **이중 입력** — 발주서에 "원재료·수량"이 이미 있는데, 입고 시 책임자가 LOT 탭에서 자재를 다시 골라 수량·LOT·유통기한을 **수기 재입력**한다. (`lot_service.register_lot`은 발주와 무관하게 동작)
2. **재고·LOT 따로 입력** — 입고분을 `원재료 재고` 탭(`restock`)과 `유통기한·LOT` 탭(`register_lot`)에 **두 번** 입력해야 한다. 한쪽만 하면 정합성이 깨진다.
3. **발주-입고 연결 부재** — "이 발주가 다 들어왔는가? 부분만 왔는가?"를 알 방법이 없다. 발주서는 영원히 `sent`로 남는다.
4. **forecast 악순환** — 입고가 재고에 반영 안 되면 forecast는 입고된 자재를 계속 "부족"으로 판단해 중복 발주를 유발한다.
5. **추적성 단절** — "어느 발주의 어느 LOT이 언제 들어왔는지"가 끊겨 있다.

## 3. Feature Items

### 3.1 입고 등록 (발주서 → 입고)

| Item | Detail |
|------|--------|
| 대상 | `status='sent'` 발주서만 입고 가능 (발주가 실제 전송된 건) |
| 자동 채움 | 발주 항목(`order_qty>0`)이 입고 폼에 자재명·발주량·기입고량·잔여량과 함께 표시 |
| 입력 | 항목별 **입고수량**(필수), **LOT 번호**(선택), **유통기한**(선택), 비고(선택) |
| 동시 반영 | 입고수량>0 항목마다 ① `lot_service.register_lot()`로 LOT 생성 ② `stock_service.restock()`로 재고 증가 — **한 트랜잭션** |
| 부분 입고 | 한 발주서를 여러 번 나눠 입고 가능(분할 납품). 입고 이력 누적 |
| 권한 | manager 이상 |

### 3.2 입고 상태 추적 (ERP 상태와 직교)

| Item | Detail |
|------|--------|
| 별도 축 | ERP 전송 상태(`status`)와 **분리된** `receipt_status` 컬럼 도입: `pending`(미입고) / `partial`(부분입고) / `received`(입고완료) |
| 판정 | 발주 항목 전부 `received_qty >= order_qty` → `received`, 일부만 입고 → `partial`, 전혀 없음 → `pending` |
| 누적 | `purchase_order_items.received_qty`에 입고 누적량 기록 |
| 비고 | lot-expiry-tracking이 "재고와 분리된 레이어"를 둔 철학과 동일 — ERP 축을 건드리지 않음(CHECK 제약·기존 흐름 무회귀) |

### 3.3 입고 이력 (추적성)

| Item | Detail |
|------|--------|
| 입고 단위 | 한 번의 입고 작업 = `po_receipt` 1건(헤더) + 항목별 `po_receipt_item` N건 |
| 입고번호 | `RC-YYYYMMDD-NNN` 자동 채번 |
| 연결 | 각 입고 항목이 생성한 `lot_id`(material_lots) / `stock_log_id`(material_stock_logs) 를 저장 → 완전 추적 |
| 조회 | 발주서 상세에서 "입고 이력" 표시(입고번호·일자·항목·수량·LOT) |

### 3.4 입고 화면 (책임자 전용)

| Item | Detail |
|------|--------|
| 위치 | `/management` "발주서" 탭의 발주 상세에 **"입고" 액션** 추가(신규 탭 아님, 발주 맥락 유지) |
| 목록 | 발주서 목록에 `receipt_status`(미입고/부분입고/입고완료) 배지 표시 |
| 입고 모달 | 발주 항목 표(발주량/기입고/잔여) + 항목별 입고수량·LOT·유통기한 입력 → "입고 확정" |
| 이력 | 발주 상세에 누적 입고 이력 표 |

## 4. Scope

### In Scope
- 입고 이력 테이블 신설(`po_receipts`, `po_receipt_items`)
- `purchase_orders.receipt_status`, `purchase_order_items.received_qty` 컬럼 추가(`ensure_column`)
- 입고 등록 서비스(`receiving_service`): 발주 항목 검증 → LOT 생성 + 재고 증가 + 입고 이력 + 상태 갱신을 **원자적**으로 처리
- `lot_service.register_lot()` / `stock_service.restock()` **재사용**(중복 구현 없음)
- 발주서 상세 입고 모달 + 입고 이력 + 목록 배지(orders.js / management.html 확장)
- 감사 로그(입고 등록)
- pytest 단위/통합 테스트(부분입고·완전입고·재고/LOT 동시반영·권한·상태전이·회귀)

### Out of Scope
- 발주 없이 임의 입고(기존 LOT 탭 수동 등록 경로는 그대로 유지)
- 입고 검수 반려/불량 처리 워크플로(수량/LOT만, 품질판정 제외)
- 입고 취소·되돌리기(차기 — 우선 정방향 입고 우선. reverse는 stock/lot에 경로 존재하나 본 기능 미노출)
- 공급사·납품서 매칭, 단가·정산
- LOT 기반 FIFO 계량 차감(별도 기능, 입고가 선행 조건)

## 5. Success Criteria

1. `sent` 발주서에 대해 입고 폼이 발주 항목으로 자동 채워진다(발주량·기입고·잔여 표시).
2. 입고수량>0 항목마다 LOT(`material_lots`)이 생성되고 재고(`materials.stock_quantity`)가 증가하며, 둘이 같은 트랜잭션에서 처리된다(한쪽만 반영되는 일 없음).
3. 부분 입고 시 `receipt_status='partial'`, 전 항목 충족 시 `received`로 전이된다.
4. 입고 이력(`RC-…`)이 LOT·재고로그와 연결되어 발주 상세에서 조회된다.
5. 입고는 `sent` 상태에서만 가능하고(draft/cancelled 거부), manager 미만은 접근 불가.
6. 모든 입고가 감사 로그에 남는다.
7. 기존 발주/LOT/재고/계량/forecast 기능에 회귀가 없다(전체 pytest 통과).

## 6. Design Decisions (자율 판단)

| 결정 | 선택 | 근거 |
|------|------|------|
| 입고 상태 모델링 | ERP `status`와 **분리된** `receipt_status` 컬럼 | ERP 전송 ≠ 물리 입고. 직교 축으로 분리하면 기존 CHECK 제약·전송 흐름 무회귀. [[project_lot_expiry_tracking]]의 "분리 레이어" 철학 계승 |
| LOT/재고 반영 | 기존 `register_lot` + `restock` **재사용** | 검증·로깅·음수처리 로직이 이미 검증됨. 중복 구현은 정합성 위험 |
| 원자성 | 한 입고 = 한 트랜잭션(caller commits) | LOT만 생기고 재고는 안 느는 부분실패 방지. 두 서비스 모두 "caller owns transaction" 계약 |
| 부분 입고 | 누적 `received_qty` + 분할 입고 허용 | 분할 납품이 현장 현실. 1발주=1입고 가정은 비현실적 |
| 초과 입고 | 허용하되 잔여 초과분은 경고만(차단 X) | 실제 납품은 발주량과 다를 수 있음. 음수재고 note 패턴과 동일 관용 |
| 입고 단위 | receipt 헤더 + 항목(2테이블) | 한 번에 여러 자재 입고. 추적·감사 단위 명확 |
| UI 위치 | 발주서 탭 내 입고 모달 | 신규 탭 회피. 발주 맥락에서 입고가 자연스러움. [[project_management_csrf]] 헤더 규칙 준수 |
| 입고번호 | `RC-YYYYMMDD-NNN` | 발주 `PO-` 채번 패턴 계승. 사람이 읽는 추적번호 |

## 7. Open Questions (기본값으로 자율 결정)

1. 유통기한 미입력 허용? → **허용**(`lot_service`가 `no_expiry` 지원). 소모성·무기한 자재 존재 — **확정**
2. LOT 번호 미입력 허용? → **허용**(`lot_service` lot_no nullable) — **확정**
3. 입고수량 0 항목? → **건너뜀**(LOT/재고 미반영). 분할 입고에서 일부만 도착하는 정상 케이스 — **확정**
4. 입고 후 발주 수정? → **차단**(이미 sent라 order_service가 이미 draft만 수정 허용. 입고는 별 경로) — **확정**
5. 단위 → 발주·재고·LOT 모두 `g` 고정 계승 — **확정**
6. `received` 도달 후 추가 입고? → **허용**(추가분도 재고/LOT 반영, 상태는 received 유지) — **확정**
