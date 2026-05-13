# Split common.js Plan (Phase 2 of split-large-files initiative)

> `static/js/common.js` 1,218줄을 책임별 11개 모듈로 분리하여 단위 테스트·코드 리뷰·동시 작업의 부담을 줄이는 리팩터링 계획서. `window.IRMS` API 시그니처 100% 보존, 번들러 도입 없음.

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | split-common-js (Phase 2) |
| Priority | Medium (장기 부채 정리, Phase 1 후속) |
| Base | 2026-05-13 main, commit `82d7d6e` (Phase 1 archived) |
| Goal | `static/js/common.js` (1,218 LOC, IIFE) → 책임별 11개 모듈 + 진입 래퍼 |
| Deliverable | `static/js/common/*.js` (11개) + 얇아진 `static/js/common.js` + 템플릿 `<script>` 로드 순서 갱신 |
| Author | ykh00046 |
| Date | 2026-05-13 |
| Status | Draft |
| Parent PDCA | [`docs/archive/2026-05/split-large-files`](../../archive/2026-05/split-large-files/) |

---

## 2. Problem Statement

`common.js`는 IIFE(`(function() { "use strict"; ... })()`) 한 덩어리로 다음을 모두 담고 있다:

1. **HTTP 클라이언트** — `request()`, `getCsrfToken()`, `safeNextUrl()`, `detailToText()`
2. **응답 매퍼 9종** — `mapUser`, `mapAdminUser`, `mapAuditLog`, `mapChatRoom`, `mapChatMessage`, `mapMaterial`, `mapRecipe`, `mapPreview`, `mapWeighingStep`
3. **API 클라이언트 6종 도메인** — auth/users/audit/recipes/chat/stock/spreadsheet/weighing/stats
4. **포매터·유틸** — `formatDateTime`, `formatValue`, `statusLabel`, `escapeHtml`, `debounce`, `loadPreference` 등
5. **UI 헬퍼** — `notify`, `showLoading`, `hideLoading`, `btnLoading`, `bindLogoutButton`, `bindLoginForm`
6. **오디오/음성** — `playChatSound`, `resumeAudioCtx`, `speakText`, `speakNextQueuedText`
7. **폴링** — `pollNegativeStock`
8. **DOM 부트스트랩** — 모바일 nav-toggle, chat-float, Enter 전송, `initTableScrollHints`

라인 906-965에서 `window.IRMS = { ... }`로 56개 함수를 한 번에 export. 모든 페이지(`/management`, `/work`, `/status`, `/attendance`, `/login` 등)가 `_base_app.html` 또는 자체 템플릿에서 `<script src="/static/js/common.js">`로 로드.

### Pain Points

1. **단위 테스트 불가** — 함수가 IIFE 클로저 안에 갇혀 있어 Jest에서 import 불가
2. **코드 리뷰 부담** — 채팅 알림 음 한 줄 고치는 PR이 1,218줄 파일 전체로 표시됨
3. **머지 충돌 빈발** — 관리자/작업자 페이지 작업이 같은 파일을 동시 편집
4. **책임 추적 어려움** — `notify` 호출 경로를 추적하려면 1,200줄 스캔

---

## 3. Feature Items

### 3.1 `static/js/common/core.js` 신규 (HTTP·CSRF 코어)

| Item | Detail |
|------|--------|
| 목표 | `window.IRMS` 객체 초기화 + HTTP request 코어 노출 |
| 포함 함수 | `getCsrfToken`, `safeNextUrl`, `detailToText`, `request` |
| 진입 위치 | `static/js/common/core.js` |
| 부수효과 | `window.IRMS = window.IRMS \|\| {}; window.IRMS._core = { request, ... }` |
| 의존 | 없음 (가장 먼저 로드) |
| 예상 LOC | ~100 |

### 3.2 `static/js/common/mappers.js` 신규 (응답 매퍼)

| Item | Detail |
|------|--------|
| 목표 | API 응답 row → 정규화된 객체로 매핑하는 9개 함수 |
| 포함 함수 | `mapUser`, `mapAdminUser`, `mapAuditLog`, `mapChatRoom`, `mapChatMessage`, `mapMaterial`, `mapRecipe`, `mapPreview`, `mapWeighingStep` |
| 진입 | `window.IRMS._mappers = { mapUser, ... }` (내부용, IRMS 공개 API 아님) |
| 의존 | core.js (선행 로드만 — 직접 호출 없음) |
| 예상 LOC | ~120 |

### 3.3 `static/js/common/api-users.js` 신규 (인증·사용자·감사로그 API)

| Item | Detail |
|------|--------|
| 포함 함수 | `login`, `loginManager`, `loginOperator`, `logout`, `getCurrentUser`, `listUsers`, `createUser`, `updateUser`, `resetUserPassword`, `deleteUser`, `listAuditLogs` |
| 진입 | `window.IRMS.login = ...`, `window.IRMS.listAuditLogs = ...` 등 (총 11개 export) |
| 의존 | core.js, mappers.js |
| 예상 LOC | ~110 |

### 3.4 `static/js/common/api-recipes.js` 신규 (레시피·상태·import API)

| Item | Detail |
|------|--------|
| 포함 함수 | `getRecipeImportNotifications`, `getRecipeProgress`, `getOperatorProgress`, `getRecipes`, `updateRecipeStatus`, `deleteRecipe`, `previewImport`, `importRecipes`, `getProducts`, `getRecipesByProduct`, `getRecipeDetail` |
| 진입 | IRMS 공개 API 11개 |
| 의존 | core.js, mappers.js |
| 예상 LOC | ~130 |

### 3.5 `static/js/common/api-stock.js` 신규 (재료·재고·계량 API)

| Item | Detail |
|------|--------|
| 포함 함수 | `getMaterials`, `getWeighingQueue`, `completeWeighingStep`, `undoWeighingStep`, `completeWeighingRecipe`, `resetWeighingRecipe` |
| 진입 | IRMS 공개 API 6개 |
| 의존 | core.js, mappers.js |
| 예상 LOC | ~80 |

### 3.6 `static/js/common/api-chat.js` 신규 (채팅 API)

| Item | Detail |
|------|--------|
| 포함 함수 | `listChatRooms`, `getChatMessages`, `postChatMessage`, `clearChatMessages` |
| 진입 | IRMS 공개 API 4개 |
| 의존 | core.js, mappers.js |
| 예상 LOC | ~50 |

### 3.7 `static/js/common/api-spreadsheet.js` 신규 (스프레드시트 API)

| Item | Detail |
|------|--------|
| 포함 함수 | `ssListProducts`, `ssCreateProduct`, `ssUpdateProduct`, `ssDeleteProduct`, `ssLoadSheet`, `ssSaveSheet`, `ssAddColumn`, `ssDeleteColumn`, `ssAddRow`, `ssDeleteRow` |
| 진입 | IRMS 공개 API 10개 |
| 의존 | core.js |
| 예상 LOC | ~60 |

### 3.8 `static/js/common/api-stats.js` 신규 (통계 API)

| Item | Detail |
|------|--------|
| 포함 함수 | `getStats`, `exportStatsCsv` |
| 진입 | IRMS 공개 API 2개 |
| 의존 | core.js |
| 예상 LOC | ~40 |

### 3.9 `static/js/common/format.js` 신규 (포매터·유틸)

| Item | Detail |
|------|--------|
| 포함 함수 | `statusLabel`, `statusClass`, `formatDateTime`, `toDateOnly`, `formatValue`, `escapeHtml`, `debounce`, `loadPreference`, `savePreference`, `clearPreference`, `colorLabel` |
| 진입 | IRMS 공개 API 11개 |
| 의존 | 없음 (순수 함수, core.js 의존 없음) |
| 예상 LOC | ~150 |

### 3.10 `static/js/common/ui.js` 신규 (UI 헬퍼)

| Item | Detail |
|------|--------|
| 포함 함수 | `notify`, `showLoading`, `hideLoading`, `btnLoading`, `bindLogoutButton`, `bindLoginForm`, `initTableScrollHints` |
| 진입 | IRMS 공개 API 4개 (`notify`, `showLoading`, `hideLoading`, `btnLoading`) + 내부 부트스트랩(`bindLogoutButton`, `bindLoginForm`, `initTableScrollHints`) |
| 의존 | core.js, format.js |
| 예상 LOC | ~150 |

### 3.11 `static/js/common/audio.js` 신규 (오디오·TTS)

| Item | Detail |
|------|--------|
| 포함 함수 | `playChatSound`, `resumeAudioCtx`, `speakText`, `speakNextQueuedText` |
| 진입 | `window.IRMS.audio = { playChatSound, speakText, ... }` (내부용, 기존 공개 API에는 없음) |
| 의존 | core.js |
| 예상 LOC | ~100 |

### 3.12 `static/js/common/polling.js` 신규 (재고 폴링)

| Item | Detail |
|------|--------|
| 포함 함수 | `pollNegativeStock` |
| 진입 | 자동 시작 (DOMContentLoaded 시 setInterval 등록) |
| 의존 | core.js, api-stock.js, ui.js |
| 예상 LOC | ~60 |

### 3.13 `static/js/common.js` 진입 래퍼 축소

| Item | Detail |
|------|--------|
| 목표 | 기존 1,218줄 → ~80줄 부트스트랩 전용 |
| 잔여 책임 | DOMContentLoaded·nav-toggle·chat-float·Enter-전송 키 처리만 |
| 동작 | 11개 모듈이 미리 로드되어 `window.IRMS`가 완성된 상태에서 마지막에 실행 |
| 예상 LOC | ~80 |

### 3.14 템플릿 `<script>` 로드 순서 갱신

10개 템플릿(`_base_app.html`, `attendance*.html`, `entry*.html`, `ink_plan.html`, `login.html`, `management_login.html`, `weighing_select.html`)이 `common.js`를 로드. 모두 다음 순서로 교체:

```html
<!-- Order matters: core → mappers → APIs → format → ui → audio → polling → bootstrap -->
<script src="/static/js/common/core.js"></script>
<script src="/static/js/common/mappers.js"></script>
<script src="/static/js/common/format.js"></script>
<script src="/static/js/common/api-users.js"></script>
<script src="/static/js/common/api-recipes.js"></script>
<script src="/static/js/common/api-stock.js"></script>
<script src="/static/js/common/api-chat.js"></script>
<script src="/static/js/common/api-spreadsheet.js"></script>
<script src="/static/js/common/api-stats.js"></script>
<script src="/static/js/common/ui.js"></script>
<script src="/static/js/common/audio.js"></script>
<script src="/static/js/common/polling.js"></script>
<script src="/static/js/common.js"></script>  <!-- bootstrap last -->
```

---

## 4. Scope

### 4.1 In Scope

- [ ] 11개 신규 `static/js/common/*.js` 파일 생성
- [ ] `static/js/common.js` 축소 (1,218 → ~80줄)
- [ ] 10개 템플릿의 `<script>` 로드 순서 갱신
- [ ] 기존 `window.IRMS` 56개 함수 시그니처 100% 보존
- [ ] 기존 JS 테스트 3개(`tests/js/`) 통과
- [ ] 페이지별 수동 스모크 (관리자/작업자/근태/로그인)

### 4.2 Out of Scope

- ❌ 번들러(esbuild/vite/webpack) 도입
- ❌ ES Modules(`import`/`export`) 전환
- ❌ TypeScript
- ❌ 로직 변경·성능 개선·UX 변경 (순수 분리만)
- ❌ Phase 3/4 (`management.js`, `work.js`) — 별도 PDCA로 진행

---

## 5. Requirements

### 5.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `window.IRMS.*` 공개 API 56개 시그니처 100% 보존 | High | Pending |
| FR-02 | 분리 후 단일 파일 ≤ 200 LOC (도메인 작아서 한도 낮춤) | High | Pending |
| FR-03 | 모듈 로드 순서 의존성 명시 (HTML 주석 + 본 plan 표 동기화) | Medium | Pending |
| FR-04 | 기존 JS 테스트 (`tests/js/` 3개) 모두 통과 | High | Pending |
| FR-05 | 브라우저 콘솔 에러 0건 (관리자/작업자/근태 골든패스) | High | Pending |

### 5.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|---|---|---|
| 페이지 로드 시간 | 기존 대비 +300ms 이내 (12개 `<script>` 추가 영향) | DevTools Network 탭, 캐시 비활성화 |
| 콘솔 에러 | `Uncaught ReferenceError` 0건 (로드 순서 문제 검증) | DevTools Console |
| 모듈 의존성 그래프 | 순환 없음 (core → mappers → api-* → ui → polling) | 수동 검토 + plan 표 |

---

## 6. Success Criteria

### 6.1 Definition of Done

- [ ] 11개 신규 모듈 + 축소된 `common.js` 모두 생성
- [ ] 10개 템플릿 `<script>` 순서 갱신 완료
- [ ] `window.IRMS` API 56개 함수 전수 검증 (정적 grep diff = 0)
- [ ] JS 테스트 3개 PASS
- [ ] 골든패스 수동 스모크 PASS (관리자 + 작업자 + 근태)
- [ ] PDCA 분석 ≥ 90% + 완료 보고서

### 6.2 Quality Criteria

- [ ] 단일 파일 ≤ 200 LOC (`format.js` 150, `ui.js` 150이 최대 예상)
- [ ] 각 모듈 상단에 책임·의존성 docstring (JSDoc 1단락)
- [ ] 콘솔 에러 0건

---

## 7. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| 모듈 로드 순서 누락 → `ReferenceError` | High | High | 분리 전 `window.IRMS` export 56개 grep → 분리 후 전수 매핑 표 작성. 로드 순서를 plan 표와 HTML 주석에 명시 |
| 템플릿 1개 누락 → 일부 페이지에서 IRMS undefined | High | Medium | `grep -l "common.js" templates/*.html` 결과 10개를 체크리스트로 전부 갱신, 수동 스모크에 모든 페이지 포함 |
| 함수 export 누락 → 화면 동작 중 silent fail | High | Medium | 모든 페이지 스크립트(`management.js`, `work.js`, `status.js`, `attendance.js`, `dashboard.js`, `chat.js`, `stock.js`)에서 `IRMS.<symbol>` 호출 추출 → 분리 후 모든 심볼 존재 검증 스크립트 |
| 페이지 로드 시간 +12 스크립트 부담 | Low | High | HTTP/2 다중화로 실측 차이 미미 예상. 임계 시 후속 PDCA에서 단순 concat 빌드 스텝 도입 검토 (별건) |
| 매퍼 함수가 API 호출 함수 안에서만 쓰이는데 모듈 분리로 `window.IRMS._mappers` 노출 필요 | Medium | High | 의도적 노출. `_mappers` 접두사로 내부용 명시 |
| `bindLoginForm`이 `IRMS.login` 등을 사용 → 로드 순서가 핵심 | Medium | High | `ui.js`는 api-users.js 뒤에 로드. plan §3.14 표대로 강제 |
| 폴링 setInterval이 분리 후 중복 시작 | Low | Low | `polling.js`에 `if (window.IRMS._pollingStarted) return;` 가드 |

---

## 8. Architecture Considerations

### 8.1 IIFE 증분 패턴 (선택된 모듈 시스템)

각 모듈은 자체 IIFE에서 `window.IRMS`에 점진 부착:

```javascript
// common/api-users.js
(function () {
  "use strict";
  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;

  IRMS.login = async function (username, password) { ... };
  IRMS.loginManager = async function (username, password) { ... };
  // ...
})();
```

**선택 이유**:
- 번들러 추가 부담 0
- 기존 `<script>` 로드 모델 그대로
- 브라우저 직접 디버깅 가능 (각 파일이 source map 없이도 보임)
- IRMS API 호환 보존

### 8.2 Dependencies

```
core.js          (no deps)
  ↓
mappers.js       (uses core internally)
  ↓
format.js        (no deps, parallel-loadable with mappers)
  ↓
api-users.js     (core + mappers)
api-recipes.js   (core + mappers)
api-stock.js     (core + mappers)
api-chat.js      (core + mappers)
api-spreadsheet.js (core)
api-stats.js     (core)
  ↓
ui.js            (core + format + api-users[bindLoginForm needs login])
audio.js         (core)
  ↓
polling.js       (core + api-stock + ui[notify])
  ↓
common.js        (DOMContentLoaded 부트스트랩)
```

---

## 9. Convention Prerequisites

### 9.1 모듈 명명 컨벤션

| Target | Rule | Example |
|---|---|---|
| 파일명 | `common/<scope>.js` (소문자, 케밥 또는 도메인) | `common/api-users.js` |
| 모듈 IIFE | `(function () { "use strict"; ... })();` 통일 | (위 예시) |
| IRMS 부착 | `const IRMS = window.IRMS = window.IRMS \|\| {};` 첫 줄 | (위 예시) |
| 내부용 심볼 | `IRMS._core`, `IRMS._mappers` (언더스코어 prefix) | (위 예시) |
| 공개 API 심볼 | `IRMS.<funcName>` 직접 부착 | (위 예시) |

### 9.2 docstring 컨벤션

각 모듈 상단:

```javascript
/**
 * <scope> module — <한 줄 설명>.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). See docs/01-plan/features/split-common-js.plan.md.
 *
 * Exports:
 *   IRMS.<func1>, IRMS.<func2>, ...
 *
 * Dependencies (must load before this file):
 *   - core.js (uses IRMS._core.request)
 *   - mappers.js (uses IRMS._mappers.mapUser)
 */
```

---

## 10. Implementation Order

```
Step 1: 분리 전 inventory 작성
  - window.IRMS export 56개 grep → /tmp/irms_api_before.txt
  - 페이지 스크립트(management/work/status/attendance/dashboard/chat/stock/admin_users)의 IRMS.* 호출처 추출

Step 2: 모듈 11개 생성 (의존성 역순)
  - core.js → format.js → mappers.js
  - api-users → api-recipes → api-stock → api-chat → api-spreadsheet → api-stats
  - ui.js → audio.js → polling.js

Step 3: common.js 축소
  - 11개 모듈로 옮긴 코드 모두 제거
  - DOMContentLoaded·nav-toggle·chat-float·Enter-전송 키 처리만 잔존

Step 4: 템플릿 10개 <script> 순서 갱신
  - _base_app.html, attendance*.html, entry*.html, ink_plan.html, login.html, management_login.html, weighing_select.html

Step 5: 검증
  - JS 테스트 3개 (Jest) 통과
  - 분리 후 window.IRMS export grep → /tmp/irms_api_after.txt
  - diff /tmp/irms_api_{before,after}.txt → 0 줄이어야 함
  - 브라우저 콘솔 에러 0건 확인 (관리자 + 작업자 + 근태)
```

---

## 11. PR 전략

- **단일 PR**: Step 1~5 한 PR (중간 상태는 페이지 로드 깨짐 → bisect 곤란)
- **커밋 단위 분리**: Step 2 모듈별로 별도 커밋, Step 3·4·5는 각각 별도 커밋
- **롤백**: `git revert <merge-commit>` 한 번으로 복구 (서버 코드·DB 영향 없음)

---

## 12. Next Steps

1. [ ] 본 Plan 검토·승인
2. [ ] `/pdca design split-common-js` — 모듈별 상세 코드 매핑, IRMS 의존성 그래프
3. [ ] `/pdca do split-common-js` — Step 1~5 실행
4. [ ] `/pdca analyze split-common-js` — gap-detector 검증
5. [ ] `/pdca report split-common-js` + `/pdca archive split-common-js`
6. [ ] Phase 3 (`management.js`) → `/pdca plan split-management-js`
7. [ ] Phase 4 (`work.js`) → `/pdca plan split-work-js`

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-13 | Initial draft (Phase 2 of split-large-files initiative) | ykh00046 |
