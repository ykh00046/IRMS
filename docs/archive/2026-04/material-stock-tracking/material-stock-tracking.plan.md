# Material Stock Tracking Plan

> 원재료 재고 추적 + 계량 시 자동 차감 + 재고 부족 경고

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | material-stock-tracking |
| Priority | High |
| Base | materials 테이블, recipe_items 계량 기록 |
| Goal | 원재료별 현재 재고 수량을 추적하고, 계량 시 자동 차감, 임계치 이하일 때 책임자에게 경고 |

## 2. Problem Statement

현재 IRMS는 레시피 계량 기록은 남기지만 **원재료 재고**는 추적하지 않는다.
현장에서는 "무엇이 얼마나 남았는지" 파악하려면 창고를 직접 확인해야 하고,
갑자기 재고가 부족한 상태로 작업을 시작하면 중단 사고가 발생한다.

### Pain Points

1. **재고 불가시성** — 시스템 내 원재료가 몇 kg/L 남았는지 알 수 없음
2. **발주 타이밍 누락** — 책임자가 수동 점검하지 않으면 재고 소진 후에야 인지
3. **작업 중단 위험** — 계량 중 재고 부족 발견 시 레시피 중단, 재작업 유발
4. **사용량 통계 부재** — 월별 소비량 분석 불가 → 발주량 산정이 감(感)에 의존

## 3. Feature Items

### 3.1 재고 스키마 추가

| Item | Detail |
|------|--------|
| 신규 컬럼 | `materials.stock_quantity REAL DEFAULT 0`, `materials.stock_threshold REAL DEFAULT 0` |
| 이력 테이블 | `material_stock_logs(id, material_id, delta, reason, actor_id, recipe_id, created_at)` |
| 마이그레이션 | `ensure_column` + `CREATE TABLE IF NOT EXISTS` |

### 3.2 자동 차감 로직

| Item | Detail |
|------|--------|
| 트리거 | 계량 확정(`measured_at` 기록) 시 해당 원재료 재고에서 `value_weight`만큼 차감 |
| 위치 | `src/routers/recipe_routes.py` 계량 확정 엔드포인트 |
| 이력 | 차감 시 `material_stock_logs`에 reason='measurement' 기록 |
| 경합 방지 | 트랜잭션 내 UPDATE + SELECT로 음수 재고 허용 여부 결정 (허용 + 경고 노출) |

### 3.3 재고 입고/조정 UI (책임자 전용)

| Item | Detail |
|------|--------|
| 위치 | `/management/materials` 페이지 확장 |
| 입력 | 원재료별 입고량, 조정 사유(입고/폐기/보정) |
| 기록 | `material_stock_logs` reason='restock'/'adjust'/'discard' |
| 권한 | manager 이상 |

### 3.4 재고 부족 경고

| Item | Detail |
|------|--------|
| 임계치 | 원재료별 `stock_threshold` 설정 (기본 0 = 경고 없음) |
| 위치 1 | Status/Management 대시보드 상단 알림 배너 |
| 위치 2 | 계량 작업 시작 시 재료 중 임계치 미달 있으면 작업 카드에 경고 표시 |
| 조건 | `stock_quantity <= stock_threshold` |

### 3.5 재고 현황 API + 이력 조회

| Item | Detail |
|------|--------|
| API | `GET /api/materials/stock` — 전 원재료 현재 재고 + 임계치 상태 |
| API | `GET /api/materials/{id}/stock-log` — 특정 원재료 입출고 이력 |
| UI | 원재료 상세 페이지에 이력 테이블 |

## 4. Scope

### In Scope
- 재고 컬럼/이력 테이블 마이그레이션
- 계량 확정 시 자동 차감
- 책임자용 입고/조정 UI
- 재고 부족 경고 배너 + 작업 카드 경고
- 재고 API + 이력 조회

### Out of Scope
- 다중 창고 / 로트 관리
- 발주서 자동 생성
- 외부 ERP 연동
- 재고 예측 (소비 추세 기반)

## 5. Success Criteria

1. 계량 확정 후 `materials.stock_quantity`가 정확히 차감되고 `material_stock_logs`에 기록
2. 책임자가 입고 처리 시 재고 증가 + 이력 기록
3. 임계치 미달 원재료는 대시보드에 경고 배너로 표시
4. 음수 재고가 되는 계량도 작업은 진행되지만 명확한 경고 노출

## 6. Open Questions

1. **음수 재고 허용 여부** — 현장 운영상 재고 카운트가 정확하지 않을 수 있음. 차단 vs 경고만?
2. **초기 재고 입력** — 기존 원재료의 초기 수량을 일괄 입력하는 UI가 필요한가, CSV 일괄 임포트?
3. **단위 혼재** — `materials.unit` (g/kg/L 등) 기준으로 일관되게 저장. 계량값도 같은 단위?
