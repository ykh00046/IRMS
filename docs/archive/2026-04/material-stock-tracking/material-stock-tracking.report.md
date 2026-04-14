# PDCA Completion Report — material-stock-tracking

| 항목 | 내용 |
|---|---|
| Feature | material-stock-tracking |
| Completed | 2026-04-14 |
| Match Rate | **98%** |
| Phase | Completed → Ready to Archive |

## 1. 개요

원재료 재고 추적 기능. 계량 확정 시 자동 차감, 책임자용 입고/조정/폐기 처리, 임계치 기반 재고 부족 경고를 제공한다. 기존 IRMS는 계량 기록만 있고 재고는 추적하지 않았기에 "얼마 남았나"를 파악하려면 창고를 직접 확인해야 했던 현장 문제를 해결.

## 2. 주요 결정 (Plan → Design)

| # | 질문 | 결정 |
|---|---|---|
| Q1 | 음수 재고 처리 | 허용 + 빨간 경고 (현장 카운트 오차 감안) |
| Q2 | 초기값 | 0으로 시작, 책임자 입고로 채움 |
| Q3 | 단위 | 전부 g 고정 |

## 3. 구현 요약

### 신규 파일
- `src/services/stock_service.py` — 차감/입고/조정/폐기/복구/임계치/상태 공통 로직 (215 LoC)
- `static/js/stock.js` — Management 재고 탭 UI 로직 (235 LoC)
- `docs/01-plan/features/material-stock-tracking.plan.md`
- `docs/02-design/features/material-stock-tracking.design.md`
- `docs/03-analysis/material-stock-tracking.analysis.md`

### 수정 파일
- `src/database.py` — 컬럼 2개(`stock_quantity`, `stock_threshold`) + `material_stock_logs` 테이블 + 인덱스 (일반/partial UNIQUE)
- `src/routers/weighing_routes.py` — 계량 확정/취소 경로에 `deduct_for_measurement` / `reverse_measurement` 훅
- `src/routers/recipe_routes.py` — `list_materials`에 stock 필드 추가, 6개 신규 API (`GET /materials/stock`, `GET /materials/{id}/stock-log`, `POST .../stock/restock|adjust|discard`, `PATCH .../stock-threshold`)
- `templates/management.html` — "원재료 재고" 탭 + 액션 모달 + 이력 모달
- `templates/status.html`, `templates/work.html` — 상단 경고 배너
- `static/js/work.js` — LOW 재고 폴링 + 계량 카드 `stock-warning-stripe` 토글
- `static/css/common.css`, `static/css/management.css` — `.stock-banner`, `.stock-status`, `.stock-warning-stripe` 등

## 4. 핵심 기술 포인트

1. **멱등 차감** — 같은 `recipe_item_id`에 대해 `reason='measurement'`의 UNIQUE partial 인덱스로 DB 레벨에서 중복 차감 방지. 서비스 로직 + 스키마 2중 보호.
2. **Undo 복구** — 계량 취소 시 `material_stock_logs`에서 해당 로그를 삭제하고 재고를 되돌림. 설계 초과 구현.
3. **음수 재고 수용** — Q1 결정에 따라 차감 후 잔량이 음수여도 작업 진행, `note='음수 재고 발생'` + UI 빨간 배지.
4. **권한 분리** — 조회(`GET`)는 operator 레벨, 재고 변경(`POST`/`PATCH`)은 manager 레벨. 모든 변경은 `write_audit_log`.
5. **경고 확산 3지점** — Management (실시간 배너 + 행 배경), Status (배너), Work (배너 + 현재 계량 카드 LOW 띠).

## 5. Gap 분석 요약 (`docs/03-analysis/material-stock-tracking.analysis.md`)

- **Match Rate 98%**, Critical Gap 없음.
- **설계 초과 3건**: (1) DB partial UNIQUE 인덱스, (2) Undo 재고 복구, (3) 전체 변경 감사 로그.
- **Low Gap 1건**: Status 페이지 재고 배너가 기존 10초 갱신과 별개로 30초 독립 폴링 — 선택 정리 대상.

## 6. 배포 전 수동 검증 체크리스트 (서버 실행 후)

1. 책임자 로그인 → Management → "원재료 재고" 탭 접근
2. 아무 원재료 100g 입고 → 재고 100, 이력 1건
3. 해당 원재료 포함 레시피 계량 30g 확정 → 재고 70, 로그 2건
4. 계량 취소 → 재고 100 복구, 로그 1건으로 감소
5. 임계치 50g 설정 후 30g 차감 → LOW 배너 노출, 행 노란색
6. 음수 재고 유도 → NEGATIVE 빨간 배지 + note 기록
7. Operator 계정으로 `POST /stock/restock` 시도 → 403 확인

## 7. 향후 개선 (Out of Scope였던 항목)

- 다중 창고 / 로트 관리
- 소비 추세 기반 발주 예측
- 외부 ERP 연동
- 재고 CSV 일괄 임포트 UI (현재는 책임자 입고 루틴으로 대체)

## 8. PDCA 사이클

```
[Plan] ✅ → [Design] ✅ → [Do] ✅ → [Check] ✅ 98% → [Report] ✅
```

**Next**: `/pdca archive material-stock-tracking`
