# split-management-js Gap Analysis (Phase 3)

> **Match Rate**: **99%** — 설계 대비 구현이 7개 평가 항목 전체에서 일치. 코드 수정 없이 Report 단계로 진행 가능.
>
> **Phase**: Check (PDCA)
> **Date**: 2026-05-19
> **Commit**: `26d1160`
> **Agent**: bkit:gap-detector
> **Recommendation**: §9.2 수동 스모크 완료 후 `/pdca report split-management-js`

---

## 1. Overview

| Item | Value |
|---|---|
| Analysis Target | split-management-js Phase 3 (`static/js/management.js` 분리) |
| Design Document | `docs/02-design/features/split-management-js.design.md` |
| Plan Document | `docs/01-plan/features/split-management-js.plan.md` |
| Implementation Commit | `26d1160` |
| Code Files Verified | 5 modules + 1 controller + 1 template + 1 test |
| Functions Verified | 33/33 |

---

## 2. 모듈 함수 매핑 (설계 §4 — 33개 함수)

| Module | 설계 함수 수 | 구현 | 일치 |
|--------|:---:|:---:|:---:|
| `management/spreadsheet-editor.js` | 6 | 6 (전부 존재·반환) | ✅ |
| `management/import-validate.js` | 7 | 7 | ✅ |
| `management/recipe-history.js` | 5 | 5 | ✅ |
| `management/recipe-lookup.js` | 6 | 6 | ✅ |
| `management/version-compare.js` | 6 | 6 | ✅ |
| `management.js` 컨트롤러 잔존 | 3 (`loadMaterials`/`refreshChatPanel`/`startChatPolling`) | 3 | ✅ |
| **합계** | **33** | **33** | **33/33** |

**결과**: 33/33 함수 — 누락 0, 중복 0, 초과 0. 모든 모듈 ≤ 250 LOC (최대 `recipe-history.js` 215 LOC).

---

## 3. `ctx` 스키마 일치 (설계 §3)

| ctx 그룹 | 설계 | 구현 | 일치 |
|---|---|---|:---:|
| `ctx.dom` | 50개 참조 6그룹 | `management.js` L12-59, 50개 전부 | ✅ |
| `ctx.state` | 10개 키, 초기값 명시 | L96-107, 10개 전부 동일 초기값 | ✅ |
| `ctx.const` | `stageLabels`, `preferenceKeys` | L80-91, L108 | ✅ |
| `ctx.spreadsheet`/`importValidate`/`recipeLookup` | 모듈 핸들 주입 | L113-121 | ✅ |
| `ctx.onDirty`/`onClone`/`copyToClipboard`/`switchToImportTab` | 콜백 주입 | L110, L118, L122-123 | ✅ |
| `chatState` | 컨트롤러 보유 (ctx.state 제외) | L129-136, 별도 보유 | ✅ |

**결과**: ctx 스키마 100% 일치. Plan §3.4의 `ctx.tabs.switchTab`는 Design §3.4가 `ctx.switchToImportTab`로 정정, 구현은 Design을 따름.

---

## 4. 상태 접근 — 참조 vs 캡처 (설계 §3.5)

모든 모듈이 `const { dom, state } = ctx;`로 디스트럭처 후 10개 공유 키를 전부 `state.<key>`로 접근. **원시값 캡처(`const x = state.foo`) 0건.**

| State Key | 교차 모듈 쓰기 | 구현 확인 |
|---|---|:---:|
| `currentPreview` | import-validate + lookup | `state.` 접근 ✅ |
| `confirmedRawText` | import-validate + lookup | ✅ |
| `previewIsStale` | import-validate + lookup | ✅ |
| `suppressDirtyTracking` | spreadsheet + lookup (최고 위험) | ✅ |
| `selectedRecipeId` | lookup + history + version-compare | ✅ |
| `pendingRevisionOf` | import-validate + lookup | ✅ |
| `sheet` | spreadsheet | ✅ |
| `spreadsheetFallbackNotified` | spreadsheet | ✅ |
| `materials` | controller (dead state, 보존) | ✅ |
| `currentHistoryChain` | version-compare (dead state, 보존) | ✅ |

**결과**: 100% — 모든 상태 접근이 `ctx.state.<key>` 경유. **Plan Risk #1(캡처 vs 참조, 최고 위험) 완전 해소.** dead state 2종은 순수 분리 원칙상 보존.

---

## 5. 교차 모듈 와이어링 (설계 §5)

| 메커니즘 | 설계 | 구현 | 일치 |
|---|---|---|:---:|
| `ctx.onDirty` | spreadsheet `onchange/onafterchanges/onpaste` | `spreadsheet-editor.js` L115-123 | ✅ |
| `ctx.onClone` | history `.history-clone-btn` + version `.history-row-clone` | `recipe-history.js` L165, `version-compare.js` L79 | ✅ |
| `ctx.spreadsheet` | import-validate + lookup | `import-validate.js` L73/137, `recipe-lookup.js` L183-218 | ✅ |
| `ctx.importValidate` | lookup `handleLookupClone` | `recipe-lookup.js` L231-234 | ✅ |
| `ctx.copyToClipboard` | recipe-history `.history-copy-btn` | `recipe-history.js` L155 | ✅ |
| `ctx.switchToImportTab` | lookup `handleLookupClone` | `recipe-lookup.js` L179 | ✅ |
| 2단계 와이어링 순서 | 생성 → ctx 주입 | `management.js` L113-126 | ✅ |
| `handleLookupClone` 인라인 jspreadsheet 콜백 | `() => ctx.onDirty()` (W3 중복 보존) | `recipe-lookup.js` L209-211 | ✅ |

**결과**: 100% — 6종 교차 메커니즘 전부 설계대로. 모듈 간 직접 import 0건, 전부 `ctx` 경유. W3 알려진 중복(`handleLookupClone` 인라인 그리드)은 §4.4대로 의도적 보존.

---

## 6. 동작 보존 (순수 분리)

- 33개 함수 본문 로직 구조가 §4 source-line 매핑대로 보존.
- 문서화된 변환만 적용: jspreadsheet 콜백 `markPreviewStale()` → `ctx.onDirty()`, 직접 호출 → `ctx.spreadsheet.*`/`ctx.importValidate.*`/`ctx.onClone()`/`ctx.copyToClipboard()`/`ctx.switchToImportTab()`.
- 초기화 시퀀스(§6) 정확히 보존: `management.js` L233-251.
- 이벤트 재매핑(§4.6.1) 18개 바인딩 전부 정확, 모달 닫기 inline 유지, `debounce` 변경 없음.
- 모든 `try/catch` + `IRMS.notify` 패턴 보존.

**결과**: 동작 변경 징후 없음. 순수 분리 의도와 일치.

---

## 7. `<script>` 로드 순서 (설계 §8) + 테스트

`management.html` L402-413: `jsuites → jspreadsheet → chat.js → spreadsheet_editor.js → management/{5개 모듈} → management.js → stock.js` — §8과 정확히 일치.

테스트: `tests/js/management_lookup.test.js` 신규 (factory 핸들 완전성 + `copyToClipboard` 2경로). 기존 JS 테스트 3개 + 신규 1개 = **4/4 PASS**. pytest **32/32 PASS** (회귀 없음).

---

## 8. Match Rate 산출

| 범주 | 가중치 | 점수 | 가중점 |
|---|---:|---:|---:|
| 모듈 함수 매핑 (33/33) | 30 | 100% | 30.00 |
| ctx 스키마 일치 | 20 | 100% | 20.00 |
| 상태 접근 (캡처 0) | 20 | 100% | 20.00 |
| 교차 와이어링 (6종) | 15 | 100% | 15.00 |
| 동작 보존 | 10 | 95% | 9.50 |
| 로드 순서 + 테스트 | 5 | 100% | 5.00 |
| **합계** | **100** | — | **99.50** |

**Reported**: **99%** — 잔여 1%는 분리 전 원본(`8fa30fa`) 대비 byte-level diff 미실행(분석 환경 제약) + §9.2 수동 스모크 미완.

---

## 9. Gaps

### Minor (코드 조치 불필요)
1. Plan §3.4/§8의 `ctx.tabs.switchTab` 표기는 Design §3.4가 `ctx.switchToImportTab`로 정정함. 구현은 Design 일치. Plan은 동결 문서 — 조치 불필요.

### Verification 보류 (코드 갭 아님)
2. **§9.2 4개 탭 수동 스모크** (Import/이력/Lookup/Chat, DevTools Console 0 errors) — 순수 분리는 공개 API 계약이 없어 정적 검증이 불가하므로 Report sign-off 전 사용자 수동 확인 권장.

### Critical
없음.

---

## 10. Recommendation

✅ **Match Rate ≥ 90%** — split-management-js Phase 3는 설계 충실 구현. iteration 불필요.

**다음 단계**: §9.2 수동 스모크 완료 → `/pdca report split-management-js` → archive.

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-19 | bkit:gap-detector 기반 초기 Gap 분석 |
