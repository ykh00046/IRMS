# split-common-js Design Document

> **Summary**: `static/js/common.js` (1,218 LOC, IIFE) → 책임별 11개 모듈 + 진입 래퍼. `window.IRMS` 56개 공개 API 시그니처 100% 보존. 번들러·ESM·TypeScript 도입 없음.
>
> **Project**: IRMS
> **Version**: post-`e2cf6a7`
> **Author**: ykh00046
> **Date**: 2026-05-13
> **Status**: Draft
> **Planning Doc**: [split-common-js.plan.md](../../01-plan/features/split-common-js.plan.md)
> **Parent Initiative**: [split-large-files (Phase 1 archived)](../../archive/2026-05/split-large-files/)

---

## 1. Overview

### 1.1 Design Goals

1. `common.js` 단일 IIFE(1,218 LOC)를 책임별 12개 파일로 분리 (11 모듈 + 1 부트스트랩 래퍼)
2. 56개 `window.IRMS.*` 공개 API + 7개 사이드 부착 심볼(`bindLoginForm`, `colorLabel`, `initTableScrollHints`, `playChatSound`, `speakText` 등) 100% 보존
3. 모듈 간 명시적 의존성 표현 — `IRMS._core` / `IRMS._mappers` 내부 namespace로 공유
4. 단일 파일 ≤ 200 LOC, 평균 80~150 LOC 목표
5. 10개 템플릿의 `<script>` 로드 순서 일관 갱신

### 1.2 Design Principles

- **순수 분리** — 함수 본문·시그니처·전역 부수효과 시점·인자 전달 방식 0줄 변경
- **window.IRMS 단일 출구** — 외부 페이지 코드는 분리를 인식할 필요 없음
- **명시적 의존성** — 각 모듈 헤더에 "이 파일보다 먼저 로드해야 할 모듈" 명시
- **사이드 이펙트 격리** — 자동 부트스트랩(`bindLogoutButton()`, `setInterval(pollNegativeStock)`, `addEventListener("click", resumeAudioCtx)`)은 각 도메인 모듈 끝에서만 발생
- **DOM 부트스트랩 일원화** — `nav-toggle`, `chat-float`, `Enter-전송`, `initTableScrollHints` 호출은 `common.js`(부트스트랩 래퍼)에 집중

---

## 2. Architecture

### 2.1 Module Dependency Graph

```
                 ┌─────────────┐
                 │  core.js    │  (window.IRMS = {}; IRMS._core = {request,...})
                 └──────┬──────┘
                        │
         ┌──────────────┼──────────────┐
         ▼              ▼              ▼
  ┌─────────────┐ ┌──────────┐ ┌──────────────┐
  │ mappers.js  │ │format.js │ │ api-spreadsheet.js │ (no mapper)
  │(IRMS._mapp.)│ │(IRMS.fmt)│ │ api-stats.js │
  └──────┬──────┘ └────┬─────┘ └──────────────┘
         │             │
         ▼             ▼
  ┌──────────────────────────────┐
  │ api-users.js / api-recipes.js │  (need core + mappers)
  │ api-stock.js / api-chat.js    │
  └──────────────┬───────────────┘
                 │
                 ▼
  ┌──────────────────────────────┐
  │ ui.js  ──────────────────────┤  (uses core+format; bindLoginForm needs api-users.login)
  │ audio.js ────────────────────┤  (uses core only; auto-attaches listeners)
  └──────────────┬───────────────┘
                 │
                 ▼
  ┌──────────────────────────────┐
  │ polling.js                   │  (uses core[request] + format[escapeHtml])
  └──────────────┬───────────────┘
                 │
                 ▼
  ┌──────────────────────────────┐
  │ common.js  (BOOTSTRAP)       │  (DOMContentLoaded; nav/chat-float/enter)
  └──────────────────────────────┘
```

순환 의존 없음. 12개 `<script>` 태그를 위 순서대로 템플릿에 배치.

### 2.2 IIFE 증분 패턴 (스펙)

각 모듈은 동일 보일러플레이트로 시작:

```javascript
/**
 * <module name> — <한 줄 설명>.
 * Split from static/js/common.js (split-common-js, 2026-05).
 * Exports: IRMS.<func1>, IRMS.<func2>, ...
 * Depends on (must load first): core.js, mappers.js, format.js
 */
(function () {
  "use strict";
  const IRMS = window.IRMS = window.IRMS || {};
  // (필요한 의존성 디스트럭처)
  const { request } = IRMS._core;
  const { mapUser } = IRMS._mappers;

  // ─── 모듈 함수 정의 ───
  async function login(username, password) { /* ... */ }

  // ─── IRMS 부착 ───
  IRMS.login = login;
})();
```

**중요 규칙**:
- 첫 줄: `const IRMS = window.IRMS = window.IRMS || {};`
- 디스트럭처: 모듈 상단에서 의존성 읽기 (로드 후 한 번만)
- 부착: 모듈 끝에서 `IRMS.<name> = <localName>` 또는 `Object.assign(IRMS, {...})`

---

## 3. Per-Module Code Mapping

### 3.1 `static/js/common/core.js` (~100 LOC)

| Source Line | Symbol | Type |
|---:|---|---|
| common.js:4-7 | `getCsrfToken()` | private (내부) |
| common.js:9-20 | `safeNextUrl(value, fallback)` | private |
| common.js:22-37 | `detailToText(value)` | private |
| common.js:39-129 | `request(path, options)` | private |

**Public Exposure**:
```javascript
IRMS._core = { request, getCsrfToken, safeNextUrl, detailToText };
```

**Why namespaced as `_core`**: 페이지 코드가 직접 `IRMS.request(...)`를 호출하지 않음 (현재도 IIFE 내부에서만 사용). 분리 후에도 동일하게 내부용.

---

### 3.2 `static/js/common/mappers.js` (~120 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:131-143 | `mapUser(row)` |
| common.js:145-156 | `mapAdminUser(row)` |
| common.js:158-172 | `mapAuditLog(row)` |
| common.js:174-186 | `mapChatRoom(row)` |
| common.js:188-199 | `mapChatMessage(row)` |
| common.js:201-211 | `mapMaterial(row)` |
| common.js:213-232 | `mapRecipe(row)` |
| common.js:234-252 | `mapPreview(result)` |
| common.js:560-577 | `mapWeighingStep(row)` |

**Public Exposure**:
```javascript
IRMS._mappers = { mapUser, mapAdminUser, mapAuditLog, mapChatRoom,
                  mapChatMessage, mapMaterial, mapRecipe, mapPreview,
                  mapWeighingStep };
```

---

### 3.3 `static/js/common/format.js` (~150 LOC)

| Source Line | Symbol | IRMS Public |
|---:|---|:---:|
| common.js:680-689 | `statusLabel(status)` | ✅ |
| common.js:691-693 | `statusClass(status)` | ✅ |
| common.js:695-710 | `formatDateTime(value)` | ✅ |
| common.js:712-724 | `toDateOnly(value)` | ✅ |
| common.js:726-735 | `formatValue(value)` | ✅ |
| common.js:737-745 | `escapeHtml(str)` | ✅ |
| common.js:747-753 | `debounce(fn, delay)` | ✅ |
| common.js:755-762 | `loadPreference(key, fallback)` | ✅ |
| common.js:764-774 | `savePreference(key, value)` | ✅ |
| common.js:776-782 | `clearPreference(key)` | ✅ |
| common.js:1016-1022 | `colorLabel(color)` | ✅ |

**Public Exposure**: 11개 모두 `IRMS.<name>` 직접 부착 (기존 시그니처 보존).

---

### 3.4 `static/js/common/api-users.js` (~110 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:254-263 | `login(username, password)` |
| common.js:265-274 | `loginManager(username, password)` |
| common.js:276-285 | `loginOperator(username, password)` |
| common.js:287-291 | `logout()` |
| common.js:293-296 | `getCurrentUser()` |
| common.js:298-305 | `listUsers()` |
| common.js:307-318 | `createUser(user)` |
| common.js:320-331 | `updateUser(userId, user)` |
| common.js:332-338 | `resetUserPassword(userId, password)` |
| common.js:339-342 | `deleteUser(userId)` |
| common.js:343-355 | `listAuditLogs(filters)` |

**Public Exposure**: 11개 모두 `IRMS.<name>` 직접 부착.

**Dependencies**: `IRMS._core.request`, `IRMS._mappers.mapUser`, `mapAdminUser`, `mapAuditLog`.

---

### 3.5 `static/js/common/api-recipes.js` (~130 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:356-370 | `getRecipeImportNotifications(filters)` |
| common.js:371-415 | `getRecipeProgress(filters)` |
| common.js:416-447 | `getOperatorProgress()` |
| common.js:496-506 | `getRecipes(filters)` |
| common.js:507-514 | `updateRecipeStatus(recipeId, action)` |
| common.js:515-518 | `deleteRecipe(recipeId)` |
| common.js:519-529 | `previewImport(rawText, createdBy)` |
| common.js:530-543 | `importRecipes(rawText, createdBy, revisionOf)` |
| common.js:544-548 | `getProducts()` |
| common.js:549-555 | `getRecipesByProduct(productName, limit)` |
| common.js:556-559 | `getRecipeDetail(recipeId)` |

**Public Exposure**: 11개 모두 `IRMS.<name>` 직접 부착.

**Dependencies**: `IRMS._core.request`, `IRMS._mappers.mapRecipe`, `mapPreview`.

---

### 3.6 `static/js/common/api-stock.js` (~80 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:491-495 | `getMaterials()` |
| common.js:579-593 | `getWeighingQueue(colorGroup)` |
| common.js:594-606 | `completeWeighingStep(recipeId, materialId, recipeItemId)` |
| common.js:607-619 | `undoWeighingStep(recipeId, materialId, recipeItemId)` |
| common.js:620-627 | `completeWeighingRecipe(recipeId)` |
| common.js:628-634 | `resetWeighingRecipe(recipeId)` |

**Public Exposure**: 6개 모두 `IRMS.<name>` 직접 부착.

**Dependencies**: `IRMS._core.request`, `IRMS._mappers.mapMaterial`, `mapWeighingStep`.

---

### 3.7 `static/js/common/api-chat.js` (~50 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:448-455 | `listChatRooms()` |
| common.js:456-471 | `getChatMessages(filters)` |
| common.js:472-486 | `postChatMessage(message)` |
| common.js:487-490 | `clearChatMessages()` |

**Public Exposure**: 4개 모두 `IRMS.<name>` 직접 부착.

**Dependencies**: `IRMS._core.request`, `IRMS._mappers.mapChatRoom`, `mapChatMessage`.

---

### 3.8 `static/js/common/api-spreadsheet.js` (~60 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:855-868 | `ssListProducts()` |
| common.js:870-872 | `ssCreateProduct(body)` |
| common.js:874-876 | `ssUpdateProduct(productId, body)` |
| common.js:878-880 | `ssDeleteProduct(productId)` |
| common.js:882-884 | `ssLoadSheet(productId)` |
| common.js:886-888 | `ssSaveSheet(productId, rows)` |
| common.js:890-892 | `ssAddColumn(productId, body)` |
| common.js:894-896 | `ssDeleteColumn(columnId)` |
| common.js:898-900 | `ssAddRow(productId)` |
| common.js:902-904 | `ssDeleteRow(rowId)` |

**Public Exposure**: 10개 모두 `IRMS.<name>` 직접 부착.

**Dependencies**: `IRMS._core.request` (mapper 의존 없음).

---

### 3.9 `static/js/common/api-stats.js` (~40 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:635-666 | `getStats(filters)` |
| common.js:667-678 | `exportStatsCsv(filters)` |

**Public Exposure**: 2개 모두 `IRMS.<name>` 직접 부착.

**Dependencies**: `IRMS._core.request` (mapper 의존 없음).

---

### 3.10 `static/js/common/ui.js` (~150 LOC)

| Source Line | Symbol | IRMS Public |
|---:|---|:---:|
| common.js:784-796 | `showLoading(el)` | ✅ |
| common.js:798-802 | `hideLoading(el)` | ✅ |
| common.js:804-816 | `btnLoading(btn, loading)` | ✅ |
| common.js:818-832 | `notify(message, type)` | ✅ |
| common.js:834-851 | `bindLogoutButton()` | private (호출만) |
| common.js:981-1012 | `bindLoginForm(opts)` | ✅ |
| common.js:1025-1036 | `initTableScrollHints()` | ✅ |

**Side Effect**: 모듈 끝에서 `bindLogoutButton()` 즉시 호출 (현재 common.js:1109과 동일 시점).

**Public Exposure**:
```javascript
Object.assign(IRMS, { showLoading, hideLoading, btnLoading, notify,
                       bindLoginForm, initTableScrollHints });
bindLogoutButton(); // 즉시 실행
```

**Dependencies**: `IRMS._core.safeNextUrl` (`bindLoginForm`이 사용), `IRMS.logout` (`bindLogoutButton`이 사용 — **api-users.js 후 로드 필수**).

---

### 3.11 `static/js/common/audio.js` (~100 LOC)

| Source Line | Symbol | IRMS Public |
|---:|---|:---:|
| common.js:1041 | `notifSoundCtx` (모듈 변수) | private |
| common.js:1043-1060 | `playChatSound()` | ✅ |
| common.js:1063-1067 | `resumeAudioCtx()` | private |
| common.js:1071-1072 | `speechQueue`, `speechActive` (모듈 변수) | private |
| common.js:1074-1093 | `speakNextQueuedText()` | private |
| common.js:1095-1104 | `speakText(text)` | ✅ |

**Side Effect**: 모듈 끝에서 `document.addEventListener("click", resumeAudioCtx)` + `keydown` (현재 common.js:1068-1069).

**Public Exposure**:
```javascript
IRMS.playChatSound = playChatSound;
IRMS.speakText = speakText;
document.addEventListener("click", resumeAudioCtx);
document.addEventListener("keydown", resumeAudioCtx);
```

**Dependencies**: 없음 (브라우저 native API만 사용).

---

### 3.12 `static/js/common/polling.js` (~60 LOC)

| Source Line | Symbol |
|---:|---|
| common.js:1112-1140 | `pollNegativeStock()` |
| common.js:1141-1144 | 자동 setInterval 등록 |

**Side Effect**: 모듈 끝에서 `if (document.getElementById("negative-stock-banner")) { pollNegativeStock(); setInterval(pollNegativeStock, 60000); }` 자동 실행.

**Public Exposure**: 없음 (자동 부트스트랩, IRMS 부착 없음 — 현재와 동일).

**Dependencies**: `IRMS._core.request` (직접 호출), `IRMS.escapeHtml` (format.js).

**Double-init Guard**:
```javascript
if (window.IRMS._negStockPollingStarted) return;
window.IRMS._negStockPollingStarted = true;
```

---

### 3.13 `static/js/common.js` (축소 부트스트랩, ~80 LOC)

분리 후 잔존 책임:

| Source Line (현재) | 잔존 코드 |
|---:|---|
| common.js:1146-1154 | nav-toggle (모바일 햄버거) |
| common.js:1156-1208 | chat-float 토글·overlay·closeBtn |
| common.js:1210-1221 | Enter-전송 키 핸들러 |
| common.js:1212-1216 | DOMContentLoaded 시 `IRMS.initTableScrollHints()` 호출 |

**Dependencies**: 모든 `common/*.js` 12개 (가장 마지막에 로드).

**Required transformation**:
- 직접 함수 호출 → `IRMS.initTableScrollHints()` 사용
- IIFE 형태 유지 (외부 노출 변수 없음)

---

## 4. window.IRMS Public API Inventory (58 + 5 in scope · +1 extra-scope)

### 4.1 기존 58개 (line 906-965)

> **2026-05-13 정정**: 초기 plan/design은 56으로 적었으나 실측 결과 58개. `clearPreference`는 Format 도메인이며, `getMaterials`는 stock/material 데이터이므로 Stock+Weighing 도메인으로 분류 (api-stock.js로 이동).

| Domain | Count | Functions |
|---|---:|---|
| Auth/Users | 11 | login, loginManager, loginOperator, logout, getCurrentUser, listUsers, createUser, updateUser, resetUserPassword, deleteUser, listAuditLogs |
| Chat | 4 | listChatRooms, getChatMessages, postChatMessage, clearChatMessages |
| Recipes | 11 | getRecipeImportNotifications, getRecipeProgress, getOperatorProgress, getRecipes, updateRecipeStatus, deleteRecipe, previewImport, importRecipes, getProducts, getRecipesByProduct, getRecipeDetail |
| Stock+Weighing | 6 | **getMaterials**, getWeighingQueue, completeWeighingStep, undoWeighingStep, completeWeighingRecipe, resetWeighingRecipe |
| Stats | 2 | getStats, exportStatsCsv |
| Spreadsheet | 10 | ssListProducts, ssCreateProduct, ssUpdateProduct, ssDeleteProduct, ssLoadSheet, ssSaveSheet, ssAddColumn, ssDeleteColumn, ssAddRow, ssDeleteRow |
| Format | 10 | statusLabel, statusClass, formatDateTime, toDateOnly, formatValue, escapeHtml, debounce, loadPreference, savePreference, clearPreference |
| UI | 4 | notify, showLoading, hideLoading, btnLoading |

**Total**: 11+4+11+6+2+10+10+4 = **58** ✓ (검증: `sed -n '906,965p' static/js/common.js | grep -cE "^\s+[a-zA-Z][a-zA-Z0-9]*,$"` → 58)

### 4.2 사이드 부착 5개 (라인 1014-1107)

이 5개는 `window.IRMS = {...}` 블록 외부에서 추가된 함수. Phase 2 분리 대상에 포함.

| Symbol | Source Line | Target Module |
|---|---:|---|
| `IRMS.bindLoginForm` | 1014 | ui.js |
| `IRMS.colorLabel` | 1023 | format.js |
| `IRMS.initTableScrollHints` | 1038 | ui.js |
| `IRMS.playChatSound` | 1106 | audio.js |
| `IRMS.speakText` | 1107 | audio.js |

**Total IRMS 공개 표면 (Phase 2 분리 대상)**: 58 + 5 = **63개**

### 4.3 본 PDCA 범위 외 IRMS 표면 (참고용)

`common.js` 외부에서 `window.IRMS`에 추가 부착되는 심볼 — 본 분리 작업의 영향을 받지 않으나, 로드 순서 분석 시 인지 필요.

| Symbol | Source File:Line | 사용처 | 비고 |
|---|---|---|---|
| `IRMS.createChat` | `static/js/chat.js:352-353` | `status.js:49`, `work.js:158`, `management.js:319` | `chat.js`는 `common/*.js` 13개 뒤, 그러나 `status.js`/`work.js`/`management.js` 앞에 로드되어야 함 (현재도 동일 — 변경 없음) |

### 4.4 신규 내부 namespace

| Namespace | Members | 사용처 |
|---|---|---|
| `IRMS._core` | request, getCsrfToken, safeNextUrl, detailToText | 모든 api-* + ui.bindLoginForm + polling |
| `IRMS._mappers` | mapUser, mapAdminUser, mapAuditLog, mapChatRoom, mapChatMessage, mapMaterial, mapRecipe, mapPreview, mapWeighingStep | api-users, api-recipes, api-stock, api-chat |
| `IRMS._negStockPollingStarted` | boolean | polling.js 자체 가드 |

페이지 코드가 `_core` / `_mappers`에 직접 의존하지 않도록 언더스코어 prefix.

---

## 5. Template `<script>` Loading Strategy

### 5.1 영향 받는 템플릿 10개

`grep -l "common.js" templates/*.html` 결과:

| Template | 현재 줄 |
|---|---|
| `templates/_base_app.html` | 68 |
| `templates/attendance.html` | 195 |
| `templates/attendance_change_password.html` | 70 |
| `templates/attendance_login.html` | 65 |
| `templates/entry.html` | 60 |
| `templates/entry_test.html` | 60 |
| `templates/ink_plan.html` | 153 |
| `templates/login.html` | 57 |
| `templates/management_login.html` | 70 |
| `templates/weighing_select.html` | 62 |

### 5.2 새 로드 블록 (전 템플릿 동일)

```html
<!-- IRMS common modules: load order matters (core → mappers/format → APIs → ui/audio → polling → bootstrap) -->
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
<script src="/static/js/common.js"></script>
```

기존 한 줄(`<script src="/static/js/common.js"></script>`)을 위 13줄로 교체.

#### 5.2.1 ⚠️ 페이지별 JS 로드 순서 강제 룰

**페이지별 스크립트(`work.js`, `management.js`, `status.js`, `chat.js`, `dashboard.js`, `attendance.js`, `admin_users.js`, `stock.js`, `insight.js`, `weighing_select.js`, `attendance_login.js`, `attendance_change_password.js`, `management_login.js`, `login.js`)는 위 13줄 공통 블록의 *뒤*에 로드해야 한다.**

근거:
- `static/js/work.js:103`이 IIFE 최상단(`var colorLabel = IRMS.colorLabel;`)에서 `IRMS.colorLabel`을 즉시 읽음. `format.js` 로드 전에 `work.js`가 실행되면 `colorLabel === undefined`로 캐시되어 화면 깨짐
- 동일 패턴이 다른 페이지 스크립트에도 존재할 수 있음 (예: `chat.js:352-353`이 `IRMS.notify` / `IRMS.formatDateTime` 의존)

검증 명령:
```bash
grep -nE "var\s+\w+\s*=\s*IRMS\." static/js/*.js
grep -nE "const\s+\w+\s*=\s*IRMS\." static/js/*.js
grep -nE "let\s+\w+\s*=\s*IRMS\." static/js/*.js
```

위 grep으로 IIFE 최상단에서 IRMS를 읽는 페이지 스크립트를 모두 찾아내, 분리 후 콘솔 에러가 발생하지 않는지 확인한다.

### 5.3 Jinja2 partial 도입 검토 → **미채택**

13줄 반복을 `{% include "_common_scripts.html" %}` 한 줄로 줄일 수도 있으나:
- Jinja partial 변경 시 캐시 무효화 추적이 어려움
- 현재 IRMS는 Jinja includes 패턴 사용 빈도 낮음
- 13줄 명시 노출이 디버깅·로드 순서 가독성에 더 좋음

→ 본 PDCA에서는 **각 템플릿에 13줄 명시**. 향후 필요 시 별도 PDCA에서 partial 도입 검토.

---

## 6. Side-Effect Initialization Order

분리 후 사이드 이펙트 발생 시점:

| Module | 사이드 이펙트 | 발생 시점 |
|---|---|---|
| `core.js` | `window.IRMS = window.IRMS \|\| {}; IRMS._core = {...}` | 스크립트 파싱 즉시 |
| `mappers.js` | `IRMS._mappers = {...}` | 즉시 |
| `format.js` | `Object.assign(IRMS, {...11개})` | 즉시 |
| `api-*.js` | `Object.assign(IRMS, {...})` | 즉시 |
| `ui.js` | export + `bindLogoutButton()` | 즉시 (DOMContentLoaded 전이라도 OK — `getElementById` 는 null 반환하면 함수가 no-op) |
| `audio.js` | export + `addEventListener("click"/"keydown", resumeAudioCtx)` | 즉시 |
| `polling.js` | `if (banner) { poll(); setInterval(poll, 60000); }` | 즉시 (banner 없으면 no-op) |
| `common.js` | nav-toggle/chat-float/Enter handler 바인딩, `initTableScrollHints()` (DOMContentLoaded 시) | 즉시 또는 DOMContentLoaded |

→ 모든 사이드 이펙트가 **현재와 동일 순서**로 발생.

---

## 7. Error Handling

분리에서 새로 추가되는 에러 시나리오는 **로드 순서 위반에 따른 `TypeError: Cannot read properties of undefined`** 한 가지뿐:

| 시나리오 | 결과 | 방어 |
|---|---|---|
| `api-users.js`가 `core.js` 전에 로드 | `IRMS._core is undefined` → 즉시 throw | template 로드 순서 표 (§5.2) 준수 + 매뉴얼 스모크에서 콘솔 0건 확인 |
| `ui.js`가 `api-users.js` 전에 로드 | `bindLogoutButton`이 `logout` 호출 시 fail | 위와 동일 |
| `polling.js`가 `format.js` 전에 로드 | `escapeHtml is not defined` → 폴링 실패 (silent) | 위와 동일 |
| **페이지별 스크립트가 13개 공통 모듈 전에 로드** | `work.js:103`의 `var colorLabel = IRMS.colorLabel;`이 `undefined` 캐시 → 색상 라벨 화면 깨짐 | **§5.2.1 룰 적용**: 모든 페이지 JS는 13개 공통 블록 *뒤*에 위치. PR review 시 `<script>` 순서 명시적 확인 |

기존 IIFE의 모든 try/catch·notify 패턴은 그대로 보존.

---

## 8. Test Plan

### 8.1 자동 검증

| Type | Tool | Command |
|---|---|---|
| Lint (load 순서) | grep diff | `grep -E '^\s*<script.*common' templates/*.html` 비교 |
| 정적 export | grep | `grep -hoE 'IRMS\.\w+\s*=' static/js/common/*.js \| sort -u` → 56+5 = 61개 일치 |
| JS 단위 테스트 | jest | `tests/js/*.test.js` 3개 |
| Python 회귀 | pytest | 32개 통과 (영향 없음 검증용) |

### 8.2 수동 스모크 체크리스트

각 페이지에서 DevTools Console 기준 **에러 0건** 확인:

**관리자 페이지** (`/management`)
- [ ] 페이지 로드 — 콘솔 에러 0건
- [ ] 레시피 검색·필터 동작
- [ ] 레시피 상세 모달 (재료 목록)
- [ ] 레시피 이력 모달 (`v1`, `v2`, …)
- [ ] 버전 비교
- [ ] 엑셀 import preview → register
- [ ] 재고 페이지 (입고·조정·폐기·임계치)
- [ ] 통계 조회 + CSV 다운로드
- [ ] 진행률 카드
- [ ] 채팅 toast 알림 + 채팅 sound (audio.js)
- [ ] 모바일 nav-toggle (chrome devtools 모바일 모드)
- [ ] chat-float 토글
- [ ] 로그아웃 버튼

**작업자 페이지** (`/work`)
- [ ] 페이지 로드 — 콘솔 에러 0건
- [ ] 계량 큐 표시
- [ ] 계량 시작 → 완료 → 다음 단계
- [ ] 되돌리기
- [ ] 재고 부족 배너 (polling.js)

**근태 페이지** (`/attendance`)
- [ ] 페이지 로드 — 콘솔 에러 0건
- [ ] 로그인 → 조회
- [ ] 비밀번호 변경 (`bindLoginForm`)

**로그인 페이지** (`/management/login`, `/login`)
- [ ] 잘못된 비번 → 빨간 에러 메시지
- [ ] 정상 로그인 → 리다이렉트

---

## 9. Clean Architecture Alignment

### 9.1 Layer Structure (Frontend)

| Layer | Responsibility | Location | Phase 2 변경 |
|---|---|---|---|
| **Bootstrap** | DOM init, 자동 부트스트랩 | `common.js`, 페이지별 `<page>.js` | `common.js` 축소 |
| **Adapter (UI)** | DOM 조작 헬퍼 | `common/ui.js` | **신규** |
| **Service (API)** | HTTP 클라이언트 | `common/core.js`, `common/api-*.js` | **신규** |
| **Domain (Mappers)** | API 응답 → 도메인 객체 | `common/mappers.js` | **신규** |
| **Util (Format)** | 순수 함수 | `common/format.js` | **신규** |
| **Side-effect** | 오디오·폴링·전역 리스너 | `common/audio.js`, `common/polling.js` | **신규** |

### 9.2 Dependency Rules

```
Bootstrap → Adapter, Util, Side-effect
Adapter → Service, Util
Service → Domain, Core
Domain → (none)
Util → (none)
Side-effect → Service, Util
```

순환 없음. Phase 1(Python)의 라우터 → 서비스 → DB 패턴과 동일 정신.

---

## 10. Coding Convention

### 10.1 모듈 헤더 템플릿

```javascript
/**
 * <module name> module — <한 줄 설명>.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). See docs/01-plan/features/split-common-js.plan.md.
 *
 * Exports (window.IRMS.*):
 *   <func1>, <func2>, ...
 *
 * Internal namespace (IRMS._<name>):
 *   <namespaceMembers>
 *
 * Side effects (executed on script parse):
 *   <description or "none">
 *
 * Dependencies (must load before this script tag):
 *   - common/core.js (uses IRMS._core.request)
 *   - common/mappers.js (uses IRMS._mappers.mapUser)
 */
(function () {
  "use strict";
  const IRMS = window.IRMS = window.IRMS || {};
  // ...
})();
```

### 10.2 Naming

| Target | Rule | Example |
|---|---|---|
| Module file | `common/<scope>.js` (소문자, 케밥 또는 도메인) | `common/api-users.js` |
| Public symbol | `IRMS.<name>` 직접 부착 (camelCase 유지) | `IRMS.login` |
| Internal namespace | `IRMS._<name>` (언더스코어 prefix) | `IRMS._core` |
| Module-local 변수 | `const`/`let` 모듈 스코프 | `notifSoundCtx`, `speechQueue` |

---

## 11. Implementation Guide

### 11.1 작업 순서 (의존성 역순 — 가장 깊은 모듈부터)

1. **`common/core.js`** 생성 — `request`, `getCsrfToken`, `safeNextUrl`, `detailToText` 이동, `IRMS._core` 부착
2. **`common/mappers.js`** + **`common/format.js`** 병렬 생성
3. **`common/api-spreadsheet.js`** + **`common/api-stats.js`** 병렬 (mapper 의존 없음)
4. **`common/api-users.js`**, **`common/api-recipes.js`**, **`common/api-stock.js`**, **`common/api-chat.js`** 병렬 생성
5. **`common/ui.js`** — `bindLogoutButton()` 즉시 실행 시점 보존
6. **`common/audio.js`** — `addEventListener("click"/"keydown", resumeAudioCtx)` 보존
7. **`common/polling.js`** — `if (banner) { ... }` 가드 보존
8. **`common.js`** 축소 — DOMContentLoaded 부트스트랩만 잔존
9. **템플릿 10개** — 13줄 `<script>` 블록으로 일괄 교체
10. **검증** — JS 테스트, 정적 grep, 수동 스모크

### 11.2 단위 검증 체크포인트

각 단계 후 브라우저에서 `/management` 로드:
- 콘솔 에러 0건이면 다음 단계
- 에러 발생 시 직전 단계 롤백 → 의존성 표 재확인

### 11.3 PR 전략

- **단일 PR**: 1~10단계 한 PR (중간 상태는 페이지 로드 깨짐)
- **커밋 단위 분리**:
  - C1: core.js + mappers.js + format.js
  - C2: api-spreadsheet/api-stats
  - C3: api-users/api-recipes/api-stock/api-chat
  - C4: ui.js + audio.js + polling.js
  - C5: 축소된 common.js + 템플릿 10개
  - C6: 검증 결과 메모

### 11.4 롤백 시나리오

`git revert <merge-commit>` 한 번. 서버·DB 영향 없음.

---

## 12. Future Scope (Phase 3, 4)

본 design은 Phase 2 한정. 후속:

| Phase | 대상 | 별도 PDCA |
|---|---|---|
| 3 | `static/js/management.js` (1,006 LOC) | `/pdca plan split-management-js` |
| 4 | `static/js/work.js` (760 LOC) | `/pdca plan split-work-js` |

Phase 2 머지·안정화 후 진행.

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-13 | Initial draft — Phase 2 (common.js 분리) 상세 설계 | ykh00046 |
