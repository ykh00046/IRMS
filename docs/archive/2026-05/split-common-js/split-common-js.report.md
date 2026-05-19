# split-common-js PDCA Completion Report

> **Status**: COMPLETED
> **Feature**: split-common-js (Phase 2 — JavaScript `static/js/common.js` 분리)
> **Commit**: `b9e38bb`
> **Date**: 2026-05-19
> **Match Rate**: 99% (check passed)
> **Author**: ykh00046

---

## Executive Summary (한국어)

`static/js/common.js` (1,218줄, 단일 IIFE) → 책임별 11개 도메인 모듈 + 1개 부트스트랩 래퍼로 순수 분리. `window.IRMS` public API 63개 심볼, 내부 네임스페이스 2개(`_core`/`_mappers`), 부수효과(리스너·로그아웃 바인딩·재고 폴링 가드) 모두 100% 보존. 번들러·ES Module·TypeScript 미도입 원칙 유지(IIFE + `window.IRMS` 네임스페이스 패턴 고수). 10개 템플릿의 `<script>` 로드 블록을 의존성 순서대로 갱신. 설계 대비 구현 일치도 99%. split-large-files 이니셔티브 Phase 2 완료.

---

## Plan vs Outcome

| In-Scope Item (Plan §4) | Delivered | Notes |
|---|:---:|---|
| `common.js` → 11개 도메인 모듈 분리 | ✅ | `static/js/common/*.js` 11개 신규 |
| 진입 부트스트랩 래퍼 유지 | ✅ | `common.js` 93 LOC로 축소 (nav/chat-float/Enter/`initTableScrollHints`) |
| `window.IRMS` API 시그니처 100% 보존 | ✅ | 63/63 public 심볼 + `_core`/`_mappers` 내부 네임스페이스 보존 |
| 번들러 미도입 | ✅ | `package.json` 없음, `import`/`export`/`require()` 0건 |
| IIFE 패턴 고수 | ✅ | 12개 모듈 모두 `(function(){"use strict";...})()` |
| 10개 템플릿 `<script>` 로드 순서 갱신 | ✅ | 13줄 로드 블록, 의존성 순서 정확 |
| 부수효과(audio 리스너·재고 폴링) 보존 | ✅ | `polling.js` 자동 부트스트랩 + 중복 가드 보존 |

**Result**: **100% 계획 달성**.

---

## Implementation Highlights

### 구조 개선 — 11개 도메인 모듈

| 모듈 | LOC | 책임 |
|------|---:|------|
| `common/core.js` | 146 | HTTP 클라이언트 — `request`, `getCsrfToken`, `safeNextUrl`, `detailToText` → `IRMS._core` |
| `common/mappers.js` | 172 | 응답 매퍼 9종 → `IRMS._mappers` |
| `common/format.js` | 145 | 포매터·유틸 11종 (`formatDateTime`, `escapeHtml`, `debounce` 등) |
| `common/api-users.js` | 126 | auth/users/audit API 11개 |
| `common/api-recipes.js` | 191 | recipe API 11개 |
| `common/api-stock.js` | 91 | stock/weighing API 6개 |
| `common/api-chat.js` | 69 | chat API 4개 |
| `common/api-spreadsheet.js` | 84 | spreadsheet API 10개 |
| `common/api-stats.js` | 65 | stats API 2개 |
| `common/ui.js` | 161 | UI 헬퍼 6개 + `bindLogoutButton` 부수효과 |
| `common/audio.js` | 89 | 오디오/음성 — `playChatSound`, `speakText` + 리스너 2개 |
| `common/polling.js` | 62 | `pollNegativeStock` + 자동 부트스트랩 + 중복 가드 |
| `common.js` (래퍼) | 93 | 부트스트랩 — nav/chat-float/Enter/`initTableScrollHints` |

### 설계 원칙 준수

- **순수 분리 원칙(§1.2)**: 본문 0줄 변경. 코드 이동만 발생 — `audio.js`의 `var`도 동작 동일성 보장 위해 원본 그대로 복사.
- **public 표면 불변**: 원본 `window.IRMS={}` 블록 58개 + 부수 부착 5개 = 63개 심볼 전수 보존.
- **번들러 0 정책**: split-large-files Phase 1에서 carry-forward된 위험을 IIFE + window namespace로 차단.
- **의존성 기반 로드 순서**: `core` 최우선 → `mappers`/`format` → APIs → `ui`/`audio`/`polling` → `common.js` 부트스트랩.

---

## Metrics

### 라인 수 (before → after)

| Module | Before | After | Δ |
|---|---:|---:|:---:|
| `common.js` (단일 IIFE) | 1,218 | 93 | −1,125 |
| `common/*.js` 11개 신규 | — | 1,401 | +1,401 |
| **Subtotal** | 1,218 | **1,494** | **+276** |

> **Note**: `+276`은 모듈별 IIFE 래퍼·헤더 JSDoc(책임/exports/부수효과/의존성)·`const IRMS = ...` 선언이 12회 반복된 결과. 본문 로직은 1줄도 변경되지 않음. 단일 파일 1,218줄 → 최대 191줄 모듈로 분산되어 코드 리뷰·동시 작업 진입 장벽 대폭 하락.

### 검증

| Type | Count | Status |
|---|---:|:---:|
| 모듈 존재·책임 확인 | 12 | ✅ OK |
| `window.IRMS` public 심볼 보존 | 63 | ✅ OK |
| 내부 네임스페이스 보존 (`_core`/`_mappers`) | 2 | ✅ OK |
| 템플릿 로드 블록 갱신 | 10 | ✅ OK |
| 번들러/ESM 미도입 정적 검증 | — | ✅ OK |

---

## Gap Analysis Summary (Analysis §1-9)

| Category | Score | Notes |
|---|:---:|---|
| 모듈 구조 (12/12) | 100% | 11개 도메인 모듈 + 부트스트랩 래퍼, 책임 일치 |
| API 시그니처 보존 (63/63) | 100% | public 63개 + 내부 네임스페이스 2개 보존 |
| 번들러 미도입 원칙 | 100% | `package.json` 없음, ESM 0건, IIFE 패턴 |
| 템플릿 로드 순서 (10/10) | 100% | 13줄 블록, 의존성 순서 정확 |
| 컨벤션 준수 | 98% | `audio.js` `var` 사용(의도적 — 0줄 변경 원칙) |

**Computed Match Rate**: 99% (raw 99.8%, 문서 불일치 M1 + 컨벤션 M3 인정)

---

## Lessons Learned

### 1. Plan-vs-Design 개수 정정의 추적

Plan 문서는 분리 대상을 "56개 함수"로 추정했으나, Design §4.1이 호출처 전수 조사 후 "2026-05-13 정정"으로 63개(원본 export 58 + 부수 부착 5)로 상향했다. 구현은 Design을 따랐고 정확하다. **교훈**: Plan의 추정치는 Design 단계에서 실측으로 정정될 수 있으며, 정정 이력을 Design 문서에 명시해 두면 Gap 분석 시 코드 갭과 문서 갭을 명확히 구분할 수 있다.

### 2. 순수 분리에서 컨벤션과 동작 동일성의 충돌

`audio.js`는 컨벤션(§10.2 `const`/`let`)을 따르면 `var`를 바꿔야 하지만, 설계 §1.2 "본문 0줄 변경" 순수성 원칙은 원본 그대로 복사를 요구한다. 후자를 택해 `var`를 유지했다. **교훈**: 순수 분리 리팩터링에서는 컨벤션 정렬보다 동작 동일성이 우선한다. 컨벤션 정리는 분리가 끝난 뒤 별도 커밋으로 분리해야 한다.

### 3. 번들러 도입 유혹의 차단

11개 모듈로 쪼개면 ES Module + 번들러가 자연스러워 보이지만, IRMS는 서버사이드 Jinja2 + `<script>` 직접 로드 구조다. 번들러 도입은 빌드 파이프라인·배포 절차(현장 `update_and_run.bat`)를 모두 바꾼다. IIFE + `window.IRMS` 네임스페이스 패턴을 고수해 배포 절차를 1줄도 건드리지 않았다. **교훈**: 모듈화의 목표는 "파일 분리"이지 "빌드 시스템 도입"이 아니다. 운영 환경 제약을 먼저 본다.

### 4. 의존성 기반 로드 순서

11개 모듈을 단순 알파벳 순으로 로드하면 `ui.js`가 `api-users.js`의 `logout`보다 먼저 로드되어 `bindLogoutButton`이 깨진다. 의존성 그래프를 그려 `core → mappers/format → APIs → ui/audio/polling → 부트스트랩` 순서를 확정하고 10개 템플릿에 동일 블록으로 박았다. **교훈**: 모듈 분리의 실질 위험은 코드가 아니라 로드 순서다. 순서를 설계 표와 템플릿 주석 양쪽에 명시한다.

---

## Risks Closed & Carried Forward

### Closed (이 PDCA로 해소)

| Risk | 원 Impact | 해소 방법 |
|---|---|---|
| **`window.IRMS` API 누락** | High | 63개 심볼 호출처 전수 조사 → 63/63 보존 확인 |
| **`<script>` 로드 순서 오류** | High | 의존성 그래프 기반 13줄 블록, 10개 템플릿 일괄 적용 |
| **ES Module 마이그레이션 유혹** | Medium | 번들러 0 정책, IIFE + window namespace 고수 |
| **부수효과 손실** (audio 리스너·재고 폴링) | Medium | `polling.js` 중복 가드 + 자동 부트스트랩 보존 검증 |

### Carried Forward

| Risk | 대응 |
|---|---|
| **§5.2.1 페이지 스크립트 순서** (M4) | base 템플릿 범위 밖 — 자식 페이지(`work.js`/`chat.js` 등) 렌더 시 DevTools Console 0 errors 수동 스모크로 확인 권장 |
| **JS 테스트 부재** | `/pdca plan tests-coverage`로 후속 진행 |

---

## Next Steps

### 즉시 (현재 사이클 완료)

1. ✅ 이 완료 보고서 확인
2. plan/design/analysis/report를 `docs/archive/2026-05/split-common-js/`로 archive
3. `docs/_INDEX.md`, `docs/archive/2026-05/_INDEX.md`, `.bkit-memory.json` 갱신

### 후속

- split-large-files 이니셔티브 Phase 3/4 (잔여 대용량 파일 분리) — 필요 시 `/pdca plan`
- `/pdca plan tests-coverage` — JS 모듈 단위 테스트 커버리지
- 잉크/사출 생산계획 기능 정식 PDCA 사이클 시작 (`/pdca plan`)

---

## Appendix

### 파일 목록

**신규 (11개)** — `static/js/common/`
`core.js`(146) · `mappers.js`(172) · `format.js`(145) · `api-users.js`(126) · `api-recipes.js`(191) · `api-stock.js`(91) · `api-chat.js`(69) · `api-spreadsheet.js`(84) · `api-stats.js`(65) · `ui.js`(161) · `audio.js`(89) · `polling.js`(62)

**수정 (11개)**
- `static/js/common.js` — 1,218 → 93 LOC 부트스트랩 래퍼로 축소
- 템플릿 10개 — `<script>` 로드 블록 갱신: `_base_app.html`, `attendance.html`, `attendance_change_password.html`, `attendance_login.html`, `entry.html`, `entry_test.html`, `ink_plan.html`, `login.html`, `management_login.html`, `weighing_select.html`

### Commit Reference

| Field | Value |
|---|---|
| **SHA** | `b9e38bb` |
| **Date** | 2026-05-19 |
| **Branch** | main |

### 관련 문서

- **Plan**: `docs/01-plan/features/split-common-js.plan.md`
- **Design**: `docs/02-design/features/split-common-js.design.md`
- **Analysis**: `docs/03-analysis/split-common-js.analysis.md`
- **Parent PDCA**: `docs/archive/2026-05/split-large-files/`

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 1.0 | 2026-05-19 | Phase 2 split-common-js PDCA 완료 보고서 | ykh00046 |
