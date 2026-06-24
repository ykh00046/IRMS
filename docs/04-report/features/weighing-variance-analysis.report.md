# weighing-variance-analysis 완료 보고서

> **상태**: 완료 (언어 일관성 1건 사용자 결정 대기)
> **프로젝트**: IRMS · **작성일**: 2026-06-18 · **PDCA**: #1 · **Match Rate**: 99%

---

## 1. 요약

### 1.1 개요
| 항목 | 내용 |
|------|------|
| 기능 | weighing-variance-analysis (계량 편차 분석 대시보드) |
| 기간 | 2026-06-18 (단일 세션) |
| 선행 | actual_weight 캡처·variance_service·variance API (기구현, 본 PDCA에서 UI/테스트/문서로 완결) |

### 1.2 결과
| 지표 | 결과 |
|------|------|
| Match Rate | 99% |
| Python 테스트 | 206 passed, 10 subtests, 1 warning |
| JavaScript 테스트 | 5 passed |
| 신규 단위 테스트 | 9 (V1–V9) |
| 회귀 | 0 |
| Critical 이슈 | 0 |

### 1.3 가치
| 관점 | 내용 |
|------|------|
| 문제 | 수동 계량의 목표 대비 실측 편차가 어디에도 집계되지 않아 품질·낭비 관리 불가 |
| 해결 | 운영 대시에 편차 요약 카드 + TOP10 차트 + 요약표 + 레시피 드릴다운 추가 |
| 효과 | 관리자가 어느 자재·레시피에서 칭량 오차가 큰지 즉시 인지 |
| 핵심 | 실측 커버리지·총 편차·|편차|로 품질 지표 가시화 |

---

## 2. 성공 기준 최종 상태

| # | 기준 | 상태 | 근거 |
|---|------|:----:|------|
| 1 | variance/summary 집계 반환 | 충족 | dashboard_routes + V1–V3 |
| 2 | variance/materials \|편차\|DESC·실측0 제외 | 충족 | V4·V5 |
| 3 | variance/materials/{id}/recipes + 404 | 충족 | 라우터 + V9(서비스)·QA |
| 4 | operator/비인증 차단 | 충족 | V8 (401/403) |
| 5 | 대시 편차 요약+표 기간연동·드릴다운 | 충족 | 카드/차트/요약표/모달, loadAll 연동 |
| 6 | 실측0 기간 빈 상태(오류無) | 충족 | empty-state + 카드 `-` |
| 7 | 단위 테스트 + 회귀 0 | 충족 | 206 py + 5 js passed |

**성공률**: 7/7 (100%)

---

## 3. 산출물

| 산출물 | 위치 | 상태 |
|--------|------|------|
| Plan | docs/01-plan/features/weighing-variance-analysis.plan.md | 완료 |
| Design | docs/02-design/features/weighing-variance-analysis.design.md | 완료 |
| Analysis | docs/03-analysis/features/weighing-variance-analysis.analysis.md | 완료 |
| QA | docs/05-qa/weighing-variance-analysis.qa-report.md | PASS(조건부) |
| 단위 테스트 | tests/test_weighing_variance.py | 신규 (9) |
| 대시보드 UI | templates/dashboard.html · static/js/dashboard.js | 구현 |
| 서비스/API (선행) | src/services/variance_service.py · src/routers/dashboard_routes.py | 검증 |
| 스키마/캡처 (선행) | src/db/schema.py · migrations.py · models.py · weighing_routes.py | 검증 |

---

## 4. 미완 / 후속

| 항목 | 우선순위 | 비고 |
|------|----------|------|
| **대시보드 언어 일관성** | 높음 | 외부 프로세스가 dashboard.html을 2회 영어로 강제 복원 → 현재 HTML 영어/JS 한국어 혼재. `feedback_korean_ui` 규약 충돌. 외부 주체 식별·중지 후 한국어 재정렬 또는 영어 정식 채택+메모리 갱신 필요. **사용자 결정 대기** (기능 무관, 라벨 텍스트만) |
| 라우터 404 자동 테스트 | 낮음 | manager 세션 테스트 인프라 부재 → 서비스 V9 + QA로 대체. 차기 공통 인증 픽스처 도입 시 자동화 |
| 편차 허용 한계(tolerance) 경보 | 중 | 차기 PDCA 후보 |
| 원가 환산(단가×편차) | 중 | 자재 단가 필드 선행 필요 |

---

## 5. 회고

### 잘된 점
- 선행 백엔드(서비스·API·캡처)를 재사용해 UI/테스트/문서만으로 기능 완결.
- 9개 단위 테스트로 폴백·정렬·0분모·권한 경계를 고정, 전체 회귀 0.
- 라우트 등록·HTML/JS ID 정합을 자동 스크립트로 교차 검증.

### 개선점 / 교훈
- **외부 프로세스가 동일 파일(dashboard.html)을 작업 중 반복 재작성**하며 한국어 현지화를
  되돌림 → 동일 파일을 두고 외부 자동화와 경합 시 thrash 발생. 향후 UI 파일 편집 전
  외부 포맷터/병행 에이전트 활성 여부 확인 필요.
- manager 인증 공통 테스트 픽스처가 없어 라우트 양성 경로(200/404) 자동화가 제한됨.

---

## 6. 변경 이력
### v1.0.0 (2026-06-18)
- 추가: 계량 편차 분석 대시보드 UI(요약 카드 2종·편차 TOP10 차트·요약표·드릴다운 모달).
- 추가: tests/test_weighing_variance.py (9).
- 문서: Plan/Design/Analysis/QA/Report.
- 비고: dashboard.html 언어는 외부 프로세스 영향으로 영어 상태(사용자 결정 대기).
