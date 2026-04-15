# Measurement Dashboard — Completion Report

> PDCA Cycle: Plan → Design → Do → Check → **Report**
> Feature: `measurement-dashboard`
> Completed: 2026-04-15
> Match Rate: **97%** (PASS)

## 1. Overview

| Item | Detail |
|---|---|
| Goal | 계량 실적·재료 사용량·시간당 속도·담당자 실적을 대시보드로 시각화 |
| Priority | Medium |
| Level | Dynamic |
| DB 변경 | 없음 |
| API 신규 | 6개 (manager 전용) |
| UI 신규 | `/dashboard` 페이지 1개 + 드릴다운 모달 |

## 2. Problem → Solution

**문제**: IRMS는 계량 데이터를 꾸준히 수집하지만 분석 화면이 없어 책임자가 "이번 주 완료 몇 건?", "PL-835-1 한 달 사용량?" 같은 질문에 SQL 직접 조회에 의존. 월간 리포트는 수기 집계.

**해결**: manager 전용 `/dashboard` 페이지에 기간 필터 + 4개 요약 카드 + 4개 패널(추이 라인차트, 재료 TOP 10 바차트, 시간당 속도 차트, 담당자 실적 표) + 재료 클릭 드릴다운 구성.

## 3. PDCA Phase Summary

### Plan
- 5개 기능 항목, 5개 Open Questions (차트 라이브러리, NULL 처리, 담당자 섹션, 기본 기간, 기간 상한)

### Design — Q&A 결과
- **Q1**: Chart.js 로컬 번들 (오프라인 현장)
- **Q2**: 편차 분석 → **시간당 계량 속도**로 대체 (저울 실측 미연동 발견 후 설계 수정)
- **Q3**: 담당자별 실적 포함 (manager 전용 접근이므로 OK)
- **Q4**: 기본 표시 기간 7일
- **Q5**: 기간 필터 제한 없음

### Do
- **Backend** (`src/routers/dashboard_routes.py` 신규)
  - `_parse_range` 헬퍼 (기본 7일, `INVALID_DATE`/`INVALID_RANGE` 400)
  - `_compute_active_hours` (`cnt/60` 하한으로 0시간 근사 오차 방지)
  - 6개 엔드포인트: summary / materials / materials/{id}/recipes / throughput / trend / operators
  - router-level manager 권한
- **Routing**
  - `api.py`에 dashboard_router include
  - `pages.py`에 `/dashboard` 페이지 라우트 (manager 전용)
  - `_base_app.html`에 "Dashboard" 네비 링크 (manager 가시)
- **Vendor**: `static/vendor/chartjs/chart.umd.min.js` (200KB UMD minified)
- **Template**: `templates/dashboard.html` — 필터, 4카드, 2×2 차트 그리드, 드릴다운 모달
- **JS**: `static/js/dashboard.js` — 프리셋/커스텀 기간, localStorage, 5개 병렬 fetch, Chart 인스턴스 3개, 드릴다운
- **CSS**: `static/css/dashboard.css` — 카드 그리드, 차트 wrap

### Check
- Match Rate: **97%**
- 누락 없음
- 추가(non-breaking): refresh 버튼, empty state, `INVALID_DATE` 에러
- Plan 6개 Success Criteria 중 5개 PASS (편차 분석은 Q2 결정에 따라 시간당 속도로 대체)

### Act
- Iterate 불필요

## 4. Files Changed

**신규**
- `src/routers/dashboard_routes.py` — 6개 엔드포인트 + 헬퍼
- `templates/dashboard.html` — 대시보드 레이아웃
- `static/js/dashboard.js` — 필터/fetch/Chart 렌더
- `static/css/dashboard.css` — 카드/차트 그리드
- `static/vendor/chartjs/chart.umd.min.js` — Chart.js 4.4.7 UMD 번들

**수정**
- `src/routers/api.py` — dashboard_router include
- `src/routers/pages.py` — `/dashboard` 페이지 라우트
- `templates/_base_app.html` — manager 네비 "Dashboard" 링크

## 5. Success Criteria Verification

| # | Criterion | Result |
|---|---|---|
| 1 | 기본 7일 기준 요약 카드 4개 즉시 표시 | PASS |
| 2 | 기간 필터 변경 시 모든 섹션 갱신 | PASS |
| 3 | 재료 TOP 10 바 클릭 드릴다운 | PASS |
| 4 | (원) 편차 분석 → 시간당 계량 속도 대체 | REPLACED (Q2) |
| 5 | 일자별 추이 라인 차트 | PASS |
| 6 | operator 차단 / manager 허용 | PASS |

## 6. Key Learnings

1. **설계-현실 불일치 조기 발견**: Do 단계 진입 직후 `actual_weight` 컬럼이 존재하지 않는다는 사실을 확인. 구현 진행 전에 사용자 확인 후 "시간당 계량 속도"로 대체. 구현 중 rework를 피한 정확한 판단.
2. **저울 미연동 제약의 명시화**: 리스크 섹션에 "차후 `actual_weight` 컬럼 추가 시 편차 분석 feature를 별도 사이클로" 기록 → 다음 사이클 후보로 트래킹 가능.
3. **`active_hours` 근사**: SQLite에 세션 경계가 없으므로 `min/max(measured_at)`로 추정. 1건만 있거나 동시 계량으로 0이 되는 케이스를 `cnt/60` 하한으로 보정. 완벽하진 않지만 현장 규모에선 충분.
4. **Chart.js vendor 선택**: CDN 의존 대신 200KB 로컬 번들. 공장 네트워크 단절에도 동작.
5. **기존 `insight` 페이지 공존**: 이미 있는 `/insight`와 별개로 `/dashboard`를 추가. 향후 두 페이지 병합 검토 필요.

## 7. Non-breaking Extensions

- **Refresh 버튼** — 기간 변경 없이 수동 갱신
- **`INVALID_DATE` 에러 코드** — `INVALID_RANGE`와 구분하여 UX 개선 여지
- **Drill-down empty state** — 재료 상세에 데이터 없을 때 명시적 표시

## 8. Known Follow-ups / Next Features

- **편차 분석 (deviation)**: 저울 실측 연동(`actual_weight` 컬럼) 추가 후 별도 사이클로 진행
- **`/insight`와 `/dashboard` 병합**: 중복 기능 정리
- **Feature #4**: Cloudflare Tunnel 외부 접속 (원 계획의 마지막 feature)

권장: 커밋/푸시 → `/pdca archive measurement-dashboard` → `/pdca plan cloudflare-tunnel-access`.
