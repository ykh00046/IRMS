# Measurement Dashboard Plan

> 계량 결과를 시각화하여 생산 품질과 재료 사용 효율을 한눈에 파악

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | measurement-dashboard |
| Priority | Medium |
| Base | `recipes` + `recipe_items` (value_weight, actual_weight, completed_at), `material_stock_logs` |
| Goal | 기간별 계량 실적, 재료별 누적 사용량, 목표 대비 편차, 오차 분포를 차트·테이블로 제공 |

## 2. Problem Statement

현재 IRMS는 계량 데이터를 수집·저장하고 있으나 분석 화면이 없다:

1. 책임자가 "이번 주 몇 건의 레시피를 완료했나?" 확인하려면 Management 이력 탭에서 수동 카운트
2. "PL-835-1은 한 달에 얼마나 쓰나?" — DB 직접 조회 필요
3. "계량 편차가 큰 재료/담당자를 어떻게 파악하나?" — 지원 기능 없음
4. 월간 리포트 작성 시 Excel 수기 집계

## 3. Feature Items

### 3.1 요약 카드 (Summary Cards)

| Item | Detail |
|------|--------|
| 위치 | 신규 페이지 `/dashboard` (책임자만) |
| 카드 | 기간 내 완료 레시피 수, 총 계량 횟수, 총 재료 사용량(g), 평균 편차(%) |
| 기간 필터 | 오늘 / 7일 / 30일 / 사용자 지정 |

### 3.2 재료별 사용량 랭킹

| Item | Detail |
|------|--------|
| 표시 | 상위 N개 재료, 사용량(g) 내림차순 바 차트 + 표 |
| 드릴다운 | 재료 클릭 시 해당 재료가 쓰인 레시피 목록 |

### 3.3 편차 분석

| Item | Detail |
|------|--------|
| 계산 | 편차% = (actual_weight - value_weight) / value_weight × 100 |
| 표시 | 재료별 평균/최대 편차, 편차 상위 레시피 목록 |
| 목적 | 계량 정확도 모니터링, 문제 재료 식별 |

### 3.4 일자별 추이 차트

| Item | Detail |
|------|--------|
| 표시 | 기간 내 일별 완료 레시피 수 라인 차트 |
| 겹침 | 일별 총 재료 사용량 (이중 Y축) |

### 3.5 담당자별 실적 (선택)

| Item | Detail |
|------|--------|
| 표시 | 계량 담당자별 완료 건수, 평균 편차% |
| 목적 | 교육/평가용 참고 (책임자만 열람) |

### 3.6 Backend API

| 엔드포인트 | 용도 |
|---|---|
| `GET /api/dashboard/summary?from=&to=` | 요약 카드 수치 |
| `GET /api/dashboard/materials?from=&to=&limit=` | 재료별 사용량 랭킹 |
| `GET /api/dashboard/deviation?from=&to=` | 편차 분석 |
| `GET /api/dashboard/trend?from=&to=` | 일자별 추이 |
| `GET /api/dashboard/operators?from=&to=` | 담당자별 실적 |

모두 manager 권한.

## 4. Scope

### In Scope
- 책임자 전용 `/dashboard` 페이지 (Jinja2 템플릿 + vanilla JS + 경량 차트 라이브러리)
- 5개 신규 API (manager only)
- 기간 필터 (today/7d/30d/custom)
- 기존 DB 그대로 사용, 스키마 변경 없음

### Out of Scope
- 실시간 갱신 (주기적 새로고침 또는 수동 새로고침)
- 엑셀/PDF 내보내기 (다음 사이클)
- 재고 예측/발주 추천
- 모바일 전용 레이아웃 (2-tier 앱이 데스크톱 중심)

## 5. Success Criteria

1. `/dashboard` 접속 시 기본(7일) 기준 요약 카드 4개가 즉시 표시됨
2. 기간 필터 변경 시 모든 섹션이 갱신됨
3. 재료 사용량 랭킹 상위 10개가 바 차트로 표시되고 클릭 시 해당 재료 레시피 목록이 나타남
4. 편차 분석이 평균/최대/상위 목록을 표시함
5. 일자별 추이 라인 차트가 렌더링됨
6. operator가 `/dashboard` 접근 시 차단되고 manager만 허용됨

## 6. Open Questions

1. **차트 라이브러리** — Chart.js(유명, ~60KB) vs 경량 자체 SVG 구현 vs 기존 번들 내 다른 lib? 오프라인 현장 환경이라 vendor 디렉토리에 로컬 번들 필요.
2. **편차 계산 기준** — `actual_weight`가 NULL인 항목(미완료/수기 입력 미지원)을 어떻게 처리할 것인가 — 제외 vs 0으로 간주?
3. **담당자별 실적 섹션 포함 여부** — 책임자가 원하는 기능인지, 혹은 개인정보/평가 민감성 때문에 제외할지?
4. **기본 표시 기간** — 오늘 / 7일 / 30일 중 기본값은 무엇이 적절한가?
5. **기간 필터 범위 상한** — 최대 1년? 성능상 제한 필요?
