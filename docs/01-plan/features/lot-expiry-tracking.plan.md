# Plan — lot-expiry-tracking (자재 LOT·유통기한 관리)

> R4 신규기능. material-stock-tracking / material-forecast / forecast-dashboard-alert 후속. 작성일 2026-06-02.

## 1. 배경 / 문제

현재 재고는 자재별 단일 수량(`materials.stock_quantity`)만 추적한다. 그러나 잉크·경화제·
첨가제는 **유통기한(shelf life)** 이 있는 화학 자재로, 기한 경과분을 인지하지 못하면
변질 자재가 레시피에 투입되거나(품질 사고) 기한 임박분을 방치해 폐기 손실이 발생한다.

운영 담당자(비개발자)의 본질적 요구는 *"기한 지나기 전에 알기"* 이다. 입고 단위(LOT)별로
수량·입고일·유통기한을 기록하고, 만료/임박 LOT을 **운영 대시 진입 시 즉시** 인지시키면
이미 검증된 `forecast-dashboard-alert` 패턴을 그대로 재사용해 안전하게 가치를 실현할 수 있다.

## 2. 목표 (What)

- **G1** 관리자가 입고 시 LOT 단위로 (LOT번호, 수량, 입고일, 유통기한)을 등록한다.
- **G2** 자재별 LOT 목록과 각 LOT의 유통기한 상태(만료/임박/정상/무기한)를 조회한다.
- **G3** LOT 소진(사용)·폐기를 기록하여 잔여 수량을 관리한다.
- **G4** 운영 대시(`/dashboard`) 진입 시 만료·임박 LOT 건수와 상위 항목을 **즉시** 본다(0건이면 미노출).
- **G5** 발주/감사용으로 LOT 현황을 CSV로 내보낸다.

## 3. 비목표 (Out of Scope)

- **`materials.stock_quantity` 재설계 / FIFO 자동 차감** — 계량 차감 경로(`stock_service.deduct_for_measurement`)는 무변경. LOT은 **유통기한·이력 추적 전용 레이어**이며 stock_quantity의 source of truth를 침범하지 않는다(결합도 0, 리스크 최소). 추후 통합은 별도 PDCA.
- 트레이/OS 토스트·이메일 등 외부 알림 (forecast-dashboard-alert와 동일하게 웹 카드로 한정)
- 바코드/QR 스캔 입력 (별도 후보)
- 유통기한 자동 추출(레시피·엑셀 파싱)

## 4. 사용자 가치

| 이해관계자 | 가치 |
|-----------|------|
| 관리자(manager) | 만료 임박 LOT을 로그인 직후 인지 → 폐기 손실·변질 투입 방지 |
| 현장 운영(operator) | LOT 목록 조회로 어떤 LOT을 먼저 써야 할지(선입선출) 판단 |
| 시스템 | 입고 추적성(traceability) 확보, 감사 로그 연계 |

## 5. 성공 기준 (Acceptance)

1. `POST /api/materials/{id}/lots` (manager) → LOT 등록, 잔여=입고수량으로 초기화.
2. `GET /api/materials/lots`, `GET /api/materials/{id}/lots` (operator) → LOT 목록 + 유통기한 상태.
3. `POST /api/lots/{lot_id}/consume`, `POST /api/lots/{lot_id}/discard` (manager) → 잔여 차감/폐기, 잔여 0 시 자동 `depleted`.
4. `GET /api/dashboard/expiry-alert` (manager) → `{expired, expiring_soon, items[]}`. 만료→임박 순 상위 N건(기본 5). 0건이면 카드 미노출.
5. `GET /api/lots/export` (manager) → CSV(수식 인젝션 방어 포함).
6. operator/비인증 쓰기·대시 접근 차단(401/403).
7. `material_lots` 테이블 + 인덱스가 마이그레이션으로 생성(IF NOT EXISTS, 재실행 안전).
8. 기존 기능 회귀 0 — 전체 pytest 통과.

## 6. 제약 / 규약

- 권한: 조회는 operator, 쓰기·대시 알림은 manager (`require_access_level`) — stock_routes/dashboard_routes 정책 답습.
- 신규 표/폼은 공통 CSS(`.panel`, `.table-wrap`, `.input`, `.stock-status`, `.btn`) 재사용, 자체 class 최소화 (메모리 `feedback_common_form_css`).
- 모달/숨김은 `hidden` 속성 (메모리 `feedback_css_hidden`).
- UI 문구 전부 한국어 (메모리 `feedback_korean_ui`). 상태: 만료/임박/정상/무기한.
- 알림 카드·목록은 **GET 읽기 전용** → CSRF 불필요. 쓰기는 `x-csrftoken` 헤더 직접 부착(메모리 `project_management_csrf`).
- 서비스 로직은 `services/lot_service.py`에 격리(라우터는 얇게). 순수 상태 판정 함수 분리(단위 테스트 용이).
- 단위는 자재 단위(`g` 고정) 따름.

## 7. 리스크

| 리스크 | 대응 |
|--------|------|
| stock_quantity와 LOT 합계 불일치 혼동 | 비목표 명시 + UI에 "유통기한 추적용(재고와 별도)" 안내 문구 |
| 만료 판정 시간대(UTC vs 로컬) 혼선 | 유통기한은 날짜(YYYY-MM-DD)만 저장, 비교는 로컬 date 기준 통일 |
| 대시보드 카드 노이즈 | 0건 미노출(G4), 만료+임박만 노출 |
| 권한 누락 노출 | 라우터 dependency로 강제 + 테스트 검증 |

## 8. 산출물

- `docs/02-design/features/lot-expiry-tracking.design.md`
- `src/services/lot_service.py` (등록/소진/폐기/상태판정/대시요약)
- `src/routers/lot_routes.py` (operator 조회 + manager 쓰기, tuple 반환)
- `src/routers/dashboard_routes.py` (expiry-alert 엔드포인트 추가)
- `src/routers/models.py` (LOT 요청 모델)
- `src/db/migrations.py` (material_lots 테이블 + 인덱스), `src/db/migrations.py` `_ALLOWED_TABLES` 갱신
- `templates/management.html`, `static/js/management/*` (LOT 탭)
- `templates/dashboard.html`, `static/js/dashboard.js` (만료 임박 카드)
- `tests/test_lot_expiry_tracking.py`
