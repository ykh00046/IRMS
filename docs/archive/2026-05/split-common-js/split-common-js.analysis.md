# split-common-js Gap Analysis (Phase 2)

> **Match Rate**: **99%** — 설계 대비 구현이 5개 평가 범주 전체에서 일치. 코드 수정 없이 Report 단계로 진행 가능.
>
> **Phase**: Check (PDCA)
> **Date**: 2026-05-19
> **Commit**: `b9e38bb`
> **Agent**: bkit:gap-detector
> **Recommendation**: `/pdca report split-common-js`로 진행

---

## 1. Overview

| Item | Value |
|---|---|
| Analysis Target | split-common-js Phase 2 (`static/js/common.js` 분리) |
| Design Document | `docs/02-design/features/split-common-js.design.md` |
| Plan Document | `docs/01-plan/features/split-common-js.plan.md` |
| Implementation Commit | `b9e38bb` |
| Code Files Verified | 12 modules + 1 bootstrap wrapper |
| Templates Verified | 10/10 |
| Public Symbols Verified | 63/63 |

---

## 2. 11개 모듈 존재 여부 (파일명·책임 일치)

설계 §1.1: "11개 도메인 모듈 + 1개 부트스트랩 래퍼". 전체 12개 파일 모두 존재, 책임 일치.

| # | 모듈 | 존재 | 책임 일치 |
|---|------|:---:|:---:|
| 1 | `common/core.js` | ✅ | `request`, `getCsrfToken`, `safeNextUrl`, `detailToText` → `IRMS._core` |
| 2 | `common/mappers.js` | ✅ | 매퍼 9종 → `IRMS._mappers` |
| 3 | `common/format.js` | ✅ | 포매터 11종 |
| 4 | `common/api-users.js` | ✅ | 11개 함수 |
| 5 | `common/api-recipes.js` | ✅ | 11개 함수 |
| 6 | `common/api-stock.js` | ✅ | 6개 함수 |
| 7 | `common/api-chat.js` | ✅ | 4개 함수 |
| 8 | `common/api-spreadsheet.js` | ✅ | 10개 함수 |
| 9 | `common/api-stats.js` | ✅ | 2개 함수 |
| 10 | `common/ui.js` | ✅ | public 6개 + `bindLogoutButton` 부수효과 |
| 11 | `common/audio.js` | ✅ | `playChatSound`, `speakText` + 리스너 2개 |
| 12 | `common/polling.js` | ✅ | `pollNegativeStock` + 자동 부트스트랩 + 중복 가드 |
| — | `common.js` (부트스트랩 래퍼) | ✅ | 93 LOC, nav/chat-float/Enter/`initTableScrollHints`만 |

**결과: 12/12 모듈 존재, 책임 모두 일치.**

---

## 3. `window.IRMS` API 시그니처 보존 (설계 목표: 100%)

설계 §4: public 표면 = **63개 심볼** (원본 `window.IRMS={}` 블록 58개 + 부수 부착 5개). 모듈별 export 블록 전수 확인.

| 도메인 | 설계 개수 | 구현 개수 | 일치 |
|--------|:--------:|:--------:|:---:|
| Auth/Users (api-users.js) | 11 | 11 | ✅ |
| Recipes (api-recipes.js) | 11 | 11 | ✅ |
| Stock+Weighing (api-stock.js) | 6 | 6 | ✅ |
| Chat (api-chat.js) | 4 | 4 | ✅ |
| Spreadsheet (api-spreadsheet.js) | 10 | 10 | ✅ |
| Stats (api-stats.js) | 2 | 2 | ✅ |
| Format (format.js) | 11 | 11 | ✅ |
| UI (ui.js) | 6 | 6 | ✅ |
| Audio (audio.js) | 2 | 2 | ✅ |
| **합계** | **63** | **63** | **63/63** |

내부 네임스페이스도 설계 §4.4와 일치: `IRMS._core`, `IRMS._mappers`(매퍼 9종), `IRMS._negStockPollingStarted` 가드.

**결과: 63/63 public 심볼 보존. 100% 시그니처 보존 목표 달성.**

---

## 4. 번들러 미도입 원칙

| 검사 | 기대 | 실제 | 일치 |
|------|------|------|:---:|
| 번들러 (esbuild/vite/webpack) 없음 | 금지 | `package.json` 없음, 빌드 설정 없음 | ✅ |
| ES Module 없음 | 금지 | `common/*.js`에 `import`/`export`/`require()` 0건 | ✅ |
| TypeScript 없음 | 금지 | 전부 `.js`, 순수 JS | ✅ |
| IIFE 점진 패턴 | 모듈별 `(function(){"use strict";...})()` | 12개 모듈 모두 IIFE + `const IRMS = window.IRMS = window.IRMS || {}` 패턴 | ✅ |

**결과: 번들러 미도입 원칙 완전 준수.**

---

## 5. 템플릿 `<script>` 로드 순서

설계 §5.1: 영향받는 10개 템플릿. 전부 13줄 로드 블록을 설계 순서(core → mappers → format → api-users → api-recipes → api-stock → api-chat → api-spreadsheet → api-stats → ui → audio → polling → common.js)대로 보유.

| 템플릿 | 블록 존재 | 순서 정확 |
|--------|:---:|:---:|
| `_base_app.html` | ✅ | ✅ |
| `attendance.html` | ✅ | ✅ |
| `attendance_change_password.html` | ✅ | ✅ |
| `attendance_login.html` | ✅ | ✅ |
| `entry.html` | ✅ | ✅ |
| `entry_test.html` | ✅ | ✅ |
| `ink_plan.html` | ✅ | ✅ |
| `login.html` | ✅ | ✅ |
| `management_login.html` | ✅ | ✅ |
| `weighing_select.html` | ✅ | ✅ |

`base.html`은 `common.js`를 로드하지 않으므로 그대로 둔 것이 정확(설계 10-템플릿 범위와 일치). 의존성 순서 충족: `core` 최우선, APIs 앞에 `mappers`/`format`, `ui` 앞에 `api-users`(`bindLogoutButton`→`logout` 필요), `polling` 앞에 `format`(`escapeHtml` 필요), `common.js` 부트스트랩 최후.

**결과: 10개 템플릿 모두 의존성 순서대로 갱신 완료.**

---

## 6. 누락 / 초과 / 불일치

### 누락 (설계 O, 구현 X)
없음. 모든 모듈·심볼·부수효과·중복 가드 구현 완료.

### 초과 (설계 X, 구현 O)
없음. 설계된 63개 + 내부 네임스페이스 2개 외 심볼 없음.

### Minor 불일치 (비차단)

| # | 항목 | 설계 | 구현 | 영향 |
|---|------|------|------|------|
| M1 | Plan/Design 개수 불일치 | Plan은 "56개 함수", Design §4.1은 "2026-05-13 정정"으로 63개 | 63개 구현(Design 일치) | 문서 한정 — 코드 갭 아님 |
| M2 | `audio.js` 노출 방식 | Plan §3.11은 `IRMS.audio={}` 네임스페이스 제안, Design §3.11이 직접 부착으로 재정의 | Design을 따름(정확 — 원본 public 표면 보존) | 없음 — Design이 Plan 상위 |
| M3 | `audio.js` 지역변수 컨벤션 | 설계 §10.2는 `const`/`let` | `audio.js`가 `var` 사용 | 외형적 — "0줄 본문 변경" 순수성 원칙(§1.2)에 따른 원본 그대로 복사. 의도적 |
| M4 | §5.2.1 페이지 스크립트 순서 | 페이지 스크립트는 공통 블록 뒤 로드 | 10개 base 템플릿 범위에서는 검증 불가(자식 템플릿 Jinja block 주입) | 낮음 — 스모크 테스트에서 확인 권장 |

---

## 7. Match Rate 산출

| 범주 | 가중치 | 점수 | 가중점 |
|------|---:|---:|---:|
| 모듈 구조 (12/12) | 25 | 100% | 25.00 |
| API 시그니처 보존 (63/63) | 30 | 100% | 30.00 |
| 번들러 미도입 원칙 | 15 | 100% | 15.00 |
| 템플릿 로드 순서 (10/10) | 20 | 100% | 20.00 |
| 컨벤션 준수 | 10 | 98% | 9.80 |
| **합계** | **100** | — | **99.80** |

**Computed raw**: 99.8%
**Reported**: **99%** (Plan-vs-Design 문서 불일치 M1 + `audio.js` `var` 컨벤션 M3 인정).

---

## 8. Gaps

### Minor (문서 한정, 코드 조치 불필요)
1. **M1** — Plan 문서가 "56개 함수"로 5곳 기재. Design §4.1이 이미 63개로 자체 정정함. 코드는 Design(63)과 일치하므로 코드 갭 아님. 본 분석 문서와 Report가 63개로 기록 → 정합성 확보.
2. **M3** — `audio.js`의 `var` 사용은 설계 §1.2 "0줄 본문 변경" 순수성 원칙에 따른 원본 그대로 복사. 의도적 — 동작 동일성 보장 목적. 코드 변경 불필요.

### Critical
없음.

---

## 9. Recommendation

✅ **Match Rate ≥ 90%** — split-common-js Phase 2는 무손실 순수 분리. iteration 불필요.

**다음 단계**: `/pdca report split-common-js` → 완료 보고서 생성 후 `docs/archive/2026-05/split-common-js/`로 archive.

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-19 | bkit:gap-detector 기반 초기 Gap 분석 |
