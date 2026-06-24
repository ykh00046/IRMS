# Plan — weighing-variance-analysis (계량 편차 분석 대시보드)

> R3 추가기능. measurement-dashboard / forecast-dashboard-alert 후속. 작성일 2026-06-18.

## 1. 배경 / 문제

계량 모드는 **수동 진행**(저울 연계 없음)이라, 운영자가 목표값(`recipe_items.value_weight`)을
보고 직접 칭량한다. 따라서 *목표 대비 실제로 얼마나 더/덜 담았는가*(편차)는 품질·원가의
핵심 지표지만, 지금까지는 어디에도 집계되지 않았다.

선행 작업으로 계량 단계에서 **실측값 캡처**가 이미 도입됐다:
- `recipe_items.actual_weight`(REAL) 컬럼 + 부분 인덱스 (schema.py / migrations.py)
- `WeighingStepRequest.actual_weight` 입력 + 계량 완료 시 저장 + 재고차감에 실측 우선 반영
  (models.py / weighing_routes.py / work.js)
- 집계 서비스 `services/variance_service.py` 와 대시보드 API 3종
  (`/dashboard/variance/summary`, `/variance/materials`, `/variance/materials/{id}/recipes`)

**문제**: 백엔드는 갖춰졌으나 ① **UI가 없어** 관리자가 편차를 볼 수 없고, ② **단위 테스트가
없어** 회귀에 취약하며, ③ **PDCA 문서가 없어** 결정 근거가 남지 않는다.

## 2. 목표 (What)

- **G1** 관리자가 `/dashboard`에서 선택 기간의 **계량 편차 요약**(실측 커버리지·총 편차·총 |편차|)을 본다.
- **G2** **계량 편차 상위 자재**(|편차| 기준 TOP N) 표를 보고, 행을 클릭하면 해당 자재의
  레시피별 목표/실측/편차 **드릴다운**을 본다.
- **G3** 편차 표는 기존 대시보드 **기간 필터(프리셋/사용자 지정)** 를 그대로 따른다.
- **G4** 실측 데이터가 없는 기간이면 패널은 **빈 상태 안내**를 보여준다(오류 아님 — 노이즈 제로).
- **G5** `variance_service` 집계 + 라우트 권한에 대한 **단위 테스트**를 추가해 회귀를 막는다.

## 3. 비목표 (Out of Scope)

- 계량 화면(work.js)의 실측 입력 UX 변경 (선행 작업에서 이미 처리)
- 편차 허용 한계(tolerance) 설정·초과 경보 (차기 후보)
- 원가 환산(단가 × 편차) (별도 PDCA — 자재 단가 필드 선행 필요)
- operator 화면 노출 (편차 분석은 manager 책무 — 기존 dashboard 권한 정책 유지)
- 트레이/외부 알림

## 4. 사용자 가치

| 이해관계자 | 가치 |
|-----------|------|
| 관리자(manager) | 어느 자재·레시피에서 칭량 오차가 큰지 한눈에 → 품질·낭비 관리 |
| 현장 운영 | 영향 없음(권한 분리 유지) |
| 시스템 | actual_weight 부분 인덱스로 편차 집계 확장성 확보(선행) |

## 5. 성공 기준 (Acceptance)

1. `GET /api/dashboard/variance/summary` (manager) → 실측 커버리지·목표합·실측합·편차합·|편차|합 반환.
2. `GET /api/dashboard/variance/materials?limit=N` → |편차| 내림차순 상위 N건, 실측 0건 자재는 제외.
3. `GET /api/dashboard/variance/materials/{id}/recipes` → 레시피별 목표/실측/편차/편차율, 없는 자재는 404.
4. operator/비인증 접근 차단(401/403).
5. 대시보드에 편차 요약 + 상위 자재 표가 기간 필터에 연동되어 렌더, 행 클릭 시 드릴다운 모달 표시.
6. 실측 데이터 0건 기간 → 표/모달이 빈 상태 안내(오류 토스트 없음).
7. `variance_service` 3함수 + 라우트 권한 단위 테스트 통과, 기존 전체 pytest 회귀 0.

## 6. 제약 / 규약

- manager 전용(`require_access_level("manager")`) — 기존 dashboard_routes 정책 답습.
- 모든 값 단위 `g` 고정. 편차율은 목표=0이면 `null`.
- 신규 표/모달은 공통 CSS(`.panel`/`.table-wrap`/`.metric-card`/`.stock-status`/`.empty-state`) 재사용,
  자체 class 최소화 (메모리 `feedback_common_form_css`).
- 모달 숨김은 `hidden` 속성 (메모리 `feedback_css_hidden`).
- UI 문구 전부 한국어 (메모리 `feedback_korean_ui`). 담당자 미기록은 `(미기록)`.
- 편차 표/요약은 **GET 읽기 전용** → CSRF 불필요.
- 집계 로직은 `services/variance_service.py`에 격리(라우터는 얇게) — 선행 구조 유지.
- 출력은 `IRMS.escapeHtml`로 XSS 방지(dashboard.js 기존 패턴).

## 7. 리스크

| 리스크 | 대응 |
|--------|------|
| 실측 미입력 기간이 많아 편차가 비어 보임 | 커버리지(%) 노출 + 빈 상태 안내로 의미 명확화 |
| 음수 편차(덜 담음) 표기 혼동 | 편차/편차율에 부호 + 색상(초과=빨강/부족=파랑) 구분 |
| 대시보드 레이아웃 과밀 | 기존 `.dashboard-grid` 패턴 재사용, 단일 패널 + 드릴다운 모달 |
| 권한 누락 노출 | 라우터 dependency로 manager 강제 + 테스트로 검증 |

## 8. 산출물

- `docs/02-design/features/weighing-variance-analysis.design.md`
- `templates/dashboard.html` (편차 요약 + 상위 자재 패널 + 드릴다운 모달)
- `static/js/dashboard.js` (편차 로더·렌더, 기간 필터 연동)
- `static/css/dashboard.css` (편차 부호 색상 — 필요 시)
- `tests/test_weighing_variance.py` (variance_service + 라우트 권한)
- 이미 존재(선행, 회귀 검증 대상): `src/services/variance_service.py`,
  `src/routers/dashboard_routes.py`(variance 3종), `src/db/schema.py`, `src/db/migrations.py`,
  `src/routers/models.py`, `src/routers/weighing_routes.py`
