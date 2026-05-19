# split-management-js Design Document

> **Summary**: `static/js/management.js` (1,006 LOC, 단일 `DOMContentLoaded` 페이지 컨트롤러) → 책임별 5개 팩토리 모듈 + 얇은 컨트롤러. 공유 가변 상태는 단일 `ctx` 객체로 참조 공유. `/management` 페이지 동작 100% 보존. 번들러·ESM·TypeScript 도입 없음.
>
> **Project**: IRMS
> **Version**: post-`c9e5d86`
> **Author**: ykh00046
> **Date**: 2026-05-19
> **Status**: Draft
> **Planning Doc**: [split-management-js.plan.md](../../01-plan/features/split-management-js.plan.md)
> **Parent Initiative**: split-large-files — [Phase 1](../../archive/2026-05/split-large-files/), [Phase 2](../../archive/2026-05/split-common-js/)

---

## 1. Overview

### 1.1 Design Goals

1. `management.js` 단일 IIFE(1,006 LOC)를 책임별 6개 파일로 분리 (5개 팩토리 모듈 + 1개 컨트롤러)
2. `/management` 페이지 4개 탭(Import / 이력 / Lookup / Chat) 동작 100% 보존
3. 함수가 공유하는 **10개 가변 상태**를 **단일 `ctx.state` 객체**로 안전하게 공유 (+ 불변 상수 2개 → `ctx.const`, `chatState`는 컨트롤러 보유 → 공유 심볼 합계 13개)
4. 모듈 간 상호 호출을 `ctx` 콜백/핸들로만 — 모듈은 서로 직접 참조하지 않음
5. 단일 모듈 ≤ 250 LOC
6. `templates/management.html` `<script>` 블록 갱신 (1개 템플릿)

### 1.2 Design Principles

- **순수 분리** — 함수 본문·동작·부수효과 시점 0줄 변경. 코드 이동과 `ctx` 경유 참조 치환만
- **단일 상태 객체** — 모든 모듈이 `ctx.state`의 동일 참조를 공유. 원시값을 모듈 스코프로 캡처 금지 (§4.2)
- **lazy `ctx` 참조** — 모듈은 `ctx.spreadsheet`/`ctx.onDirty` 등을 **호출 시점에** 읽음. 팩토리 실행 시점에 캡처 금지 (2단계 와이어링이 성립하는 근거, §5)
- **컨트롤러 단일 조립 지점** — `ctx` 생성·모듈 인스턴스화·교차 와이어링·이벤트 바인딩은 `management.js` 한 곳에만
- **기존 패턴 계승** — `IRMS.createChat({...})`(`chat.js`)이 이미 쓰는 팩토리 패턴을 management 네임스페이스로 확장

### 1.3 Phase 2와의 차이

| 항목 | Phase 2 (common.js) | Phase 3 (management.js) |
|------|---------------------|--------------------------|
| 대상 성격 | 독립 함수 라이브러리 | 단일 페이지 컨트롤러 |
| 공개 계약 | `window.IRMS` 63개 API 시그니처 | 없음 — "`/management` 동작 동일"만 |
| 분리 패턴 | IIFE 증분 부착 (`IRMS.x = ...`) | 팩토리 + 공유 `ctx` |
| 상태 공유 | 거의 없음 (순수 함수 위주) | 10개 가변 상태 교차 공유 |
| 검증 수단 | 정적 grep diff = 0 | 수동 스모크 (정적 검증 불가) |
| 영향 템플릿 | 10개 | 1개 (`management.html`) |

> **수치 정정 (2026-05-19)**: Plan 문서는 "31개 함수 / 13개 가변 상태"로 추정했으나, 실측 결과 **함수 33개**(모듈 30 + 컨트롤러 잔존 3: `loadMaterials`/`refreshChatPanel`/`startChatPolling`), **가변 상태 10개**(+ 불변 상수 2개 + `chatState` = 공유 심볼 13개)다. 본 설계의 수치가 정확하며 구현 기준이다.

---

## 2. Architecture

### 2.1 Module Dependency Graph

```
                    ┌──────────────────┐
                    │   management.js  │  컨트롤러 (조립 지점)
                    │  ctx 생성·와이어링 │
                    └────────┬─────────┘
                             │ creates / injects ctx
        ┌────────────┬───────┼────────────┬──────────────┐
        ▼            ▼       ▼            ▼              ▼
 ┌────────────┐ ┌─────────┐ ┌──────────┐ ┌───────────┐ ┌──────────────┐
 │ spreadsheet│ │ import- │ │ recipe-  │ │ recipe-   │ │ version-     │
 │ -editor    │ │ validate│ │ history  │ │ lookup    │ │ compare      │
 └─────┬──────┘ └────┬────┘ └────┬─────┘ └─────┬─────┘ └──────┬───────┘
       │             │           │             │              │
       │ ctx.onDirty │ ctx.       │ ctx.onClone │ ctx.spread.  │ ctx.onClone
       │ (→import)   │ spreadsheet│ (→lookup)   │ ctx.import-  │ (→lookup)
       │             │            │             │ Validate     │
       └─────────────┴────────────┴── ctx.state (공유 단일 객체) ──┘
```

순환 없음. 모듈 간 직접 참조 0 — 모든 교차 호출은 `ctx`의 핸들/콜백 경유. 컨트롤러가 유일하게 모든 모듈을 안다.

### 2.2 Factory + Shared Context 패턴 (스펙)

각 모듈은 동일 보일러플레이트:

```javascript
/**
 * <module> module — <한 줄 설명>.
 * Split from static/js/management.js (split-management-js, 2026-05).
 *
 * Factory: IRMS.management.create<Name>(ctx)
 * Returns: { <handle1>, <handle2>, ... }
 * ctx deps: ctx.dom.<...>, ctx.state.<...>, ctx.<moduleHandle>, ctx.<callback>
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.management = NS.management || {};

  NS.management.createImportValidate = function (ctx) {
    const { dom, state } = ctx;

    function markPreviewStale() {
      if (state.suppressDirtyTracking || !state.currentPreview || state.previewIsStale) return;
      state.previewIsStale = true;
      // ...
    }
    // ...
    return { markPreviewStale, handlePreview, handleRegister, handleClear,
             renderValidationMeta, renderIssues, syncRegisterState };
  };
})();
```

**중요 규칙**:
- 팩토리는 `ctx`를 받아 핸들 객체를 반환할 뿐, **즉시 실행(이벤트 바인딩·DOM 조작) 안 함** → 5개 모듈 간 `<script>` 로드 순서 무관
- 상태는 항상 `state.<key>` (= `ctx.state.<key>`)로 읽고 씀. `const x = state.foo` 같은 원시값 캡처 금지
- 다른 모듈 호출은 `ctx.spreadsheet.destroySpreadsheet()` / `ctx.onDirty()` 처럼 **호출 시점에** `ctx`에서 꺼냄

---

## 3. The `ctx` Object (핵심 설계 산출물)

컨트롤러가 만들어 모든 모듈에 전달하는 단일 공유 컨텍스트.

### 3.1 `ctx.dom` — DOM 참조 (관리 외 변경 없음, 현재 L2-68과 동일)

| 그룹 | 멤버 |
|------|------|
| shell/import | `shell`, `spreadsheetContainer`, `rawInput`, `previewBtn`, `registerBtn`, `clearBtn`, `previewMeta`, `errorList`, `warningList` |
| history | `historyBody`, `historyStatus`, `historySearch`, `historyFrom`, `historyTo`, `historySummary`, `historyResetBtn` |
| chat | `roomMeta`, `roomTabs`, `chatMessages`, `chatForm`, `chatStageGroup`, `chatStage`, `chatInput`, `chatSend` |
| lookup | `lookupProduct`, `productList`, `lookupBtn`, `lookupResult`, `lookupActions`, `lookupSelectedLabel`, `lookupCopyBtn`, `lookupCloneBtn`, `lookupHistoryBtn` |
| version 모달 | `historyModal`, `historyModalClose`, `historyModalTitle`, `historyModalSubtitle`, `versionHistoryBody`, `historyCompareBtn`, `compareModal`, `compareModalClose`, `compareModalTitle`, `compareThead`, `compareTbody` |
| tabs | `tabBtns`(NodeList), `tabPanels`(NodeList) |

### 3.2 `ctx.state` — 가변 공유 상태 (단일 객체)

| Key | 초기값 | Owner 모듈 | 교차 쓰기 |
|-----|--------|-----------|-----------|
| `currentPreview` | `null` | import-validate | recipe-lookup(`handleLookupClone` L935) |
| `materials` | `[]` | (controller `loadMaterials`) | — (현재 코드에서 set 후 read 없음 — 그대로 보존) |
| `sheet` | `null` | spreadsheet-editor | — |
| `confirmedRawText` | `""` | import-validate | recipe-lookup(`handleLookupClone` L937) |
| `previewIsStale` | `false` | import-validate | recipe-lookup(`handleLookupClone` L936) |
| `suppressDirtyTracking` | `false` | spreadsheet-editor | recipe-lookup(`handleLookupClone` L889/931) |
| `spreadsheetFallbackNotified` | `false` | spreadsheet-editor | — |
| `currentHistoryChain` | `null` | version-compare | — (`handleLookupHistory` L639에서 set 후 read 없음 — `materials`와 동일한 dead state, 순수 분리 원칙상 그대로 보존) |
| `selectedRecipeId` | `null` | recipe-lookup | recipe-history(L445), version-compare(L683) |
| `pendingRevisionOf` | `null` | import-validate | recipe-lookup(`handleLookupClone` L934) |

> `chatState`(L75-82)는 `IRMS.createChat`에 그대로 넘기므로 `ctx.state`가 아니라 컨트롤러가 별도 보유.

### 3.3 `ctx.const` — 불변 상수

| Key | 내용 | 사용 모듈 |
|-----|------|-----------|
| `stageLabels` | `{registered, in_progress, completed}` (L84-88) | chat (controller가 createChat에 전달) |
| `preferenceKeys` | `{status, search, from, to}` (L90-95) | recipe-history |

### 3.4 `ctx`의 모듈 핸들·콜백 (2단계 와이어링으로 주입)

| Key | 값 | 주입 시점 |
|-----|-----|----------|
| `ctx.spreadsheet` | `createSpreadsheetEditor(ctx)` 반환 객체 | 모듈1 생성 직후 |
| `ctx.importValidate` | `createImportValidate(ctx)` 반환 객체 | 모듈2 생성 직후 |
| `ctx.onDirty` | `ctx.importValidate.markPreviewStale` | 모듈2 생성 직후 |
| `ctx.onClone` | `ctx.recipeLookup.handleLookupClone` | 모듈4 생성 직후 |
| `ctx.copyToClipboard` | `ctx.recipeLookup.copyToClipboard` | 모듈4 생성 직후 (recipe-history의 `.history-copy-btn` L433-441이 사용) |
| `ctx.switchToImportTab` | 컨트롤러가 정의한 탭 전환 함수 | `ctx` 생성 시 |

---

## 3.5 State Read/Write Matrix

> Plan Risk #1(공유 상태 캡처 vs 참조, High/High) 직접 대응. 각 상태 키를 읽고/쓰는 함수와 소속 모듈. 모듈 경계를 넘는 쓰기가 있는 상태(`currentPreview`, `confirmedRawText`, `previewIsStale`, `suppressDirtyTracking`, `selectedRecipeId`, `pendingRevisionOf`)는 반드시 `ctx.state.<key>`로 접근해야 한다 — 원시값 캡처 시 모듈 간 불일치 발생.

| State Key | Write (함수 / 모듈) | Read (함수 / 모듈) |
|-----------|---------------------|--------------------|
| `currentPreview` | `handlePreview`·`handleClear` (import-validate), `handleLookupClone` (lookup) | `syncRegisterState`·`markPreviewStale`·`handleRegister` (import-validate) |
| `materials` | `loadMaterials` (controller) | — (reader 없음) |
| `sheet` | `destroySpreadsheet`·`getActiveWorksheet`·`initSpreadsheet` (spreadsheet) | `destroySpreadsheet`·`getActiveWorksheet` (spreadsheet), `handleClear` (import-validate) |
| `confirmedRawText` | `handlePreview`·`handleClear` (import-validate), `handleLookupClone` (lookup) | `syncRegisterState`·`handleRegister` (import-validate) |
| `previewIsStale` | `markPreviewStale`·`handlePreview`·`handleClear` (import-validate), `handleLookupClone` (lookup) | `syncRegisterState`·`markPreviewStale`·`renderValidationMeta`·`handleRegister` (import-validate) |
| `suppressDirtyTracking` | `initSpreadsheet` (spreadsheet), `handleLookupClone` (lookup) | `markPreviewStale` (import-validate), `initSpreadsheet` (spreadsheet) |
| `spreadsheetFallbackNotified` | `initSpreadsheet` (spreadsheet) | `initSpreadsheet` (spreadsheet) |
| `currentHistoryChain` | `handleLookupHistory` (version-compare) | — (reader 없음, dead state) |
| `selectedRecipeId` | `setLookupSelection`·`handleLookupClone` (lookup), `renderHistory` clone 버튼 (history), `renderHistoryModal` clone 버튼 (version-compare) | `handleLookupHistory` (version-compare), `handleLookupCopy`·`handleLookupClone` (lookup) |
| `pendingRevisionOf` | `handleClear` (import-validate), `handleLookupClone` (lookup) | `handleRegister` (import-validate) |

**관찰**: `suppressDirtyTracking`은 2개 모듈(spreadsheet·lookup)이 쓰고 또 다른 곳에서 읽는 최고 위험 상태 → `ctx.state.suppressDirtyTracking` 직접 참조 필수. `materials`/`currentHistoryChain`은 reader가 없는 dead state로, 순수 분리 원칙상 제거하지 않고 그대로 보존(향후 별도 정리 PDCA 후보).

---

## 4. Per-Module Code Mapping

### 4.1 `static/js/management/spreadsheet-editor.js` (~210 LOC)

| Source Line | Symbol | Returned Handle |
|---:|---|:---:|
| 97-105 | `getSpreadsheetFactory()` | ✅ |
| 107-115 | `setRawInputMode(enabled)` | ✅ |
| 185-201 | `destroySpreadsheet()` | ✅ |
| 203-216 | `getActiveWorksheet()` | ✅ |
| 219-274 | `initSpreadsheet()` | ✅ |
| 277-313 | `getSpreadsheetDataAsText()` | ✅ |

**ctx deps**: `dom.spreadsheetContainer`, `dom.rawInput`; `state.sheet`, `state.suppressDirtyTracking`, `state.spreadsheetFallbackNotified`; `ctx.onDirty` (jspreadsheet `onchange`/`onafterchanges`/`onpaste` 콜백에서 호출 — L256-264).

**핵심 변환**: `initSpreadsheet`의 `onchange: () => markPreviewStale()` → `onchange: () => ctx.onDirty()`. 콜백은 사용자 입력 시점에 실행되므로 `ctx.onDirty`가 그때 세팅돼 있으면 됨 (lazy 참조).

**Factory**: `IRMS.management.createSpreadsheetEditor(ctx)` → `{ getSpreadsheetFactory, setRawInputMode, destroySpreadsheet, getActiveWorksheet, initSpreadsheet, getSpreadsheetDataAsText }`

---

### 4.2 `static/js/management/import-validate.js` (~135 LOC)

| Source Line | Symbol | Returned Handle |
|---:|---|:---:|
| 117-125 | `syncRegisterState()` | ✅ |
| 175-183 | `markPreviewStale()` | ✅ |
| 329-341 | `renderIssues(list, target, emptyText)` | ✅ |
| 343-354 | `renderValidationMeta(result)` | ✅ |
| 488-515 | `handlePreview()` | ✅ |
| 517-546 | `handleRegister()` | ✅ |
| 548-563 | `handleClear()` | ✅ |

**ctx deps**: `dom.previewBtn/registerBtn/previewMeta/errorList/warningList`; `state.currentPreview/confirmedRawText/previewIsStale/pendingRevisionOf/suppressDirtyTracking`; `ctx.spreadsheet.getSpreadsheetDataAsText` (`handlePreview` L489), `ctx.spreadsheet.initSpreadsheet` (`handleClear` L553).

**핵심 변환**: `markPreviewStale`이 `renderValidationMeta`(동일 모듈) 호출 — 모듈 내부 참조. `handlePreview`/`handleClear`의 `getSpreadsheetDataAsText`/`initSpreadsheet` → `ctx.spreadsheet.*`.

**Factory**: `IRMS.management.createImportValidate(ctx)` → `{ syncRegisterState, markPreviewStale, renderIssues, renderValidationMeta, handlePreview, handleRegister, handleClear }`

---

### 4.3 `static/js/management/recipe-history.js` (~185 LOC)

| Source Line | Symbol | Returned Handle |
|---:|---|:---:|
| 128-133 | `persistHistoryFilters()` | ✅ |
| 135-153 | `updateHistorySummary()` | ✅ |
| 155-160 | `restoreHistoryFilters()` | ✅ |
| 162-173 | `resetHistoryFilters()` | ✅ |
| 356-486 | `renderHistory()` | ✅ |

**ctx deps**: `dom.historyBody/historyStatus/historySearch/historyFrom/historyTo/historySummary`; `const.preferenceKeys`; `state.selectedRecipeId` (clone 버튼 L445); `ctx.onClone` (`renderHistory` 내 동적 생성된 `.history-clone-btn` 클릭 핸들러 L443-447).

**핵심 변환**: `renderHistory`의 아코디언 상세행 clone 버튼 `handleLookupClone()` → `ctx.onClone()`. `resetHistoryFilters`는 동일 모듈 `renderHistory` 호출.

**Factory**: `IRMS.management.createRecipeHistory(ctx)` → `{ persistHistoryFilters, updateHistorySummary, restoreHistoryFilters, resetHistoryFilters, renderHistory }`

---

### 4.4 `static/js/management/recipe-lookup.js` (~200 LOC)

| Source Line | Symbol | Returned Handle |
|---:|---|:---:|
| 605-616 | `loadProducts()` | ✅ |
| 618-631 | `setLookupSelection(recipeId)` | ✅ |
| 762-846 | `handleLookup()` | ✅ |
| 848-862 | `copyToClipboard(text)` | ✅ |
| 864-873 | `handleLookupCopy()` | ✅ |
| 875-947 | `handleLookupClone()` | ✅ |

**ctx deps**:
- `dom.lookupProduct/productList/lookupBtn/lookupResult/lookupActions/lookupSelectedLabel/lookupCopyBtn/lookupCloneBtn/lookupHistoryBtn`
- `dom.errorList/warningList` (`handleLookupClone` L939-940)
- **`dom.spreadsheetContainer/rawInput`** — `handleLookupClone`이 인라인으로 jspreadsheet를 직접 생성·조작 (L893, L903, L928-929). spreadsheet-editor 모듈 핸들로는 커버되지 않는 직접 DOM 접근
- `state.selectedRecipeId/pendingRevisionOf/currentPreview/previewIsStale/confirmedRawText/suppressDirtyTracking`
- `ctx.spreadsheet.{destroySpreadsheet,getSpreadsheetFactory,setRawInputMode,getActiveWorksheet}`
- `ctx.importValidate.{renderValidationMeta,renderIssues,syncRegisterState}`
- `ctx.onDirty` — `handleLookupClone`이 인라인 생성하는 jspreadsheet의 `onchange/onafterchanges/onpaste` 콜백 (L916-918)
- `ctx.switchToImportTab` (L882-886)

> `handleLookupClone`은 가장 결합도 높은 함수 — 스프레드시트(핸들 + 직접 DOM)·검증·탭 3개 영역을 건드림. `ctx` 경유로 모두 해소.

**핵심 변환**:
1. `handleLookupClone`의 인라인 jspreadsheet 콜백 `onchange: () => markPreviewStale()` (L916-918) → `onchange: () => ctx.onDirty()`. (`initSpreadsheet`의 동일 패턴 변환과 일관)
2. L433-441의 history-copy-btn은 `copyToClipboard` 호출 — 그러나 해당 버튼은 `renderHistory`(recipe-history 모듈) 안에서 생성됨. `copyToClipboard`는 순수 함수이므로 `ctx.copyToClipboard = recipeLookup.copyToClipboard`로 노출하여 recipe-history가 `ctx.copyToClipboard()` 사용 (§3.4, §5 와이어링).

> **알려진 중복** (W3): `handleLookupClone`의 인라인 jspreadsheet 생성(L903-921)은 `initSpreadsheet`(L243-267)와 거의 동일한 설정을 반복한다. 순수 분리 원칙(§1.2, 동작 0줄 변경)상 본 PDCA에서는 중복을 그대로 보존한다 — recipe-lookup이 `ctx.spreadsheet`에 위임하지 않고 자체적으로 그리드를 만드는 이유. 중복 제거는 §12 Future Scope 후보.

**Factory**: `IRMS.management.createRecipeLookup(ctx)` → `{ loadProducts, setLookupSelection, handleLookup, copyToClipboard, handleLookupCopy, handleLookupClone }`

---

### 4.5 `static/js/management/version-compare.js` (~145 LOC)

| Source Line | Symbol | Returned Handle |
|---:|---|:---:|
| 633-645 | `handleLookupHistory()` | ✅ |
| 647-689 | `renderHistoryModal(data)` | ✅ |
| 691-694 | `getSelectedVersionIds()` | ✅ |
| 696-699 | `updateCompareButtonState()` | ✅ |
| 701-716 | `handleCompareVersions()` | ✅ |
| 718-760 | `renderCompareModal(data)` | ✅ |

**ctx deps**: `dom.historyModal/historyModalTitle/historyModalSubtitle/versionHistoryBody/historyCompareBtn/compareModal/compareModalTitle/compareThead/compareTbody`; `state.selectedRecipeId/currentHistoryChain`; `ctx.onClone` (`renderHistoryModal` 내 `.history-row-clone` 버튼 L679-687).

**핵심 변환**: `renderHistoryModal`의 `.history-row-clone` 버튼 `handleLookupClone()` → `ctx.onClone()`.

**Factory**: `IRMS.management.createVersionCompare(ctx)` → `{ handleLookupHistory, renderHistoryModal, getSelectedVersionIds, updateCompareButtonState, handleCompareVersions, renderCompareModal }`

---

### 4.6 `static/js/management.js` 컨트롤러 축소 (~165 LOC)

분리 후 잔존 책임:

| Source Line (현재) | 잔존 코드 |
|---:|---|
| 1, 1006 | `DOMContentLoaded` 래퍼 |
| 2-68 | DOM 참조 수집 → `ctx.dom` 구성 |
| 29-39 | 탭 네비게이션 + `switchToImportTab` 정의 |
| 41-95 | `ctx.state` / `ctx.const` 초기화, `chatState` 보유 |
| 315-317 | `loadMaterials` (controller 보유 — `ctx.state.materials` set) |
| 319-327 | `IRMS.createChat` 호출 + `refreshChatPanel`/`startChatPolling` 래퍼 |
| 565-600, 949-986 | 이벤트 리스너 바인딩 (버튼·필터·모달·visibilitychange) |
| 987-1005 | 초기화 IIFE |

> **W2 주의**: 현재 management.js는 DOM 참조(L2-68)와 상태 선언(L41-95)이 서로 끼어 있다 — 특히 L48-68의 Lookup/모달 DOM 참조가 상태 선언 구역 한가운데에 위치. 컨트롤러는 이를 깔끔한 `ctx.dom` 블록과 `ctx.state`/`ctx.const` 블록으로 분리 수집해야 한다 (코드 이동만, 동작 변경 없음).

**모듈 조립 + 와이어링** (§5).

### 4.6.1 이벤트 리스너 재매핑 (L565-600, L949-986 → `ctx` 타깃)

컨트롤러에 잔존하는 이벤트 바인딩은 분리 후 호출 대상이 모듈 핸들로 바뀐다:

| Source Line | 이벤트 | 분리 전 호출 | 분리 후 호출 |
|---:|---|---|---|
| 565 | `previewBtn` click | `handlePreview` | `importValidate.handlePreview` |
| 570 | `registerBtn` click | `handleRegister` | `importValidate.handleRegister` |
| 571 | `clearBtn` click | `handleClear` | `importValidate.handleClear` |
| 572-594 | `historyStatus/Search/From/To` change·input | `persistHistoryFilters`·`updateHistorySummary`·`renderHistory` | `recipeHistory.*` |
| 595-597 | `historyResetBtn` click | `resetHistoryFilters` | `recipeHistory.resetHistoryFilters` |
| 598-600 | `rawInput` input | `markPreviewStale` | `ctx.onDirty` (= `importValidate.markPreviewStale`) |
| 950-952 | `lookupBtn` click | `handleLookup` | `recipeLookup.handleLookup` |
| 953-960 | `lookupProduct` keydown(Enter) | `handleLookup` | `recipeLookup.handleLookup` |
| 961-963 | `lookupCopyBtn` click | `handleLookupCopy` | `recipeLookup.handleLookupCopy` |
| 964-966 | `lookupCloneBtn` click | `handleLookupClone` | `recipeLookup.handleLookupClone` |
| 967-969 | `lookupHistoryBtn` click | `handleLookupHistory` | `versionCompare.handleLookupHistory` |
| 970-972 | `historyModalClose` click | inline (`historyModal.hidden = true`) | inline 유지 (DOM만 — 컨트롤러) |
| 973-975 | `historyCompareBtn` click | `handleCompareVersions` | `versionCompare.handleCompareVersions` |
| 976-978 | `compareModalClose` click | inline | inline 유지 (컨트롤러) |
| 982-986 | `document` visibilitychange | `refreshChatPanel` | 컨트롤러 보유 (chat 래퍼) |

`debounce`(L579) 등 `IRMS.*` 공통 API 호출은 변경 없음.

---

## 5. Cross-Module Wiring (컨트롤러 조립 순서)

```javascript
document.addEventListener("DOMContentLoaded", () => {
  const ctx = {
    dom: { /* §3.1 — getElementById/querySelector 묶음 */ },
    state: { currentPreview: null, materials: [], sheet: null,
             confirmedRawText: "", previewIsStale: false,
             suppressDirtyTracking: false, spreadsheetFallbackNotified: false,
             currentHistoryChain: null, selectedRecipeId: null,
             pendingRevisionOf: null },
    const: { stageLabels, preferenceKeys },
  };

  // 탭 전환 (handleLookupClone이 사용)
  function switchToImportTab() { /* L882-886 */ }
  ctx.switchToImportTab = switchToImportTab;

  // ── 2단계 와이어링: 생성 → ctx 주입 ──
  const spreadsheet = IRMS.management.createSpreadsheetEditor(ctx);
  ctx.spreadsheet = spreadsheet;

  const importValidate = IRMS.management.createImportValidate(ctx);
  ctx.importValidate = importValidate;
  ctx.onDirty = importValidate.markPreviewStale;     // spreadsheet onchange가 사용

  const recipeLookup = IRMS.management.createRecipeLookup(ctx);
  ctx.recipeLookup = recipeLookup;
  ctx.onClone = recipeLookup.handleLookupClone;       // history/compare가 사용
  ctx.copyToClipboard = recipeLookup.copyToClipboard; // recipe-history가 사용

  const recipeHistory = IRMS.management.createRecipeHistory(ctx);
  const versionCompare = IRMS.management.createVersionCompare(ctx);

  // 이벤트 바인딩 + chat + 초기화 IIFE ...
});
```

**와이어링이 성립하는 근거**: 모든 모듈은 `ctx.spreadsheet`·`ctx.importValidate`·`ctx.onDirty`·`ctx.onClone`·`ctx.copyToClipboard` 등을 **호출 시점(사용자 인터랙션·init IIFE)** 에 `ctx`에서 꺼낸다 (§1.2 lazy 참조 원칙). 팩토리 실행 시점에는 이들이 `undefined`여도 무방하다 — `createImportValidate`조차 `ctx.spreadsheet`를 `handlePreview`/`handleClear` 본문(call time)에서만 읽으므로 마찬가지다.

**모듈 생성 순서는 따라서 임의(arbitrary)다.** 유일한 실제 제약은 *"모든 모듈 생성 + 모든 `ctx` 핸들/콜백 주입이 끝난 뒤에 이벤트 바인딩과 init IIFE를 실행한다"* 뿐이다. 본 설계·§11.1·Plan §10은 가독성을 위해 **spreadsheet → importValidate → recipeLookup → recipeHistory → versionCompare** 순서로 통일하되, 이는 의무가 아니라 컨벤션이다.

---

## 6. Side-Effect & Initialization Order

분리 후에도 현재와 동일 순서로 발생:

| 시점 | 동작 | 위치 |
|------|------|------|
| script parse | 5개 모듈이 `IRMS.management.create*` 팩토리만 등록 (실행 없음) | 각 모듈 |
| `DOMContentLoaded` | `ctx` 생성 → 모듈 5개 조립 → 이벤트 바인딩 | `management.js` |
| `DOMContentLoaded` (init IIFE, L987-1005) | `restoreHistoryFilters` → `updateHistorySummary` → `initSpreadsheet` → `loadMaterials` → `renderIssues×2` → `syncRegisterState` → `Promise.all([renderHistory, refreshChatPanel, loadProducts])` → `startChatPolling` | `management.js` |
| `visibilitychange` | `refreshChatPanel({replace:false, silent:true})` | `management.js` (L982-986) |

→ 초기화 IIFE 순서는 **현재 L987-1005 그대로** 보존. 단 호출 대상이 `ctx` 모듈 핸들로 바뀜 (`initSpreadsheet()` → `spreadsheet.initSpreadsheet()` 등).

---

## 7. Error Handling

분리에서 새로 추가되는 에러 시나리오:

| 시나리오 | 결과 | 방어 |
|----------|------|------|
| `management.js` 컨트롤러가 5개 모듈 `<script>` 전에 로드 | `IRMS.management.create* is undefined` → throw | `management.html` 로드 순서: 5개 모듈 → 컨트롤러 마지막 (§8) |
| 모듈이 `ctx.state.foo`가 아닌 캡처값 사용 | 탭 전환·복제 후 상태 불일치 (silent) | §1.2 원칙 + 코드 리뷰 체크 + 수동 스모크(Lookup→복제→Import 흐름) |
| `createImportValidate`가 `ctx.spreadsheet` 주입 전 호출 | `ctx.spreadsheet undefined` | §5 생성 순서 강제 (spreadsheet 먼저) |
| `ctx.onDirty`/`ctx.onClone`가 콜백 실행 시점에 미설정 | `TypeError` | 컨트롤러가 모든 모듈 생성·주입 완료 후 이벤트 바인딩·init IIFE 실행 |

기존 IIFE의 모든 try/catch·`IRMS.notify` 패턴은 그대로 보존.

---

## 8. Template `<script>` Loading

`templates/management.html` 현재 L402-407:

```html
<script src="/static/vendor/jsuites/jsuites.min.js"></script>
<script src="/static/vendor/jspreadsheet/jspreadsheet.min.js"></script>
<script src="/static/js/chat.js"></script>
<script src="/static/js/spreadsheet_editor.js"></script>
<script src="/static/js/management.js"></script>
<script src="/static/js/stock.js"></script>
```

`management.js` 한 줄을 6줄로 교체:

```html
<script src="/static/vendor/jsuites/jsuites.min.js"></script>
<script src="/static/vendor/jspreadsheet/jspreadsheet.min.js"></script>
<script src="/static/js/chat.js"></script>
<script src="/static/js/spreadsheet_editor.js"></script>
<!-- management modules: 5 factories (order-free) → controller last -->
<script src="/static/js/management/spreadsheet-editor.js"></script>
<script src="/static/js/management/import-validate.js"></script>
<script src="/static/js/management/recipe-history.js"></script>
<script src="/static/js/management/recipe-lookup.js"></script>
<script src="/static/js/management/version-compare.js"></script>
<script src="/static/js/management.js"></script>
<script src="/static/js/stock.js"></script>
```

- 5개 모듈은 팩토리 등록만 하므로 상호 순서 무관
- `management.js` 컨트롤러는 5개 모듈 뒤 + `chat.js` 뒤(컨트롤러가 `IRMS.createChat` 사용)
- `stock.js`는 그대로 마지막 — management 모듈과 무관

---

## 9. Test Plan

### 9.1 자동 검증

| Type | Tool | Command |
|------|------|---------|
| 신규 순수함수 테스트 | jest | `copyToClipboard` 등 — `tests/js/management_lookup.test.js` 신규 |
| 기존 JS 테스트 회귀 | jest | `tests/js/*.test.js` 3개 (영향 없음 확인) |
| Python 회귀 | pytest | 32개 (영향 없음 확인용) |
| 로드 순서 | grep | `grep -nE 'management' templates/management.html` |

> management.js는 `DOMContentLoaded` 클로저라 현재 단위 테스트 0건. 본 PDCA에서 순수 함수(`copyToClipboard`)에 한해 최소 테스트 1개 신규 추가. 나머지는 §9.2 수동 스모크가 1차 검증.

### 9.2 수동 스모크 체크리스트 (`/management`, DevTools Console 에러 0건)

**Import 탭**
- [ ] 페이지 로드 — 콘솔 에러 0건
- [ ] 스프레드시트 입력 → Validate → 배지(Rows/Warn/Error) 표시
- [ ] 시트 수정 → "재검증 필요" 경고 (`markPreviewStale`)
- [ ] Validate 통과 → Register → 등록 성공 → 시트 초기화 (`handleClear`)
- [ ] jspreadsheet 로드 실패 시 raw-input 폴백

**이력 탭**
- [ ] 상태/검색/기간 필터 → 목록 갱신, 새로고침 후 필터 복원 (`restoreHistoryFilters`)
- [ ] 행 클릭 → 아코디언 상세 (재료 칩)
- [ ] 상세행 — 엑셀 복사 / 복제하여 등록 / 등록 취소 / 삭제
- [ ] 필터 초기화 버튼

**Lookup 탭**
- [ ] 제품명 입력 → 조회 → 피벗 테이블
- [ ] 행 선택 → 액션 버튼 활성화
- [ ] 엑셀 복사 / 복제하여 등록 (→ Import 탭 전환 + 시트 로드)
- [ ] 버전 이력 모달 → 버전 2개 체크 → 비교 모달

**Chat / 공통**
- [ ] 채팅 패널 로드·전송·룸 전환
- [ ] 탭 간 전환 시 상태 유지 (Lookup 선택 → Import 복제 → 상태 일관)
- [ ] 모달 닫기 버튼

---

## 10. Coding Convention

### 10.1 모듈 헤더 템플릿

```javascript
/**
 * <module> module — <한 줄 설명>.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.create<Name>(ctx)
 * Returns: { <handle1>, <handle2>, ... }
 *
 * ctx dependencies:
 *   dom:   <used dom refs>
 *   state: <used state keys>
 *   other: ctx.spreadsheet / ctx.onDirty / ctx.onClone / ...
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.management = NS.management || {};
  NS.management.create<Name> = function (ctx) { /* ... */ };
})();
```

### 10.2 Naming

| Target | Rule | Example |
|--------|------|---------|
| 파일명 | `management/<scope>.js` (케밥 케이스) | `management/recipe-lookup.js` |
| 팩토리 | `IRMS.management.create<PascalName>` | `createImportValidate` |
| 상태 접근 | 항상 `ctx.state.<key>` / 디스트럭처 후 `state.<key>` | `state.selectedRecipeId` |
| 교차 호출 | `ctx.<handle>.<fn>()` / `ctx.<callback>()` | `ctx.spreadsheet.initSpreadsheet()` |

---

## 11. Implementation Guide

### 11.1 작업 순서

```
Step 1: inventory 확정
  - 33개 함수(30 모듈 + 3 컨트롤러) ↔ 10개 가변 상태 read/write 매트릭스(§3.5), 함수 호출 그래프 (§3·§4)

Step 2: 모듈 5개 생성
  - spreadsheet-editor → import-validate → recipe-lookup → recipe-history → version-compare
    (§5 와이어링 순서와 동일. spreadsheet→importValidate 포함 모든 순서가 임의 — lazy ctx 참조, §5 참조)

Step 3: management.js 컨트롤러 축소
  - ctx 생성 + 5개 모듈 조립 + 2단계 와이어링 + 이벤트 바인딩 + 초기화 IIFE

Step 4: management.html <script> 6줄 교체 (§8)

Step 5: 검증
  - 신규 jest 테스트 + 기존 3개 + 4개 탭 수동 스모크 + 콘솔 에러 0건
```

### 11.2 단위 검증 체크포인트

각 단계 후 `/management` 로드 → 콘솔 에러 0건이면 다음. 에러 시 직전 단계 롤백 → `ctx` 의존 표(§4) 재확인.

### 11.3 PR 전략

- **단일 PR**: Step 1~5 (중간 상태는 페이지 깨짐)
- **커밋 단위**: C1 spreadsheet-editor+import-validate / C2 recipe-history+version-compare / C3 recipe-lookup / C4 컨트롤러+템플릿 / C5 테스트·검증
- **롤백**: `git revert <merge-commit>` (서버·DB 영향 없음, 단일 템플릿)

---

## 12. Future Scope

| Phase | 대상 | 별도 PDCA |
|-------|------|-----------|
| 4 | `static/js/work.js` (~760 LOC) | `/pdca plan split-work-js` |

Phase 3 머지·안정화 후 진행.

---

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-05-19 | Initial draft — Phase 3 (management.js 분리) 상세 설계 | ykh00046 |
