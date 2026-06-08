# 발주 입고·검수 — Gap 분석 (Check)

| 항목 | 값 |
|------|------|
| Feature | `purchase-order-receiving` |
| Phase | Check (Gap Analysis) |
| 분석일 | 2026-06-06 |
| Agent | bkit:gap-detector |
| **Match Rate** | **99%** |
| Critical / Major / Minor | 0 / 0 / 1 |

## 1. 종합

| 카테고리 | 점수 |
|----------|:----:|
| Design Match (§2~§5) | 99% |
| Plan Success Criteria (7) | 100% |
| §9 검증반영(M-1/M-2/m-3/m-4) | 100% |
| 컨벤션 준수 | 100% |
| **종합** | **~99%** |

설계와 구현이 매우 잘 일치한다. Critical/Major 갭 없음. 90% 초과로 iterate 불필요 → report 단계 진행.

## 2. 요소 매핑 (설계 → 구현)

- **§2 데이터모델**: `_ALLOWED_TABLES`(+2), `ensure_column`(receipt_status/received_qty), `po_receipts`(8컬럼)/`po_receipt_items`(11컬럼) + 인덱스 2 — schema.py·migrations.py 양쪽 일치. ✅
- **§3 서비스**: `ReceivingStateError`, `generate_receipt_no`, `receive_order`(9단계 전부), `_recompute_receipt_status`(3분기), `list_receipts`. 호출계약(`register_lot(received_at=None)`, `restock(note="발주 입고: …")`) 일치. ✅
- **§4 라우터/모델**: POST/GET `/orders/{id}/receipts`, 오류매핑(404/400), 감사로그 `order_receive`, `ReceiptLineBody`/`ReceiptCreateBody`, api.py 인접 등록, order_service receipt_status 노출. ✅
- **§5 UI**: 목록 입고 배지, sent 전용 입고 버튼, 입고 모달(발주량/기입고/잔여/입고수량/LOT/유통기한), CSRF 헤더, 입고 이력 표, 공통 CSS 재사용·hidden 토글. ✅

## 3. Plan Success Criteria (7/7 충족)

| # | 기준 | 테스트 |
|---|------|--------|
| 1 | sent 폼 자동 채움(발주량/기입고/잔여) | UI |
| 2 | LOT+재고 한 트랜잭션 | R2,R4 |
| 3 | partial→received 전이 | R3,R4 |
| 4 | RC- 이력 ↔ lot_id/stock_log_id 연결 | R11 |
| 5 | sent 전용 + manager 전용 | R6,R13 |
| 6 | 전 입고 감사 로그 | 라우트+라이브 스모크 |
| 7 | 회귀 없음(전체 pytest) | 180 passed |

## 4. §9 검증반영 확인 (전부 반영)

- **M-1** 신규 2컬럼은 `ensure_column`에만, 2테이블은 schema.py+migrations 양쪽 — 정확히 반영. ✅
- **M-2** `_recompute_receipt_status`의 `orderable and all(...)` 가드로 빈시퀀스 vacuous-True 방지 + float epsilon 1e-9 하드닝. ✅
- **m-3** `register_lot(received_at=None)`로 datetime→date 변환 회피, 헤더만 `utc_now_text()`. ✅
- **m-4** `material_name`을 `purchase_order_items`에서 스냅샷 복사. ✅

## 5. 추가/변경 (비파괴)

- (추가) `RECEIPT_STATUS_LABEL` + `receipt_status_label` 반환 — 편의 라벨, 무해.
- (추가) float epsilon 1e-9 — 부동소수 드리프트 방어.
- (Minor) 설계 §7 테스트 표가 초기엔 R10(없는 발주)/failed 분기를 누락 → **설계 문서 보정 완료**(코드가 진실 원칙). pytest 16개 수집 일치.

## 6. 결론

Match Rate **99% (≥90%)**. 코드 수정 불필요. QA(전체 pytest 180 통과 + 라이브 HTTP 스모크) 완료. → `report` 단계 진행.
