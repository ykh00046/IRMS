# Plan — forecast-dashboard-alert (운영 대시 발주 임박 알림)

> R2 추가기능. material-forecast(0499ece) 후속. 작성일 2026-06-01.

## 1. 배경 / 문제

`material-forecast`로 발주 임박(긴급/임박) 산출은 가능하지만, **확인 동선이 수동**이다.
관리자가 `/management` → "소모예측·발주" 탭으로 직접 들어가야만 위험을 인지한다.
현장 운영자(비개발자) 요구의 본질은 *"발주 놓치지 않기"* 이며, report §7 차기 후보 ①
(트레이 발주 임박 푸시)도 같은 가치를 노린다.

트레이 클라이언트는 메모리(`feedback_tts_pyinstaller_failure`) 기준 PyInstaller/TTS 회귀
이력으로 취약하고 세션 내 QA가 어렵다. 따라서 **동일한 가치(능동적 발주 임박 인지)를
안전한 웹 스택으로 실현**한다 — 관리자가 항상 처음 보는 **운영 대시(`/dashboard`) 상단에
발주 임박 알림 카드**를 노출한다.

## 2. 목표 (What)

- **G1** 관리자가 `/dashboard` 진입 시, 발주 권장(긴급+임박) 건수와 상위 항목을 **즉시** 본다.
- **G2** 알림 카드에서 한 번의 클릭으로 상세(소모예측·발주 탭)로 이동한다.
- **G3** 발주 권장이 0건이면 카드는 **노출되지 않는다**(노이즈 제로 — 메모리 `feedback_alert_semantics` 정합).
- **G4** (스케일 하드닝, report §7 후보 ④) forecast 소비 집계 쿼리를 위한
  `material_stock_logs(reason, created_at)` 인덱스를 추가해 로그 누적 시 성능 저하를 예방한다.

## 3. 비목표 (Out of Scope)

- 트레이/OS 토스트 푸시 (별도 PDCA, report §7 ①)
- 이메일/메신저 외부 알림
- 발주서 생성·ERP 연동 (report §7 ②)
- 예측 모델 고도화 (계절성 등, report §7 ③)
- operator 화면 노출 (발주는 manager 책무 — 기존 forecast 권한 정책 유지)

## 4. 사용자 가치

| 이해관계자 | 가치 |
|-----------|------|
| 관리자(manager) | 로그인 직후 발주 임박을 한눈에 → 발주 누락 방지 |
| 현장 운영 | 영향 없음(권한 분리 유지) |
| 시스템 | 인덱스로 forecast 쿼리 확장성 확보 |

## 5. 성공 기준 (Acceptance)

1. `GET /api/dashboard/forecast-alert` (manager) → `{reorder_recommended, urgent, soon, items[]}` 반환, items는 긴급→임박 순 + 상위 N건(기본 5).
2. operator/비인증 접근 차단(401/403).
3. 대시보드 진입 시 발주 권장>0이면 카드 노출(건수 배지 + 상위 항목 + 상세 링크), 0이면 미노출.
4. 발주 권장 0건 응답에서 카드 DOM이 hidden.
5. `material_stock_logs(reason, created_at)` 인덱스가 마이그레이션으로 생성(IF NOT EXISTS, 재실행 안전).
6. 기존 기능 회귀 0 — 전체 pytest 통과.

## 6. 제약 / 규약

- manager 전용(`require_access_level("manager")`) — 기존 dashboard_routes 정책 답습.
- 신규 폼/표는 공통 CSS(`.panel`, `.metric-card`, `.stock-status`) 재사용, 자체 class 최소화 (메모리 `feedback_common_form_css`).
- 모달/숨김은 `hidden` 속성 사용 (메모리 `feedback_css_hidden`).
- UI 문구 전부 한국어 (메모리 `feedback_korean_ui`).
- 알림 카드는 **GET 읽기 전용** → CSRF 헤더 불필요(쓰기 경로 없음).
- 서비스 로직은 `services/forecast_service.py`에 격리(라우터는 얇게).

## 7. 리스크

| 리스크 | 대응 |
|--------|------|
| compute_forecast 전체 재계산 비용 | items 상위 N 슬라이스만 직렬화, 쿼리는 인덱스로 보강 |
| 대시보드 레이아웃 깨짐 | 기존 `.panel` 구조 재사용, 카드 단독 섹션 |
| 권한 누락 노출 | 라우터 dependency로 manager 강제 + 테스트로 검증 |

## 8. 산출물

- `docs/02-design/features/forecast-dashboard-alert.design.md`
- `src/services/forecast_service.py` (forecast_alert 헬퍼)
- `src/routers/dashboard_routes.py` (엔드포인트)
- `src/db/migrations.py` (인덱스)
- `templates/dashboard.html`, `static/js/dashboard.js`
- `tests/test_forecast_dashboard_alert.py`
