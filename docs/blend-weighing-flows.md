# 배합(계량) 흐름 통합 레퍼런스

> BRM 배합 화면(단건 `/blend`, 이어서 계량 `/blend/continuous`)의 전체 계량 흐름을
> 시나리오별로 정리한 문서. 운영자·개발자 겸용. **모든 서술은 현재 코드 기준**이며 각
> 규칙 옆에 `파일:함수`를 표기했다. 값 단위는 전부 `g` 고정, 저울 해상도 기준 소수 2자리.
>
> 관련 소스
> - 프런트: `static/js/blend.js`(단건), `static/js/blend_continuous.js`(연속), 순수헬퍼 `static/js/blend_lib.js`
> - 템플릿: `templates/blend.html`, `templates/blend_continuous.html`, 스타일 `static/css/blend.css`
> - 백엔드: `src/routers/blend_routes.py`, `src/services/blend_service.py`, 모델 `src/routers/models.py`
> - 레시피 속성 승계: `src/routers/recipe_import_routes.py`

---

## 0. 용어·데이터 모델 요약

| 레시피 속성 | 컬럼 | 의미 | 배합에서의 효과 |
|---|---|---|---|
| 기준 자재 | `recipes.anchor_material_id` | 먼저 계량해 총량을 역산하는 자재 | 총량 입력 잠금, 실측→이론·총량 파생 |
| 허용 편차 | `recipes.tolerance_g` | 레시피별 허용 편차(g), NULL이면 기본 0.05 | 행별 편차 판정·저장 차단 기준 |
| 반응기 | `recipes.use_reactor` | 반응기 진행 반제품 | 저장 시 반응기(1~4) 필수 |
| 파생 | `recipes.is_derived` | 앞 단계 총량을 이월받아 다시 계량하지 않음 | 기준 자재 행 '파생 이월' 허용 |
| 1차→2차 연계 | `recipes.stage1_recipe_id` | 1차 레시피 명시 링크 | 현황 가족 묶음(문서 범위 밖), 원료 LOT 제안 근거 |

- 서버 진리값: `theory_amount`·`ratio`·`total_amount`은 저장 시 **서버가 레시피에서 재산출**한다
  (`blend_service.derive_details_from_recipe`, 감사 F-5). 클라이언트가 보낸 이론/비율은 버린다.
- 사람만 아는 값(실제 계량량·자재 LOT·수동입력 여부·이월 표식)만 클라이언트에서 받는다.

---

## 1. 표준 계량 흐름 (기준 자재 없는 일반 레시피)

**순서**: 분류→레시피 선택 → 총 배합량 입력 → (LOT → 실제량) 자재별 반복 → 저장.

### 동작 규칙
- **레시피 선택**: `blend.js:onRecipeChange`. 같은 레시피 재선택·미해석(빈 값)은 무시(입력 보존).
  선택 시 이전 레시피의 실제량·LOT·증량·이월·override 상태를 전부 초기화. 서버가 개정 체인
  최신판으로 자동 귀결(`blend_service._resolve_latest_revision`→`resolve_chain_tip`).
- **허용 편차 로드**: `state.toleranceG = recipe.tolerance_g || 0.05`(`blend.js:onRecipeChange`).
  서버 응답의 `recipe.tolerance_g`는 `blend_service.recipe_tolerance_g`가 산출(NULL/0 이하→0.05).
- **총량→이론량**: `blend.js:recomputeTheory`가 `theoryFromWeights`(value_weight 비례)로 산출,
  구 레시피(value_weight 없음)는 `computeTheoryAmount(ratio,total)` 폴백. 서버 `scale_theory`와 동일 산술,
  저울 해상도 2자리 반올림.
- **키보드 흐름(LOT 우선)**: 총량 Enter→첫 자재 LOT, LOT Enter→같은 행 실제량, 실제량 Enter→다음 행 LOT,
  마지막이면 저장 버튼(`blend.js:renderMatRows`, `bind`).
- **기준량 빠른 채우기**: 레시피 관리에서 지정한 `base_totals`(최대 3개)만 버튼 노출
  (`blend.js:renderBaseTotalButton`, `blend_lib.baseTotalValues`). 클릭 시 총량 채움+이론 재계산+`warnAllVariance`.
- **계량 순서 안내**: 실제량이 빈 첫 행을 파란 `row-next`로 강조(`blend.js:updateNextWeighGuide`).
- **다음 입력칸 강조**: 총량 미입력 시 총량, 입력 후 작업자 칸을 `needs-input`으로(`blend.js:updateInputGuide`).

### LOT 필수 정책
- **클라이언트**: 저장 시 실제량(>0)이 있는데 LOT가 빈 행이 있으면 차단·첫 누락 행 포커스
  (`blend.js:saveBlend` → `blend_lib.missingLotNames`/`missingLotBlockMessage`).
- **서버**: `enforce_carry_over`·`derive` 이후 최종 행 기준으로 `blend_service.missing_lot_names` 검사,
  누락 시 `HTTP 400 "자재 LOT 를 입력하세요: …"`(`blend_routes.blend_create`).

### 허용 편차 정책
- 판정 기준: **행별** `|실제-이론| > tolerance_g`. 합계 편차는 제한 없음.
- **입력 즉시 경고**: 실제량 change/Enter 시 `blend.js:warnIfVariance`(초과·부족 모두 경고,
  편차 벗어난 값이 남아 있으면 다음 LOT로 내려가지 않음 — 2026-07-22 현장 요구).
- **표시**: `blend_lib.varianceDisplay`가 허용 내는 중립, 초과는 `var-up`/`var-down` 색.
- **저장 차단(클라)**: 기준 자재 제외한 초과 행이 있으면 저장 중단(`blend.js:saveBlend`).
- **저장 차단(서버)**: `blend_service.weighing_tolerance_violations(details, tolerance)` →
  `HTTP 400 "허용 편차(±{tol}g)를 초과한 자재: …"`(`blend_routes.blend_create`). 편차는 `recipe_id`로
  결정하며 recipe_id 없으면 0.05.

### 저장 후
- `product_lot` = `{제품명}{YYMMDD}{순번:02d}` 자동 채번(`blend_service.generate_product_lot`),
  BEGIN IMMEDIATE로 채번+INSERT 원자화(감사 F-1).
- 실제량/LOT/증량/이월 초기화, 임시저장 삭제(`clearDraft`), 5분 무입력 자동 로그아웃 카운트 시작
  (`blend.js:armPostSaveLogout`).

---

## 2. 기준 자재(anchor) 레시피

`recipes.anchor_material_id` 지정 레시피. 기준 자재를 먼저 실측하고 그 값으로 총량·이론을 역산.

### 동작 규칙
- **유효성 방어**: 기준 자재가 항목에 없거나 그 value_weight≤0이면 anchor 무효화(None 처리)해
  일반 흐름으로 되돌린다(`blend_service.get_recipe_for_blend` 의 `effective_anchor`).
- **총량 잠금**: 총 배합량 입력이 readonly + "기준 자재 계량 후 자동 산출" placeholder, 기준량 버튼 숨김
  (`blend.js:applyAnchorMode`).
- **입력 잠금 순서**: 기준 자재 실측 전에는 비기준 자재 실제량 칸 `disabled`(`renderMatRows`의 `disableActual`,
  `materialRowHtml`). 기준 자재 입력 후 해제.
- **실측→파생**: 기준 자재 실제량 변경 시(손입력·저울 공통) `blend.js:applyAnchorRecompute` 트리거
  (`updateRowVar` 안에서 재진입 가드로 호출). `blend_lib.computeAnchorTheory`로 각 행 이론량·도출 총량 산출,
  총량 입력에 기입.
- **기준 값 변경 시 초기화**: 기준 자재 값이 바뀌고 다른 자재에 실측값이 있으면 경고 후 비기준 실측 전량 삭제
  (`applyAnchorRecompute`의 `changed` 분기).
- **기준 행 편차 면제**: 기준 자재는 이론=실측이라 편차 항상 `-` 표시(`blend_lib.varianceDisplay`의 `is_anchor`),
  클라 저장 차단(`saveBlend`의 `i !== ai`)·서버 편차 검사(`weighing_tolerance_violations`, theory=actual→0) 모두 통과.
- **서버 총량 역산**: 저장 시 서버가 총량을 무시하고 `total = round(anchor_actual*100/ratio_anchor, 2)`로 재계산,
  나머지 이론을 그 총량에 비례 배분(`blend_service.derive_details_from_recipe`).

### 이어서 계량 미지원
- 연속 화면은 기준 자재 레시피를 **차단**(`blend_continuous.js:onRecipeChange`의 `anchorBlocked`,
  안내 후 표를 비움). 서버는 별도 차단 없음(연속 body에 anchor 개념 없음 — 클라가 막는다).

---

## 3. 1차→2차 연계 (파생 이월)

두 단계 제조(1차 중간체 → 2차 최종). 반응기에 1차 제품이 남아 2차에서 그 자재를 다시 계량하지 않는 경우.

### 자격
- `carryOverEligible()` = **기준 자재 있음 && `recipe.is_derived`**(`blend.js:carryOverEligible`).
  파생 여부는 `use_reactor`와 **독립**(`blend_service.recipe_is_derived`).

### 프런트 흐름 (`blend.js`)
- 기준 자재 행 LOT 칸 옆 컨트롤(`refreshCarryOverControl`):
  로트 선택 전엔 힌트("반응기 1차 제품 — 로트를 선택해 이월하세요"), 등록된 1차 LOT 입력 후
  "1차 총량 N g" 배지 + [파생 이월] 버튼. 1차 총량·LOT 후보는 원료 LOT 제안(`state.lotSuggest`)에서 찾는다
  (`findStage1Match`).
- [파생 이월]→확인 모달(`openCarryOverModal`)→적용(`applyCarryOver`): `carried_over=true`, 실제량=1차 총량,
  실제량 칸 readonly + "이월" 태그(클릭 시 `clearCarryOver`). 이후 `applyAnchorRecompute`로 나머지 이론 재산출.
- LOT를 바꾸거나 이월 태그 클릭 시 이월 취소(`clearCarryOver`). LOT input 변경 감지도 이월 무효화(`renderMatRows`).

### 서버 검증·강제 (`blend_service.enforce_carry_over`, create/update 공통)
`carried_over=true` 행은 아래를 **모두** 만족해야 하며(하나라도 위반 시 `CarryOverError`→HTTP 400):
1. 레시피가 파생(`recipe_is_derived`)일 것 — 반응기 여부 무관.
2. 그 행이 레시피 기준 자재(anchor) 행일 것.
3. `material_lot`가 완료된 1차 배합 기록(product_name=이 자재명, product_lot=그 LOT, status='completed')에 존재.
- 통과하면 `actual_amount`를 1차 기록 `total_amount`로 **강제 덮어쓰기**(변조 방지)+`manual_entry=false`.
- 이 강제 채움이 `derive`보다 먼저 실행(`blend_routes.blend_create`)되어 올바른 실측으로 이론·총량이 산출됨.
- 이월 행은 LOT가 채워지므로 `missing_lot_names` 검사도 자연 만족(별도 분기 없음).

### 승계 (`recipe_import_routes.py`)
수정 등록 시 `is_derived`·`stage1_recipe_id`·`anchor_material_id`·`tolerance_g`가 명시 없으면 부모에서 승계
(`inherited_*` 블록, L145-205). `anchor_material_id`는 새 버전 자재에 여전히 존재할 때만 승계.

---

## 4. 반응기 품목 (`use_reactor`)

### 동작 규칙
- **소유권**: 같은 제품명의 가장 최근 completed 레시피 `recipes.use_reactor`를 따르고, 매칭 레시피가 없으면
  `viscosity_products.use_reactor`로 폴백(`blend_service.product_uses_reactor`).
- **필드 노출**: `recipe.use_reactor`일 때만 반응기 select 노출(`blend.js:renderReactorField`,
  `blend_continuous.js:renderReactorField`).
- **저장 필수(클라)**: `saveBlend`/`save`에서 반응기 미선택 시 차단.
- **저장 필수(서버)**: `product_uses_reactor && reactor is None` → HTTP 400 "반응기를 선택하세요"
  (`blend_routes.blend_create`/`blend_update`/`blend_create_continuous`).
- **점도 연계**: 배합 기록의 `reactor` 값은 점도 등록 시 함께 전달(`blend_routes.blend_add_viscosity`,
  `reactor=record.get("reactor")`).

---

## 5. 적게 넣은 경우(부족)

편차가 −방향(실제<이론)으로 tolerance 초과.

### 단건 (`blend.js:warnIfVariance` −방향 분기)
- 부족 경고 + **부족량 명시 팝업**. 현재 코드는 `window.confirm`이나 **의도된 최종 형태는 `#shortage-modal`**로
  교체 중(동시 작업). 모달 버튼: **"추가로 채우기 (합산 입력)"** / **"다시 계량"**.
  - "추가로 채우기": `openAddInline(i)` — 편차 셀을 인라인 입력으로 바꿔 **추가분을 현재 값에 합산**
    (`applyAddAmount`, 저울 PRINT도 합산 대상). 저울 영점 실수 등으로 부족하게 찍힌 경우 처음부터
    재계량하지 않고 올린 무게만 더해 목표를 맞춘다.
  - "다시 계량": 아무 것도 열지 않음(값 유지, 재계량 유도).
- 이미 합산 입력 중(`addModeIdx===i`)이면 팝업 생략.
- **부족 상태로는 다음 LOT로 이동 차단**: `warnIfVariance`가 true를 반환하면 Enter/저울 PRINT 흐름에서
  현재 칸에 머문다(`renderMatRows`의 actual keydown, `fillScaleValue`).
- **합산 모드 잠금**: 추가 모드 중 실제량(누계) 칸을 readonly로 잠가 직접 타이핑으로 누계가 통째로
  덮어써지는 실수 방지(`openAddInline`).

### 연속과의 차이
- 연속은 **합산 모드 없음** — 부족 시 `window.alert`로 "저울을 다시 올려 채운 뒤 최종 무게(합계)를 다시 입력"
  안내만(`blend_continuous.js:warnIfVariance` −방향). (9절 참조)

---

## 6. 많이 넣은 경우(초과) — 증량(rescale)

편차가 +방향(실제>이론)으로 tolerance 초과.

### 동작 규칙 (`blend.js`)
- **경고 + 증량 제안 모달**: `warnIfVariance` +방향→`offerRescale`. `blend_lib.rescalePlan`으로 `newTotal` 산출.
  - `newTotal = max(현재총량, tol 이상 초과한 계량 행의 required)`, `required=actual*100/ratio`.
  - **tol 게이팅**: 목표 대비 tol 이내 초과는 총량을 바꾸지 않음(편차는 정해진 총량 내 흡수). 이 게이팅이
    없으면 미세 편차가 ×100/비율로 증폭돼 총량을 밀어 올리고 계량 순서에 의존하는 버그가 났었다
    (`blend_lib.rescalePlan` 주석).
- **25,000 g 한도**: `newTotal > BATCH_LIMIT_G(25000)`면 증량 모달 대신 폐기 권장 모달(`#discard-modal`,
  [그래도 증량]/[증량 취소])(`offerRescale`→`exceedsBatchLimit`, `openDiscardModal`).
- **적용**(`applyRescale`): 일반 레시피는 총량 입력을 newTotal로 갱신 후 input 이벤트로 이론 재계산;
  기준 자재 레시피는 `state.rescaleTotalG`를 올려 `recomputeAnchorRescale`로 도출 총량·이론·추가분 갱신
  (유효 총량 = `max(기준 파생 총량, rescaleTotalG)`, `effectiveCurrentTotal`).
- **행 배지**: 계량된 행 편차 셀에 "목표 Y · 추가 +X g" 배지 표시(`renderAddBadges`), 클릭 시 인라인 추가분 입력.
  기준 자재 행도 이론이 커지므로 배지 표시(추가 계량 필요).
- **증량 대기 행 편차 경고 제외**: `state.addPending[i]`가 있으면 편차 경고·음수 편차 표시 생략(양수 배지가 대신
  안내). `warnIfVariance`는 `addPending` 행에서 false 반환(정확히 계량한 행이 증량 직후 "초과"로 오탐되던
  2026-07-22 신고 대응).
- **방금 증량 취소**(`restoreRescaleUndo`, `#rescale-undo` 링크): 증량 직전 총량·이론 스냅샷으로 1회 복원.
  **추가분을 넣기 시작하면 무효화**(`applyAddAmount`에서 `rescaleUndo=null`).
- **서버 영향 없음**: 증량은 클라 표시·저장 차단만 바꾼다. 서버는 저장 시 총량×비율로 재산출.

### 저장 시 총량
- 저장 body의 `total_amount`는 (증량 반영된) `blend-total` 입력값(`saveBlend`). 단, 기준 자재 레시피는
  서버가 anchor 실측에서 총량을 다시 역산하므로 클라 총량은 사실상 무시된다(2절).

---

## 7. 연속 증량 (반복 초과)

### 같은 행을 또 초과한 경우
- 증량 후 추가분을 넣다가 다시 초과하면 `applyAddAmount`→`warnIfVariance`→(+방향) `offerRescale` 재호출.
  `rescalePlan`은 순수 함수라 새 실제값 기준으로 `newTotal`을 다시 계산하고 `max` 규칙으로 더 커진다.
  같은 증량/폐기 모달이 다시 뜬다(`offerRescale` 주석). 중복 트리거는 모달 열림·`pendingRescale` 가드로 방지.

### 연속(다중 로트) 화면의 증량 (`blend_continuous.js`)
- **로트별 스코프**: 초과가 난 **그 로트만** 증량(`offerContRescale(j)`→`applyContRescale`,
  `state.lotRescale[j]=newTotal`). 다른 로트 절대 불변.
- 로트별 이론 `theoryFor(i,j)`가 증량 로트만 그 총량 기준으로 재산출. 헤더에 조정 총량 배지 + 강조.
- 저장 시 `lotRescale`이 하나라도 있으면 `lot_totals`(로트별 `lotTotal(j)=max(total, override)`) 전송,
  전부 null이면 미전송(기존 동작 동일)(`blend_continuous.js:save`, `models.BlendContinuousBody._check_lot_totals`).
- 서버는 로트별 `lot_total`로 도출·편차검사하고 record.total_amount도 그 값으로 저장
  (`blend_routes.blend_create_continuous`, `blend_service.create_continuous`).

---

## 8. LOT 누락 방지 + 미등록 LOT 차단

### LOT 누락(빈 값)
- 클라: `saveBlend`/`save`가 실제량 있는 행에 LOT 없으면 차단(5·1절). 서버: `missing_lot_names`→400.

### 미등록 LOT 차단(반제품 자재만)
- 대상: **원료 LOT 제안(`state.lotSuggest`)이 있는 자재** = 완료 배합 기록이 있는 반제품(1차 중간체). 일반
  원료(제안 없음)는 검증하지 않음.
- 판정: 빈 값 통과 / 제안 목록에 있으면 통과 / 그 외 서버 `/blend/product-lot-exists`로 확인
  (`blend.js:checkLotRegistered`, 캐시 `lotChecked`). 네트워크 오류는 **통과(fail-open)**.
- 확정(change) 시 검증(`validateLotInput`), 미등록이면 `#lot-invalid-modal`.
- **'사유 적고 진행' 예외(안전밸브)**: 1차 기록이 아직 없는 정당한 경우 사유 입력 후 통과 처리
  (`state.lotOverrides[name lot]=사유`). 저장 시 사유를 비고 앞에 `[미등록 LOT 진행] …`로 남김
  (`blend.js:buildOverrideNote`). 저장 직전에도 전 행 재검증(`saveBlend` 루프).
- **bulk 재생성 예외**: 일괄 생성은 LOT를 비우고 실제량=이론량으로 채운다 — `missing_lot_names`·미등록 검증을
  거치지 않음(`blend_service.create_bulk`, `blend_routes.blend_create_bulk`). 문서용/드문 재생성 경로.

---

## 9. 이어서 계량 화면과의 차이 (`/blend/continuous`)

| 항목 | 단건 `/blend` | 연속 `/blend/continuous` |
|---|---|---|
| 구조 | 자재 행 × 1로트 | 자재 행 × N로트(자재 열 우선), 실제량만 셀별 |
| 총량/LOT/서명/반응기/비고 | 배치 1건 | **전 로트 공통**(자재 LOT = `sharedLot[i]`) |
| 기준 자재 레시피 | 지원 | **미지원**(anchorBlocked, 단건으로 유도) |
| 부족(−) 처리 | **합산 모드**(추가로 채우기) | **합계 재입력**(window.alert 안내, 합산 없음) |
| 초과(+) 증량 | 배합 전체 | **로트별**(그 로트만) |
| 증량 대기 편차 억제 | `addPending`로 음수·경고 억제 | **억제 없음**(음수 편차·부족 alert 그대로 노출 — 아래 GAP) |
| 반응기 이월 | 지원 | **거부**(서버 400, `blend_create_continuous`) |
| 저장 | `POST /blend/records` 1건 | `POST /blend/records/continuous` N건(원자적, 하나 실패 시 전부 미저장) |
| 저장 후 | 폼 초기화·5분 자동 로그아웃 | `/status`로 이동 |

---

## 10. 부가 기능

- **임시 저장·복구**(단건만): 진행 입력을 localStorage(`irms.blend.draft`)에 600ms 디바운스 저장,
  진입 시 24시간 이내 초안이면 배너로 복구 제안(`blend.js:currentDraft`/`scheduleDraftSave`/`offerRestore`/
  `restoreDraft`). 저장·버리기 시 삭제. 연속 화면은 임시저장 없음.
- **저울 연동**: 현장 PC `127.0.0.1:8787`(scale_agent) `/health`·`/events` 폴링(`detectScale` 30s,
  `pollScaleEvents` 0.8s). PRINT→활성 행/셀 실제량 자동 입력(`fillScaleValue`, `activeScaleRow`/`activeScaleCell`).
  저울 연결 중 손입력은 행·배치 `manual_entry` 플래그로 기록(추적성, 이상 통계 신호).
- **저울 전용 입력 모드**: `/api/settings/scale-only-input`이 enabled면 실제량·추가분 인라인 입력을 readonly로
  잠그고(`applyScaleOnlyToRows`/`applyScaleOnlyToCells`), 저울 미연결 시 상시 배너. 기본(false)이면 동작 변화 없음.
- **작업자 교대**: 작업자 칸에서 이름 선택 시 로그아웃 없이 세션 전환(`switchWorker`). 처음 보는 이름은 등록 확인.
  저장 직전 세션과 다르면 교대 후 저장, "작업자 'X' 이름으로 저장합니다" 확인(`saveBlend`).
- **전자서명**: 캔버스 패드(`attachSignaturePad`), 서명 시에만 dataURL 저장(`worker_sign`).
- **점도 연계**: 배합 기록별 점도는 점도 관리 화면 한 곳에서 등록(`POST /blend/records/{id}/viscosity`,
  `blend_routes.blend_add_viscosity`). 배합/기록 화면에는 점도 입력 폼 없음. `product_lot`·첫 자재 LOT·반응기 연계.
- **수동입력 마스킹**: `manual_entry`는 책임자 응답에만 노출, 비책임자 조회에선 False로 가림(`_mask_manual_entry`).
- **저장 후 자동 로그아웃**: 저장으로 폼이 비면 5분 카운트, 새 입력 시작 시 해제(`armPostSaveLogout`/
  `cancelPostSaveLogout`, capture 단계 input/change 리스너).

---

## 부록: 저장 파이프라인 순서 (`blend_routes.blend_create`)

1. `details` 비었으면 400 → 2. 반응기 필수 검사 → 3. `enforce_carry_over`(이월 검증·강제 채움) →
4. 개정 체인 tip 불일치면 409 → 5. `derive_details_from_recipe`(서버 이론·총량 재산출) →
6. `missing_lot_names`(LOT 필수) → 7. `weighing_tolerance_violations`(편차) → 8. 작업자 세션 확인 →
9. `create_blend_record`(채번+INSERT 원자화) → 10. 감사 로그 + commit.
