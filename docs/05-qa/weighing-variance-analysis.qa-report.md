# QA 리포트 — weighing-variance-analysis

> 검증일 2026-06-18 · 대상 Design §7 시나리오 + 자동/수기 점검
> 판정: **PASS (조건부 — 언어 일관성 1건 외부 프로세스 충돌, 아래 5번)**

## 1. 자동 테스트

| 스위트 | 명령 | 결과 |
|--------|------|------|
| 신규 단위 | `pytest tests/test_weighing_variance.py -v` | **9 passed** |
| 전체 Python | `pytest tests -q` | **206 passed, 10 subtests, 1 warning** |
| JavaScript | `node --test tests/js/*.test.js` | **5 passed** |

회귀 0. 기존 스위트 영향 없음.

## 2. 시나리오 결과 (Design §7)

| ID | 시나리오 | 결과 |
|----|----------|:----:|
| V1 | summary 부분 실측 커버리지/폴백/편차 | ✅ |
| V2 | summary 실측 0건 | ✅ |
| V3 | summary 범위 밖 제외 | ✅ |
| V4 | materials \|편차\|DESC + 실측0 제외 | ✅ |
| V5 | materials limit | ✅ |
| V6 | recipes 편차/편차율 + \|편차\|DESC | ✅ |
| V7 | recipes 목표0 → 편차율 null | ✅ |
| V8 | 라우트 비인증 401/403 (3종) | ✅ |
| V9 | recipes 미존재 자재 → [] (서비스) | ✅ |

## 3. 통합 점검 (자동 스크립트)

- **라우트 등록**: `/api/dashboard/variance/summary`, `/variance/materials`,
  `/variance/materials/{material_id}/recipes` 3종 모두 앱에 등록 — OK.
- **HTML↔JS ID 정합**: dashboard.js의 `getElementById` 참조 ID가 dashboard.html에 모두 존재 — OK
  (`card-actual-coverage`, `card-variance-total`, `chart-variance`, `variance-*`, `variance-modal*`).

## 4. 수기 QA 가이드 (운영 확인용)

1. manager 로그인 → `/dashboard` 진입 → 요약 카드에 "실측 커버리지(%)·총 편차(g)" 표시.
2. 기간 프리셋(오늘/7일/30일) 전환 시 편차 차트·요약표가 함께 갱신(필터 연동).
3. "계량 편차 TOP 10" 막대 클릭 → 드릴다운 모달에 레시피별 목표/실측/편차/편차율 표시.
4. 미존재 자재 직접 호출 → 404(라우터). 실측 미입력 기간 → 빈 상태 안내(오류 토스트 없음).
5. operator/비인증 → 401·403.

## 5. ⚠ 미해결 — 대시보드 언어 일관성 (외부 프로세스 충돌)

- 작업 중 외부 프로세스가 `templates/dashboard.html`을 **2회 영어로 강제 복원**함.
  현재 상태: **HTML 영어 / dashboard.js 한국어**(혼재).
- 프로젝트 규약 `feedback_korean_ui`(현장 한국 운영자 대상 한국어 통일)와 충돌.
- 본 PDCA에서 한국어 현지화를 시도했으나 HTML이 반복적으로 되돌려져, **추가 thrash 방지를 위해
  재시도 중단**. JS는 규약대로 한국어 유지.
- **권장 조치**: 영어로 되돌리는 외부 주체(린터/포맷터/병행 에이전트)를 식별·중지한 뒤
  HTML을 한국어로 재정렬하거나, 영어를 정식으로 채택할 경우 `feedback_korean_ui` 메모리를 갱신.
  → 기능 동작과 무관(라벨 텍스트만 해당). 사용자 결정 대기.
