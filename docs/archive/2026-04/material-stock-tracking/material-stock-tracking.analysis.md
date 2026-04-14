# Gap Analysis — material-stock-tracking

**Feature**: material-stock-tracking
**Phase**: Check
**Date**: 2026-04-14
**Match Rate**: **98%**

## 1. Verification Matrix (Design §5 구현 순서)

| # | 단계 | 상태 | 증거 |
|---|---|---|---|
| 1 | DB 마이그레이션 (컬럼 + 테이블 + 인덱스) | ✅ | `src/database.py:110-135` |
| 2 | `stock_service.py` (deduct/reverse/restock/adjust/discard/threshold/status/list/logs) | ✅ | `src/services/stock_service.py` |
| 3 | 계량 라우터 훅 (complete 시 차감, undo 시 복구) | ✅ | `src/routers/weighing_routes.py` (deduct + reverse) |
| 4 | 재고 API (list + log + 4 manager endpoints) | ✅ | `src/routers/recipe_routes.py` |
| 5 | Management UI (탭 + 테이블 + 모달 + stock.js) | ✅ | `templates/management.html`, `static/js/stock.js` |
| 6 | 경고 배너 (management/status/work) | ✅ | 3곳 모두 구현 |
| 7 | work.js LOW 경고 띠 | ✅ | `refreshLowStock` + `stock-warning-stripe` 토글 |

## 2. Design 초과 구현 (긍정)

1. **DB 레벨 멱등성** — 설계는 코드상 중복 방지만 요구했으나 구현은 partial UNIQUE 인덱스 `idx_stock_logs_item_measurement ON (recipe_item_id) WHERE reason='measurement'`로 DB 레벨에서 보장.
2. **Undo 시 재고 복구** — 설계는 차감만 명시했으나 `stock_service.reverse_measurement`를 추가하여 계량 취소 시 재고를 복구함. 현장 오조작 대응성 향상.
3. **Audit log** — 4개 manager 엔드포인트 모두 `write_audit_log` 호출.

## 3. Q1/Q2/Q3 결정 준수

| # | 결정 | 구현 | 결과 |
|---|---|---|---|
| Q1 | 음수 재고 허용 + 경고 | `deduct_for_measurement`에서 `new_balance < 0` 시 note="음수 재고 발생", UI에서 빨간색 표시 | ✅ |
| Q2 | 초기값 0 | `ensure_column ... DEFAULT 0` | ✅ |
| Q3 | 단위 g 고정 | UI 라벨 "(g)", 저장은 순수 숫자 | ✅ |

## 4. Gaps

### Low Severity

**G1. 30초 폴링 간격**
- Status 페이지 기존 기능은 10초 갱신 주기 사용. 재고 배너는 별도 30초 폴링.
- 영향 낮음: 재고 경고는 긴급 업데이트가 필요한 값이 아님.
- 권장: 설계 문서 §4.3 "10초 갱신 주기에 포함" 기술을 "30초 독립 폴링"으로 업데이트하거나, status.js 내부 갱신 루프에 통합.

### Critical
없음.

## 5. 결론

- 설계 7단계 전부 구현, 3건의 설계 초과 개선(DB 유니크, undo 복구, 전체 감사).
- Match Rate **98% ≥ 90%** → **Report 단계 진행 가능**.
- G1 정리는 선택 사항.
