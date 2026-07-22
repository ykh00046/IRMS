# 레시피 관리 흐름 (BRM 배합·레시피 도메인)

> 대상 화면: `/management` (레시피 관리 — 등록/현황/조회/품목코드 탭)
> 코드 경로: `src/routers/recipe_*_routes.py`, `src/routers/item_code_routes.py`,
> `src/services/import_parser.py`, `src/services/recipe_helpers.py`,
> `src/services/blend_service.py`, `src/services/record_delete_service.py`,
> `static/js/management/*.js`, `templates/management.html`
>
> 이 문서는 실제 코드를 읽고 검증한 시나리오 기반 설명이다. 각 절은 `파일:함수` 로 근거를
> 표기한다. 마지막 절(9)은 검토 중 발견한 결함/갭 목록이다(코드 수정 없음 — 보고용).
>
> 용어: 이 도메인에서 **분류(category)** 의 허용값에 `잉크` 가 데이터로 포함된다(UI 카피가
> 아니라 레시피 속성값). CLAUDE.md 의 "UI에서 잉크 금지" 규칙과 별개다.

---

## 1. 등록 흐름 (세로 BOM 편집기 → TSV → 검증 → 등록)

**시나리오:** 책임자가 새 반제품 레시피(예: NPS)를 자재별 배합량과 함께 등록한다.

### 1.1 입력 — 세로 BOM 편집기
- 화면: `templates/management.html` `#tab-import`, 편집기 컨테이너는
  `static/js/management/bom-editor.js` `createBomEditor` 가 렌더.
- 편집기 상태 모델은 단일 레시피: `{ productName, rows[{type:'material',name,value}|{type:'step',note}], remark }`
  (`bom-editor.js` 상단 주석 + `bom` 객체).
- 자재행마다 자재명 옆에 **품목코드 배지**(`materials.code`)를 상시 표시 — `codeFor()`
  가 정확명→소문자키 순으로 매칭(`bom-editor.js:48`).
- 자재명 자동완성은 `<datalist id="bom-material-names">`, 코드가 있으면 `label` 로 붙임.
- 엑셀(TSV) 붙여넣기: `bind()` 의 `paste` 리스너가 탭 포함 클립보드만 가로채
  `loadFromTsvRows()` 로 표 전체 해석(`bom-editor.js:332`).

### 1.2 직렬화 — TSV (서버 계약 경계)
- `getSpreadsheetDataAsText()` (`bom-editor.js:140`) 가 편집기 상태를 **헤더행 + 값행**
  TSV 로 직렬화: `[반제품명, 자재명..., (설명)..., 비고?]` / `[제품명, 값..., 노트..., 비고?]`.
- 이름 없는 빈 자재행·빈 설명행은 제외. 완전 빈 편집기는 `""` 반환.
- **다중 반제품(값행 2줄 이상)은 편집기로 담을 수 없다** — `loadFromTsvRows()` 가
  `false` 를 돌려주고 raw textarea 폴백 + 경고(`bom-editor.js:172`). 서버 파서는 다중
  블록을 지원하므로 대량 이관은 raw 폴백으로 처리.

### 1.3 검증(preview) — 3단 자재 판정 + 무부작용
- 진입: `import-validate.js` `handlePreview()` → `IRMS.previewImport(raw)` →
  `POST /recipes/import/preview`.
- 라우터: `recipe_import_routes.py` `import_preview` — **미리보기는 무부작용**.
  `parse_import_text` 가 미등록 자재를 그 자리에서 INSERT 하므로, 라우터가
  `connection.rollback()` 으로 폐기한다(감사 F-3, `recipe_import_routes.py:89-102`).
  응답의 `material_id` 는 롤백될 임시값.
- 파서: `import_parser.py` `parse_import_text` → `get_header_config()` 내부의
  **자재 3단 판정**(`import_parser.py:293-383`):
  1. **existing** — `materials`(활성) 또는 별칭에 정규화 토큰 일치 → 기존 `material_id`.
     처음 쓰는 자재면 level-3 안내("처음 사용하는 원재료입니다").
  2. **master** — 기존엔 없지만 `item_code_master`(`kind='material'` → 없으면 `'product'`)
     **단일 히트** → 코드 부여하며 자동 등록 + level-3 안내(차단 아님).
  3. **unknown** — 어디에도 없음. `_similar_candidates()`(difflib, cutoff 0.75) 로 유사
     후보 제시. 처리 분기:
     - `master_index is None`(마스터 0행, 하위호환 모드) → 코드 없이 자동 등록.
     - 그 외(마스터 존재) → **errors 에 추가 → 등록 차단**(유사 후보 안내).
       (파싱은 계속하되 `errors` 가 비면 안 되므로 register 400.) 신규 자재는
       먼저 품목코드 탭(`POST /materials`) 또는 마스터에 등록해야 임포트가 통과한다.
- 유사 후보/판정 결과는 `material_matches` 배열로 preview 응답에 실림(`import_parser.py:192,543`).

### 1.4 등록(register)
- 진입: `import-validate.js` `handleRegister()`. 기준 자재·허용 편차·품목코드·반응기·파생·
  1차 연계 중 하나라도 지정되면 `importWithAnchor()` 로 `POST /api/recipes/import` **직접
  POST**(CSRF `x-csrftoken` 직접 부착), 아니면 `IRMS.importRecipes()`.
- 라우터: `recipe_import_routes.py` `import_recipes`:
  - `parse_import_text` 재실행 → `errors` 있으면 400.
  - **중복 방지**: `raw_input_hash`(SHA-256) 로 동일 원문 검사 — `not force and revision_of
    is None` 일 때만. 있으면 409 `DUPLICATE_IMPORT`(`recipe_import_routes.py:123-139`).
  - 각 `parsed_row` 를 `recipes` 에 `status='completed'` 로 INSERT(**등록 즉시 사용중**,
    `recipe_import_routes.py:306`), `recipe_items`·`recipe_steps` 삽입.
  - `write_audit_log(action="recipes_imported")`.
- `ink_name` 은 폐기 개념이지만 NOT NULL 이라, 없으면 반제품명으로 대체 저장
  (`recipe_import_routes.py:318-320`).

---

## 2. 수정 등록 (revision) — 승계와 개정 보호

**시나리오:** 기존 레시피를 현황/조회에서 "수정 등록"으로 불러와 값 일부를 고쳐 새 버전으로 등록.

### 2.1 불러오기(프리필)
- 진입: `recipe-history.js` 의 `.history-edit-btn` 또는 `recipe-lookup.js`
  `handleLookupClone()` → `recipe-edit-loader.js` `loadRecipeForEdit()`.
- `GET /recipes/{id}/detail` 응답의 `detail.tsv` 를 `loadFromTsvRows()` 로 편집기에 로드.
  detail 라우터(`recipe_operator_routes.py:193 recipe_detail`)가 자재 사이 `설명` 열을
  원위치에 끼워 TSV 를 재구성하므로 **공정 설명 왕복 보존**(`recipe_operator_routes.py:242-257`).
- `state.pendingRevisionOf = recipeId` 설정.
- **승계 프리필**(편집 로더가 화면에 미리 채움): 기준 배합량, 기준 자재(`imp-anchor`),
  허용 편차(`imp-tolerance`), 품목코드(`imp-product-code`), 반응기(`imp-use-reactor`),
  파생(`imp-is-derived`), 1차 레시피(`imp-stage1`) — `recipe-edit-loader.js:48-101`.
  빈 칸으로 두면 서버가 부모 값을 자동 승계한다.

### 2.2 서버 승계 로직 (`recipe_import_routes.py:141-338`)
`body.revision_of` 가 있으면 부모 행을 조회(`:166-175`)해 아래를 승계한다. **명시 값 우선,
없으면 부모 승계, (비개정 신규면 기본값).**

| 속성 | 컬럼 | 명시 필드 | 미지정 시 |
|------|------|-----------|-----------|
| DHR 전용 | `is_dhr` | (없음 — 체인 전체 토글) | 부모 값 승계(`:177`) |
| 기준 배합량 | `base_totals` | `base_totals`/`base_total` | 부모 `base_totals`→`base_total`(`:178-184`) |
| 기준 자재 | `anchor_material_id` | `anchor_material`(이름) | 부모 id 가 **새 버전 자재에 여전히 있을 때만** 승계, 아니면 NULL(`:277-283`) |
| 허용 편차 | `tolerance_g` | `tolerance_g` | 부모 값 승계(`:206-209`) |
| 분류 | `category` | (없음 — PUT 로 변경) | 부모 값 승계(`:192-193`) |
| 품목코드 | `product_code` | `product_code`(명시) | 마스터 매칭>부모 승계(`:294-297`) |
| 반응기 | `use_reactor` | `use_reactor` | 부모 값 승계(`:196,201`) |
| 파생 | `is_derived` | `is_derived` | 부모 값 승계(`:197,203`) |
| 1차 연계 | `stage1_recipe_id` | `stage1_recipe_id` | 부모 값 승계(`:198-205`) |

- 기준 자재 명시 시: 임포트 항목 중 `materials.name` 정확 일치가 없으면 400
  (`recipe_import_routes.py:268-275`).

### 2.3 개정 시 배합 화면 보호 (409)
- 현재 버전(tip) 판정의 단일 소스는 `recipe_helpers.SUPERSEDED_RECIPE_IDS_SQL` +
  `resolve_chain_tip()`(`recipe_helpers.py:77-110`). 규칙: **활성(비취소·비초안) 후손이
  하나라도 있는 조상은 숨긴다**(전이적). 감사 F-4 회귀(중간만 취소 시 조상+말단 동시 노출)
  대응.
- 배합 저장(`blend_routes.py:560, 908`): 화면이 들고 있던 `recipe_id` 가
  `resolve_chain_tip()` 결과와 다르면 **409 "레시피가 개정되었습니다"** 로 저장 거부 —
  옛 배합비가 DHR 에 실리는 것을 막는다.
- 이론량/비율은 저장 시 **서버가 레시피에서 직접 산출**(`blend_service.derive_details_from_recipe`,
  감사 F-5) — 클라이언트가 보낸 비율은 버린다. 기준 자재가 있으면 그 실측값으로 총량을
  역산(2자리 반올림)한다(`blend_service.py:929-943`).

---

## 3. 품목코드 (명시 > 자동인식 > 승계)

**우선순위**(register 경로, `recipe_import_routes.py:285-297`):
1. **명시**(`body.product_code`) — 최우선. `_normalize_explicit_product_code()` 로
   strip+upper + 형식 검사(`^[A-Z]{1,2}[A-Z0-9]{2,8}$`), 불일치 400(`recipe_import_routes.py:27-48`).
   **명시 코드는 반제품이 정확히 1개인 임포트에서만 허용**(값행 2개 이상이면 400 — BUG 1 §9).
2. **자동 인식** — `_resolve_product_code(product_name)` 가 `item_code_master`
   `kind='product'` **단일 히트**(정확명 → 정규화 폴백) 로 코드 + `category_hint` 반환
   (`recipe_import_routes.py:51-81`).
3. **승계** — 수정 등록 시 부모 `product_code`(위 둘이 없을 때).

### 3.1 자기 체인 제외 규칙 (충돌 검사)
- 명시 코드가 있으면 `recipes.product_code` 충돌 조회. **개정 체인은 같은 코드를 공유**하므로,
  `_revision_chain_ids(connection, revision_of)` 로 얻은 체인 id 를 `NOT IN` 으로 제외한다
  (BUG 1 회귀 방지 — 자기 부모 코드로 자신이 409 되는 것 차단, `recipe_import_routes.py:216-238`).
- `_revision_chain_ids`(`item_code_routes.py:84-119`): `revision_of` 를 루트까지
  visited-set 순환 가드로 올라간 뒤, 재귀 CTE 로 전체 자손 수집.

### 3.2 현황 인라인 지정 + 화면 편집
- 현황 표의 품목코드 셀은 **표시 전용**(`recipe-history.js:106 productCodeCell`) — 인라인
  편집은 등록/수정 탭으로 이관(code-edit-relocate §1).
- 직접 지정 API: `PUT /recipes/{id}/product-code`(`item_code_routes.py:358 set_recipe_product_code`)
  — 체인 전체에 적용, 타 체인 충돌 시 409(반제품명 포함).
- 자재 코드: `PUT /materials/{id}/code`(`item_code_routes.py:261`) — `force=true` 면 기존
  보유 자재(비활성 포함)에서 코드를 빼 이동(`material_code_cleared` audit).

### 3.3 마스터 manual 동기화
- 화면에서 새 코드를 부여하면 `_ensure_master_entry()`(`item_code_routes.py:127`)가
  `item_code_master` 에 `source='manual'` 행을 **`INSERT OR IGNORE`** 로 보충 — ERP Excel
  재임포트 없이도 제안(검색)에 노출. ERP 데이터가 권위라 기존 코드는 덮지 않는다.

---

## 4. 2단 레시피 (1차 중간체 → 2차 최종)

- **관례:** 반제품명 접미 "-1" = 중간체(1차). 1차→2차는 명시적 링크
  `recipes.stage1_recipe_id` 로 연결(9dbb649 커밋).
- **파서의 1차 자재 인식**(`import_parser.py:181-189, 327-338`): 2차 레시피가 자재로 쓰는
  이름이 마스터에 없어도, `status='completed'` 레시피의 `product_name`(정규화) 집합에
  있으면 **정상 자재로 인식**(unknown 차단 우회). 코드 없이 자동 등록 + level-3 안내,
  `material_matches` status=`"recipe"`.
- **가족 묶음(현황):** `recipe-history.js:163-188` — 2차(`stage1RecipeId` 지정)와 그 1차를
  인접 그룹으로 묶어 `◆ … 2단 제조 가족` 헤더 + `1차`/`2차` 핀 표시. `byId` 맵 + `emitted`
  집합으로 중복 방지.
- **1차 인라인 지정:** 현황의 `.recipe-stage1-select`(`recipe-history.js:290`) 가 개정 없이
  `PUT /recipes/{id}/stage1`(`recipe_manager_routes.py:314`) 호출. 자기 자신 지정은 400,
  대상 존재 검증. (경로/포커스 지연 로드로 N² DOM 회피.)
- **파생(is_derived)과의 관계:** 파생은 **반응기 이월(carry-over)** 허용 여부를 결정
  (`use_reactor` 와 독립). `blend_service.enforce_carry_over`(`blend_service.py:730`)는
  이월 행이 (1) 파생 레시피이고 (2) 기준 자재 행이며 (3) 그 LOT 가 완료된 1차 배합
  기록에 존재해야만 통과시키고, `actual_amount` 를 1차 총량으로 **강제 덮어씀**(변조 방지).
  즉 `stage1_recipe_id`(현황 묶음/자재 인식)와 `is_derived`(이월 계량)는 별개 축이다.

---

## 5. 상태 모델 · 삭제 · DHR 전용

### 5.1 상태 모델
- `recipes.status` CHECK: `pending|in_progress|completed|canceled|draft`(`schema.py:48`).
- **등록 즉시 `completed`**(사용중). 구 계량 워크플로의 pending→진행→완료 단계는 `/blend`
  전환으로 폐기(승인 단계 없음 — pending 은 영구 정체됨, `recipe_import_routes.py:304`).
- `PATCH /recipes/{id}/status`(`recipe_operator_routes.py:495`): `cancel` 은 pending/
  in_progress/completed 어디서든 허용 → `canceled`. `complete`/`start` 는 레거시 데이터용
  잔존. 잘못된 전이는 409 `INVALID_STATUS_TRANSITION`.
- 취소(canceled)는 tip 판정에서 건너뜀 — 전부 취소면 최신본이 현재로(`recipe_operator_routes.py:275-276`).

### 5.2 삭제 규칙 (`record_delete_service.py:19 delete_recipe`)
- `DELETE /recipes/{id}`(`recipe_manager_routes.py:25`), `delete_blend_records`(쿼리) 로
  연결 배합 기록 동반 삭제 여부 선택.
- 연결 배합 기록: `delete_blend_records=false` → `blend_records.recipe_id=NULL`(기록 보존),
  `true` → `delete_blend_record()` 로 함께 삭제(점도 링크 NULL, blend_details 삭제).
- **개정 체인 처리(BUG 2 수정, 2026-07-22):** 삭제 대상의 자식들을 **조부모(삭제 대상의
  `revision_of`)로 재연결**한다(`UPDATE recipes SET revision_of = <grandparent> WHERE
  revision_of = ?`, `record_delete_service.py`). `v1→v2→v3` 에서 v2 삭제 시 v3.revision_of=v1
  로 이어져 계보가 유지된다. 삭제 대상이 루트(revision_of IS NULL)면 자식들이 루트로 승격
  (기존 동작 보존). 재연결한 자식 id 는 감사 로그 `relinked_child_ids` 에 기록.
- **stage1 참조 정리(GAP 4 수정):** 삭제 대상을 `stage1_recipe_id` 로 참조하던 2차 레시피들의
  링크를 NULL 로 정리하고, 정리한 id 를 감사 로그 `stage1_cleared_recipe_ids` 에 기록한다.

### 5.3 DHR 전용
- `PATCH /recipes/{id}/dhr`(`recipe_operator_routes.py:158`): **체인 전체**를 함께
  지정/해제(`find_chain_root`→`fetch_chain`) — 한 버전만 바꾸면 옛 버전이 일반으로 남아
  배합 화면에 노출되므로.
- 일반 조회/배합 선택은 `COALESCE(is_dhr,0)=0` 만(`recipe_operator_routes.py:99,123`),
  DHR 전용은 `dhr=true` 로 분리 조회. 배합일지 변경본(인허가) 전용.

---

## 6. 분류 체계 (약품/합성/잉크/용수)

- 허용값: `{"약품","합성","잉크","용수"}` — `PUT /recipes/{id}/category`
  (`recipe_manager_routes.py:181-226`)에서 강제, 그 외/빈값은 null(미분류).
- 지정 경로: 현황 인라인 드롭다운(`recipe-history.js:94 categoryCell`, 책임자만),
  조회 탭 기준자재 패널(`recipe-lookup.js:306 handleSaveCategory`), 등록/수정 승계.
- **화면 필터 연동:** 배합·이어서계량 화면의 2단계 선택(분류→레시피)에 쓰인다
  (`recipe-lookup.js:143` 주석). 현황 표는 분류 컬럼을 렌더.
- 수정 등록 때마다 분류가 미분류로 리셋되던 문제는 부모 `category` 승계로 해결
  (2026-07-16, `recipe_import_routes.py:151-153,192`).

---

## 7. 자재 자동 등록 · 별칭 · 삭제

- **자동 등록:** `import_parser._auto_register_material`(`import_parser.py:90`) —
  `unit_type='weight', unit='g', color_group='none', category='미분류', is_active=1`,
  마스터 매칭 시 `code` 부여. 화면 신규 등록(`POST /materials`,
  `item_code_routes.py:435`)도 동일 기본값.
- **별칭:** `material_aliases`(FK `ON DELETE CASCADE`). 파서·`GET /materials` 가 별칭을
  정규화 토큰에 함께 매핑(`import_parser.py:174`, `recipe_operator_routes.py:78-88`).
- **삭제(`DELETE /materials/{id}`, `item_code_routes.py:531`):**
  - `recipe_items` 가 한 건이라도 참조하면 **409**(참조 레시피명 최대 5개 표시) — 비활성화가
    아니라 명시적으로 운영자에게 정리를 맡김.
  - 참조 0 이면 `blend_details.material_id=NULL`(기록의 이름·수치 보존), `material_aliases`
    CASCADE, `materials` 삭제.

---

## 8. 승계 속성 총정리 (수정 등록)

승계 우선순위는 모두 **명시 값 > 부모 승계 > (비개정 신규 기본값)**. 예외/함정:

- `anchor_material_id`: 부모 id 가 **새 버전 자재 집합(`item_id_set`)에 여전히 있을 때만**
  승계 — 자재가 바뀌었으면 조용히 NULL(GAP 5).
- `product_code`: 명시 > 마스터 자동 매칭 > 부모 승계 (셋 중 하나).
- `category`: 부모 승계가 마스터 `category_hint` 보다 우선. 부모/승계 없을 때만 hint 채움.
- `is_dhr`: 부모 값 승계(체인 전체 토글이라 부모=자식 동일이 정상).
- `base_totals`: 정수는 `.0` 없이 문자열 저장(`recipe_import_routes.py:240-243`).

---

## 9. 발견된 결함/갭 (보고용 — 코드 수정 없음)

심각도 표기: **BUG**(잘못된 동작) · **GAP**(설계 공백/미방어) · **POLISH**(개선 여지).

### BUG 1 — 다중 반제품 임포트 + 명시 품목코드 = 전 행 동일 코드 ✅ 수정(2026-07-22)
`recipe_import_routes.py`. (종전) `explicit_product_code` 는 루프 **밖에서 한 번** 계산돼
루프의 **모든 행**이 `effective_product_code = explicit_product_code` 를 받아, raw 폴백으로
값행 2개 이상(서로 다른 반제품)을 한 번에 임포트하며 명시 코드를 넣으면 서로 다른 반제품이
같은 코드로 등록됐다.
**수정 정책:** (1) 명시 `product_code` 는 **반제품이 정확히 1개일 때만** 허용 — 값행 2개
이상이면 400 `"여러 반제품을 한 번에 등록할 때는 품목코드를 비워 두세요(자동 인식/개별 지정)."`
(가장 단순한 올바른 정책 — 다중 반제품은 자동 인식/개별 지정으로 유도). (2) 배치 내부 중복
가드: 같은 임포트 안에서 서로 다른 반제품이 같은 유효 코드로 귀결되면(예: 수정 등록이 부모
코드를 여러 행에 승계) 두 반제품명을 모두 밝히며 400. DB 충돌 검사가 못 보는 형제 행을 막는다.

### BUG 2 / GAP — 레시피 삭제가 개정 체인을 끊는다(고아 개정) ✅ 수정(2026-07-22)
`record_delete_service.py`. (종전) `UPDATE recipes SET revision_of = NULL WHERE revision_of = ?`
가 자식들을 루트로 승격시켜, `v1→v2→v3` 에서 v2 삭제 시 v3 가 독립 루트가 되고 v1 과의
계보가 끊겼다.
**수정:** 삭제 대상의 자식들을 **조부모(삭제 대상의 `revision_of`)로 재연결**한다 — v2 삭제 시
v3.revision_of=v1. 삭제 대상이 루트면 종전대로 자식이 루트로 승격(NULL). 재연결한 자식 id 는
감사 로그(`relinked_child_ids`)에 남긴다. `revision_of` FK 부재는 그대로이나 앱 로직이 계보를
보존한다. (§5.2 참조.)

### GAP 1 — `allow_unknown_materials` 죽은 분기 제거 ✅ 수정(2026-07-23)
(종전) `static/js` 어디서도 `allow_unknown_materials` 를 전송하지 않아(`previewImport`/
`importRecipes`/`importWithAnchor` 본문 확인) 파서의 "마스터에 없는 품목(확인 후 등록)"
override 분기가 **UI 에서 도달 불가능**했다(죽은 코드).
**수정:** `ImportRequest.allow_unknown_materials` 본문 필드, 라우트 인자, `parse_import_text`
의 `allow_unknown_materials` 파라미터와 해당 분기를 모두 제거했다. 이제 `item_code_master`
가 적재된 운영 환경에서 진짜 신규 자재(마스터·기존·완료레시피 어디에도 없음)는 **항상
errors 로 차단**되는 것이 유일한 경로다. 운영자는 임포트로 신규 자재를 넣을 수 없고, 먼저
품목코드 탭(`POST /materials`)이나 마스터에 등록해야 한다(의도된 정책). 마스터 0행 하위호환
모드(코드 없이 자동 등록)와 자체 제조 1차 반제품 연계 인식은 그대로 유지된다.

### GAP 2 — 동시 수정 등록 레이스(체인 분기) ✅ 수정(2026-07-22)
(종전) 두 책임자가 같은 v1 을 불러와 각자 수정 등록하면 v2a·v2b 가 모두 `revision_of=v1` 로
생성돼 체인이 조용히 분기했다(개정 등록에 낙관적 잠금 없음).
**수정:** 등록 트랜잭션 안에서 `revision_of` 계산 직후, 부모가 **여전히 체인 tip 인지**
검사한다(`resolve_chain_tip(parent) == parent`). 이미 개정돼 tip 이 이동했으면 409
`"레시피가 방금 개정되었습니다 — 새로고침 후 최신 버전에서 다시 수정 등록하세요."` (배합 저장
409 와 동일한 낙관적 잠금 규칙, 단일 소스 `resolve_chain_tip`). 첫 개정만 성공하고 두 번째는
최신본에서 다시 시도하도록 유도돼 분기가 발생하지 않는다.

### GAP 3 — "현재 tip" 정의가 3곳에서 서로 다르게 구현됨
`recipe_helpers.py` 주석은 SUPERSEDED 규칙이 목록·배합·귀결의 **단일 소스**라고 하지만
실제 구현은 셋이다:
1. 목록/조회: `SUPERSEDED_RECIPE_IDS_SQL`(조상 숨김 — 분기 시 다중 tip 가능).
2. 배합 저장 귀결: `resolve_chain_tip`(subtree 활성 `MAX(id)`).
3. 버전 이력: `recipe_history` 의 `current_id = max(active, key=(created_at,id))`
   (`recipe_operator_routes.py:275-276`).
선형 체인에선 일치하나, 분기(GAP 2)나 `created_at` 과 `id` 정렬이 어긋나는 경우
(예: 백필/임포트로 created_at 역전) **is_current 표시와 실제 저장 귀결이 불일치**할 수 있다.

### GAP 4 — `stage1_recipe_id` 의 참조 무결성 부재 ✅ 수정(2026-07-22)
FK/ON DELETE 는 여전히 없으나(스키마 불변) 앱 로직으로 무결성을 방어한다:
- **삭제 정리:** 삭제 대상을 `stage1_recipe_id` 로 참조하던 2차들의 링크를 NULL 로 정리하고
  감사 로그(`stage1_cleared_recipe_ids`)에 기록(`record_delete_service.py`, §5.2). 댕글링 제거.
- **등록/승계 시 존재 검증:** 명시·승계된 `stage1_recipe_id` 가 실제 레시피가 아니면 400
  `"지정한 1차 레시피를 찾을 수 없습니다."`(`recipe_import_routes.py`).
- **순환 차단:** `PUT /stage1`(`recipe_manager_routes.py`)은 자기 자신(400)·미존재(400)에 더해
  **A↔B 상호 지정(2노드 순환)·더 긴 순환**을 유한 걸음(visited-set + 깊이 상한 50)으로
  검출해 400 `"1차 연계가 순환됩니다 …"`. (대상이 실제 1차인지·이미 2차인지 같은 의미적
  검증은 여전히 미적용 — POLISH 여지로 남김.)

### GAP 5 — 승계된 category/hint 가 허용값 검증을 우회
register 는 `matched_hint`(마스터 `category_hint`) 또는 부모 `category` 를 그대로 저장한다
(`recipe_import_routes.py:298-302`). `PUT /category` 의 `ALLOWED={"약품","합성","잉크","용수"}`
검증을 **거치지 않으므로**, 마스터 `category_hint` 에 그 밖의 값(예: "기타")이 있으면
`recipes.category` 에 비허용값이 들어가고, 현황/패널 드롭다운엔 해당 옵션이 없어 편집으로
정상화하기 애매해진다.

### GAP 6 — 기준 자재(anchor) 승계 무음 소실
`recipe_import_routes.py:277-283`. 수정 등록 시 부모 anchor 의 `material_id` 가 새 버전
자재 집합에 없으면(자재 삭제 후 재생성으로 id 가 바뀐 경우 등) 조용히 NULL 로 떨어진다.
`tolerance_g`/`category` 는 무조건 승계되는데 anchor 만 id 멤버십에 의존 — 사용자에게 "기준
자재가 풀렸다"는 신호가 없다.

### POLISH 1 — `product_code` DB UNIQUE 부재(앱 레벨 방어에만 의존)
`recipes.product_code` 는 개정 체인이 코드를 공유해 UNIQUE 를 걸 수 없다(설계상 의도,
`item_code_routes.py:10`). 그러나 그 결과 BUG 1·동시성으로 **타 체인 간 코드 중복이 발생하면
DB 가 못 막는다**. 부분 유니크(체인 루트 기준) 같은 보강은 스키마상 어렵다는 점만 확인.

### POLISH 2 — preview↔register 자재 매칭의 시점 의존
`completed_recipe_names`/`master_index` 는 파싱 시점에 읽힌다(`import_parser.py:178-189`).
preview 와 register 사이에 레시피 완료/취소나 마스터 변경이 있으면 unknown/recipe 판정이
달라질 수 있다(희귀). register 가 재파싱하므로 최종 판정은 register 기준이 맞다 — 다만
preview 에서 "등록 가능"으로 본 것이 register 에서 400 날 수 있음.

---

## 검증하지 못한 항목(unverifiable)
- 실제 `item_code_master` 적재 상태(운영 DB) — 코드 경로만 확인, 데이터 실측 안 함(개발
  PC ≠ 운영 PC 정책).
- BUG 1/GAP 2 의 실제 재현은 런타임 테스트가 필요(본 검토는 정적 코드 분석). pytest·서버
  실행은 태스크 범위 밖.
- `templates/management.html` 의 탭 전환·`ctx` 조립(management.js 컨트롤러)은 개별 모듈
  계약만 확인, 전체 부팅 순서는 미추적.
