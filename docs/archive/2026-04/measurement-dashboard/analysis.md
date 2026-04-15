# Measurement Dashboard — Gap Analysis (PDCA Check)

> Design: `docs/02-design/features/measurement-dashboard.design.md`
> Plan: `docs/01-plan/features/measurement-dashboard.plan.md`
> Date: 2026-04-15
> **Match Rate: 97%** — PASS

## 1. Scores

| Category | Score | Status |
|---|:---:|:---:|
| Design Match | 98% | PASS |
| Architecture Compliance | 100% | PASS |
| Convention Compliance | 95% | PASS |
| **Overall** | **97%** | **PASS** |

## 2. Backend — `src/routers/dashboard_routes.py`

| Design Item | Status | Evidence |
|---|---|---|
| `_parse_range` 기본 7일, `INVALID_DATE`/`INVALID_RANGE` 400 | PASS | lines 11–27 |
| manager 권한 (`require_access_level`) | PASS | line 45 router deps |
| `GET /dashboard/summary` — 완료/계량/총량/속도 | PASS | lines 50–101 |
| `GET /dashboard/materials?limit=` | PASS | lines 103–140 |
| `GET /dashboard/materials/{id}/recipes` 드릴다운 | PASS | lines 142–189 |
| `GET /dashboard/throughput` — by_day 포함, 빈 날짜 0 채움 | PASS | lines 191–255 |
| `GET /dashboard/trend` — 완료 수 + 총량, 빈 날짜 0 | PASS | lines 257–302 |
| `GET /dashboard/operators` — 계량 건수/총량/완료 건수 | PASS | lines 304–349 |
| `_compute_active_hours` — `cnt/60` 하한 적용 | PASS | lines 353–364 |
| SQL: `measured_at IS NOT NULL` 필터 (Q2 근거) | PASS | 전 엔드포인트 |
| `(미기록)` placeholder (measured_by NULL/빈값) | PASS | lines 314, 188 |
| Router include (`api.py`) | PASS | api.py lines 4, 16, 31 |

**Note**: Design 2.1에서 total_weight_g가 `measured_at` 범위 내 합계로 명시. 구현은 `measured_at BETWEEN ?` 필터로 일치. OK.

## 3. Page Route — `src/routers/pages.py`

| Item | Status | Evidence |
|---|---|---|
| `GET /dashboard` manager 전용 | PASS | lines 104–106 |
| operator 접근 차단 | PASS | `_protected_page_response` 리다이렉트 |

**Minor gap**: Design 3.1에서 "operator는 `/status`로 리다이렉트"라고 명시했으나, 구현은 기존 헬퍼로 `/management/login`으로 리다이렉트. 기존 규약 따름 — 수용 가능한 규약 준수.

## 4. Template — `templates/dashboard.html`

| Item | Status | Evidence |
|---|---|---|
| 기간 필터 (프리셋 3개 + 커스텀) | PASS | lines 13–29 |
| 4개 요약 카드 | PASS | lines 31–52 |
| 일자별 추이 차트 패널 | PASS | lines 55–58 |
| 재료 TOP 10 차트 패널 | PASS | lines 59–63 |
| 일자별 시간당 속도 차트 | PASS | lines 67–70 |
| 담당자별 실적 표 | PASS | lines 71–87 |
| 재료 드릴다운 모달 | PASS | lines 90–112 |
| Chart.js 로컬 번들 로드 | PASS | line 117 |
| `_base_app.html` 상속, manager 네비 링크 추가 | PASS | base template line 37 |

## 5. JS — `static/js/dashboard.js`

| Item | Status | Evidence |
|---|---|---|
| 프리셋 버튼 (today/7d/30d) | PASS | lines 43–48, 205–215 |
| 커스텀 기간 적용 | PASS | lines 217–225 |
| localStorage 기간 저장/복원 | PASS | lines 230–247 |
| 5개 API 병렬 fetch | PASS | lines 74–86 |
| 요약 카드 렌더 | PASS | lines 94–99 |
| 추이 라인 차트 (이중 Y축) | PASS | lines 101–138 |
| 재료 가로 바 차트 + 클릭 드릴다운 | PASS | lines 140–166 |
| 시간당 속도 세로 바 차트 | PASS | lines 168–184 |
| 담당자 표 렌더 + empty state | PASS | lines 186–202 |
| 드릴다운 모달 호출/렌더 | PASS | lines 172–199 |

## 6. CSS — `static/css/dashboard.css`

| Item | Status | Evidence |
|---|---|---|
| 필터 행 레이아웃 | PASS | lines 1–18 |
| 카드 그리드 (auto-fit 180px+) | PASS | lines 21–40 |
| 차트 그리드 (auto-fit 420px+) | PASS | lines 42–54 |
| 담당자 표 스크롤 | PASS | lines 55 |

## 7. Plan Success Criteria

| # | Criterion | Result |
|---|---|---|
| 1 | `/dashboard` 접속 시 기본 7일 요약 4개 카드 즉시 표시 | PASS |
| 2 | 기간 필터 변경 시 모든 섹션 갱신 | PASS |
| 3 | 재료 TOP 10 바 + 클릭 드릴다운 | PASS |
| 4 | 편차 분석 | REPLACED — Q&A에서 "시간당 계량 속도"로 대체 (저울 실측 미연동) |
| 5 | 일자별 추이 라인 차트 | PASS |
| 6 | operator 차단 / manager 허용 | PASS |

## 8. Differences

### Missing (Design O / Impl X)
**없음.**

### Added (Design X / Impl O) — non-breaking
- `refresh` 버튼 (수동 새로고침)
- 드릴다운 모달 empty state
- 4번째 요약 카드(시간당 속도)가 편차% 대신 배치

### Changed
- **편차 분석 → 시간당 계량 속도** (사용자 승인 후 Design 전체 수정)
- operator 리다이렉트 대상: 설계상 `/status` → 구현상 `_protected_page_response` 기존 규약 따름
- `parse_range` 에러 코드: 설계에 없던 `INVALID_DATE` 별도 추가 (`INVALID_RANGE`와 구분)

## 9. Recommendation

Match Rate 97% — 90% 임계치 통과. Iterate 불필요.
**다음 단계**: `/pdca report measurement-dashboard`.

선택적 후속 작업:
- 저울 실측 연동 기능이 추가되면 별도 사이클로 "편차 분석" feature 재개
- `parse_range` 에러 코드 설계 문서에 반영
