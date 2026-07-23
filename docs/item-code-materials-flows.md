# 품목코드·자재 도메인 흐름 (BRM 배합·레시피)

> 대상: ERP 품목코드(item-code P1~P6)와 자재 마스터의 수명주기를 코드 근거로 정리한다.
> 운영 절차서(`docs/ops-item-code-migration.md`)가 **무엇을 언제 실행하는가**를 다룬다면,
> 이 문서는 **왜/어떻게 동작하는가**(코드 체계·우선순위·수명주기·규칙 충돌)를 다룬다. 절차
> 반복 설명은 하지 않고 그 문서를 참조한다.
>
> 코드 경로: `src/routers/item_code_routes.py`, `src/routers/public_material_usage_routes.py`,
> `src/services/import_parser.py`, `src/services/blend_service.py`, `src/db/migrations.py`,
> `src/db/queries.py`, `tools/import_item_codes.py`, `tools/match_item_codes.py`,
> `tools/apply_manual_item_codes.py`, `static/js/management/item-codes.js`,
> `static/js/management/bom-editor.js`
>
> 각 절은 `파일:함수`(또는 `파일:line`) 로 근거를 표기한다. 마지막 절(8)은 검토 중 발견한
> 결함/갭 목록이다(코드 수정 없음 — 보고용).
>
> 용어 주의: 이 도메인에서 **분류(category)** 의 데이터값에 `잉크` 가 포함된다(레시피 속성값·
> ERP 제품구분 매핑값이지 UI 카피가 아니다). CLAUDE.md 의 "UI 잉크 금지" 규칙과 별개다.

---

## 1. 코드 체계

### 1.1 세 개의 저장소 (`migrations.py:136-161`)

| 대상 | 컬럼/테이블 | 제약 | 의미 |
|---|---|---|---|
| 자재 | `materials.code` | 부분 UNIQUE (`idx_materials_code … WHERE code IS NOT NULL`) | 자재별 ERP 품목코드. NULL=미부여 |
| 반제품 | `recipes.product_code` | **UNIQUE 아님** | 개정 체인이 같은 코드를 공유하므로 의도적으로 비유니크 |
| 마스터 | `item_code_master` | `code` PK, `kind IN ('material','product')` CHECK | ERP 품목 마스터(제안·검증 소스) |

- `materials.code`·`recipes.product_code` 는 `ensure_column` 으로 나중에 추가된 nullable 컬럼 —
  NULL 이 기본이라 미이관 환경에서 기존 동작이 그대로 유지된다(하위호환).
- 부분 UNIQUE 인덱스 덕에 자재는 코드 미부여(NULL)를 여러 개 가질 수 있으나 부여된 코드는
  전 자재에서 유일하다(`test_item_code_master.py::test_materials_code_allows_multiple_nulls`,
  `::test_materials_code_unique_on_duplicate`).
- `item_code_master` 컬럼: `code, name, spec, unit, kind, category_hint, source, imported_at`
  (`migrations.py:146-156`). `source` 로 임포트분(파일명 `code`/`code2`…)과 화면 낱개(`manual`)를
  구분한다.

### 1.2 원재료 코드 형식 완화 경위 (`item_code_routes.py:36`)

정규식 `_PRODUCT_CODE_PATTERN = ^[A-Z]{1,2}[A-Z0-9]{2,8}$` 이 자재·반제품 공통이다.

- 본래 자재 코드는 영문 **2자** 접두(AC0101 등)였다.
- 반제품(PB/B0020 등 **B 단독** 접두)이 자재로도 쓰이면서, B-계열 단일 접두 코드가 자재에
  부여될 수 있어 영문 **1~2자** 로 완화했다(`_validate_code` docstring, `:39-61`; BUG 3).
- 반제품 인라인 지정도 같은 이유로 `_validate_product_code`(영문 1~2자)로 완화 — 종전에는
  자재용 2자 패턴을 써 `B0082`/`BC`/`BW` 가 400 으로 거절됐다(BUG 2,
  `test_item_code_admin.py::test_recipe_product_code_accepts_single_letter_prefix`).
- `_validate_code`(자재)와 `_validate_product_code`(반제품)는 **현재 완전히 동일한 정규식**을
  쓴다. 의미론적 구분과 향후 패턴 분리 여지 때문에 별개 함수로 둔 것(`:64-81` docstring).
- 마스터 존재 여부는 강제하지 않는다 — 운영자 직접 입력을 허용(`:35` 주석).

### 1.3 마스터 kind 와 제품구분 매핑

- `kind='material'`: `code.xlsx` 에서 **코드 접두 `AS/AC/AH/AW`** 인 행만(대분류 필터가 아니라
  코드 접두 기준 — `import_item_codes.py:99` `MATERIAL_CODE_PREFIXES`). 리허설에서 배합 자재
  20종이 대분류 '소모품'에 있어 대분류 필터로는 누락되기 때문(`:95-98` 주석).
- `kind='product'`: `code2~4.xlsx` 전 행. `category_hint` 는 제품구분→IRMS 분류로 매핑
  (`잉크코드→잉크`, `합성코드→합성`, `약품코드→약품`; 그 외 원문 유지 — `:43-47`,
  `import_product_master`).

---

## 2. 마스터 수명주기 — 엑셀 임포트 ↔ 화면 낱개

### 2.1 엑셀 임포트 (upsert) — `import_item_codes.py`

- `_upsert_master`(`:73`)는 `INSERT … ON CONFLICT(code) DO UPDATE` — 같은 코드 재임포트 시
  name/spec/unit/kind/category_hint/source/imported_at 를 **전부 갱신**한다. 행 수는 불변
  (`test_item_code_master.py::test_material_upsert_updates_name_and_keeps_row_count`).
- 정규화: 코드는 `strip().upper()`(`_norm_code`, `:50`), 이름/규격은 `strip` 만(`_norm_text`).
- `_open_target_db`(`match_item_codes.py:51`)를 공유 — 비관례 파일명(rehearsal.db 등)도 **그 파일
  연결에 직접** `apply_schema_migrations` 를 적용한다. 과거 `init_db()` 가 관례 DB(irms.db)에만
  스키마를 잡아 대상에 `item_code_master` 가 누락되거나 관례 DB 가 오염되던 버그를 고친 것
  (`import_item_codes.py:32-37`, `test_item_code_master.py::test_import_db_arbitrary_filename_…`).

### 2.2 화면 낱개 (manual, ERP 우선 원칙) — `_ensure_master_entry` (`item_code_routes.py:127`)

운영자가 화면에서 새 코드를 부여하면 `INSERT OR IGNORE INTO item_code_master (… source='manual',
spec/unit/category_hint=NULL …)` 로 마스터 행을 보충한다. ERP Excel 재임포트 없이도 제안 검색(A1)에
노출시키기 위함.

- **ERP 권위(authoritative)**: `INSERT OR IGNORE` 이므로 이미 있는 코드(ERP 임포트분 포함)는
  건드리지 않는다. 운영자 입력이 기존 name/source/category_hint 를 덮지 않는다
  (`test_material_create.py::test_existing_master_row_not_modified_on_assign`).
- 마이그 전 DB(테이블 없음)에서는 `OperationalError` 를 조용히 무시(500 방지) — 전 조회/부여
  경로가 같은 방어 패턴(`search_item_code_master :198`, `_material_code_map :205`).
- 호출처: A3 `set_material_code`(`:319`), A4 `set_recipe_product_code`(`:403`),
  A6 `create_material`(`:502`). 모두 코드가 non-NULL 일 때만.

---

## 3. 자재 수명주기

### 3.1 자동 등록 (임포트 미리보기) — `import_parser` (`import_parser.py:294-375`)

레시피 등록 미리보기에서 헤더(자재명)를 3단으로 판정한다. `_load_master_index`(`:14`)가
`item_code_master` 를 kind 별 `normalize_token(name) → [code]` 로 인덱스화하되, **마스터 0행이면
None → 하위호환 모드**(차단 없음, 코드 없이 자동 등록).

- **existing**: 기존 `materials` 에 있음 → 기존 id, 있으면 code 함께 표시(`:377`).
- **master**: 마스터에만 존재 → 1순위 `material`, 없으면 2순위 `product`(반제품→원료, PB→B0020)에서
  **단일 히트**면 `_auto_register_material(name, code=master_code)` 로 코드까지 부여(`:298-320`).
  다중 히트는 unknown 취급.
- **unknown**: 어디에도 없음. 단, 우리가 만드는 1차 반제품(completed 레시피 `product_name`)이면
  코드 없이 정상 인식(2단계 레시피 연계, `:324-338`). 그 외엔 유사 후보 안내 + 코드 없이 자동 등록.

`_auto_register_material`(`:90`) 기본값: `unit_type='weight', unit='g', color_group='none',
category='미분류', is_active=1`.

### 3.2 화면 신규 등록 — A6 `POST /materials` (`item_code_routes.py:435`)

품목코드 관리 화면 상단 빠른 지정에서 미등록 자재명을 입력하면 새 자재로 등록
(`item-codes.js:quickAssign :117`, 미등록이면 confirm 후 POST).

- 자재명 중복은 **대소문자 무시**(`lower(name)=lower(?)`, `:450`) → 409.
- INSERT 기본값은 `_auto_register_material` 과 **동일**(`:491-497`) — 화면 생성 자재가 임포트
  생성 자재와 같게 취급되도록(`test_material_create.py::…defaults`).
- 코드는 있어도/없어도 등록 가능. 있으면 `_validate_code` 경유 + 충돌 시 A3 과 동일 규칙(409,
  `force=true` 로 이동).

### 3.3 별칭 (`material_aliases`)

- 매칭 키로 쓰인다: 자동 매칭(`match_item_codes._load_materials_targets :138`)과 임포트
  인덱스(`import_parser :148-175`) 모두 `normalize_token(name)` + 각 `normalize_token(alias)` 를
  키로 삼는다. 본명이 마스터에 없어도 별칭이 맞으면 코드가 붙는다
  (`test_match_item_codes.py::test_material_alias_match`).
- FK `ON DELETE CASCADE` — 자재 삭제 시 별칭 자동 제거(`item_code_routes.py:577`,
  `test_item_code_admin.py::test_material_delete_no_references_succeeds`).

### 3.4 삭제 (참조 시 409) — A5 `DELETE /materials/{id}` (`item_code_routes.py:531`)

`tools/apply_manual_item_codes.py` 의 DELETE_PLAIN 과 동일 정책.

- `recipe_items` 가 한 건이라도 참조하면 **409** — 참조 반제품명 최대 5개를 detail 로 노출
  (`:544-562`). 비활성화로 대체하지 않고 운영자에게 명시적으로 맡긴다.
- 참조 0 이면: `blend_details.material_id` 를 NULL 로(기록의 이름·수치 텍스트는 보존), 별칭 CASCADE,
  `materials` 행 삭제. 삭제 시점 code 를 audit `details.code` 에 남긴다(`:579-592`).

### 3.5 비활성 (is_active=0)

- 과거 이력이 실재하는 자재(색소류 등)는 삭제 대신 비활성화(`apply_manual_item_codes.py:69`
  DEACTIVATE, `record` 참조가 남은 DELETE_PLAIN 대체 처리 `:138-143`).
- A2 목록(`list_materials_for_codes :218`)은 `is_active=1` 만 노출한다. 따라서 **비활성 자재가 코드를
  쥐고 있으면 화면 목록에 안 보여** 직접 해제할 수 없다 → 3.6 의 force 이동으로만 회수 가능.

### 3.6 코드 지정·수정·해제·옮기기(force)·감사

- **지정/수정/해제**: A3 `PUT /materials/{id}/code`(`:262`). code=null/빈문자열이면 해제(NULL 저장,
  `_validate_code :50-54`).
- **옮기기(force)**: 같은 코드를 다른(활성/**비활성 모두**) 자재가 쥐고 있을 때 `force=true` 면 한
  트랜잭션에서 기존 보유 자재의 code 를 NULL 로 빼고 대상에 부여(`:281-315`). 비활성 필터 없음 —
  3.5 의 회수 불가 사태를 해소(`test_material_set_code_force_moves_code_from_inactive_holder`).
  `force` 미지정/false 는 종전대로 409(`test_material_set_code_without_force_still_409`).
- **감사(audit)**: 이동 시 두 행 — 기존 보유에 `material_code_cleared`(`:298`), 대상에
  `material_code_set`(`:335`, `details.moved_from_name`). A6 생성 이동은 `material_code_cleared` +
  `material_created`(`:476`, `:504`). A5 삭제는 `material_deleted`(`:584`).
- UI: 409 를 받으면 `confirmMoveOn409`(`item-codes.js:541`)가 "사용 중인 코드" detail 일 때만
  confirm 후 `force:true` 재시도. POST 의 409 는 자재명 중복일 수도 있어 detail 문자열로 걸러낸다.

---

## 4. 반제품 코드 — 개정 체인 공유 규칙 (A4 `item_code_routes.py:359`)

- `PUT /recipes/{id}/product-code` 는 그 레시피가 속한 **개정 체인 전체**에 코드를 부여/해제한다.
- 체인 판정 `_revision_chain_ids`(`:84`): `revision_of` 를 루트까지 거슬러 올라간 뒤(순환 가드
  visited-set), 루트에서 재귀 CTE 로 자손 전체를 수집. 중간/루트 어디서 지정해도 동일 결과
  (`test_product_code_chain_update_from_middle`, `::…from_root_updates_chain`).
- 충돌: **다른 체인**의 레시피가 같은 `product_code` 를 쓰면 409(반제품명 포함, `:379-394`).
  같은 체인 재지정은 충돌 아님.
- 해제는 체인 전체 NULL(`test_product_code_clear_propagates_to_whole_chain`).
- 반제품 코드는 UNIQUE 가 아니므로(1.1) 체인 공유가 DB 제약과 충돌하지 않는다
  (`test_item_code_master.py::test_recipes_product_code_allows_duplicates`).

---

## 5. erp_code 우선순위 사슬 (상위 대시보드 소비 관점)

`GET /public/material-usage`(`public_material_usage_routes.py`)는 내부망 공개 API로 상위 재고
대시보드에 자재 불출량을 넘긴다. 각 항목의 `erp_code` 는 `blend_service._resolve_erp_code`
(`blend_service.py:237`)가 결정하며 우선순위(P4)는:

1. **`materials.code`** — 정식 ERP 품목코드(최우선). `_material_code_map`(`:~200`)이
   `normalize_token(자재명)→code` (대소문자/공백 변형에도 매칭; §8 GAP 해결).
2. **RM 별칭** — `material_aliases` 중 `RM` 으로 시작하는 별칭(`_erp_code_map :210`).
3. **RM 형태 저장 코드** — legacy `code` 인자(materials.category 였던 값)가 `RM…` 이면.
4. **RM 형태 자재명** — 이름 자체가 `RM…` 이면.
5. **비RM 별칭** — 그 외 별칭이라도 있으면(빈 행 skip 회피).

- 집계 자체는 `material_usage_periods`(`:268`)가 `blend_details` 텍스트(material_name/material_code)
  로 GROUP BY 하고, erp_code 는 사후에 매핑으로 덧붙인다. 즉 **erp_code 가 비어도 집계는 되며**,
  코드는 상위 재고 시스템의 매칭 키로만 쓰인다.
- `_material_code_map`·`_erp_code_map` 은 `code`/별칭 컬럼이 없는 구버전 DB 에서 `OperationalError`
  를 잡아 빈 dict 반환(`:205`, `:222`).

---

## 6. 임포트/매칭 도구 체인 (P1→P2→수동확정)

절차 순서·명령은 `docs/ops-item-code-migration.md` 참조. 여기서는 각 도구의 규칙만 정리.

- **P1 임포트**(`import_item_codes.py`): 마스터만 채운다. 자재/반제품 코드 부여는 하지 않음.
- **P2 자동 매칭**(`match_item_codes.py`): 보고서 우선. `--apply` 만 **확정(단일 히트)** 반영, 모호
  (2+)·미매칭은 항상 보고만.
  - 자재(`match_materials :159`): 1순위 material 마스터, 2순위 product 마스터(교차, `confirmed_cross`).
  - 레시피(`match_recipes :227`): 같은 `product_name` completed 행 전체에 부여. category NULL 이면
    hint 로 채움, 기존값≠hint 면 **분류 충돌**(덮지 않고 보고만, `:253-255`).
  - `apply_confirmed :332` 는 `code IS NULL`/`product_code IS NULL` 행만 대상 → 멱등
    (`test_match_item_codes.py::test_apply_idempotent_two_runs`). 같은 ERP 코드에 자재 2개가
    매칭되면 첫 자재만 부여, 나머지는 `code_conflicts` 로 skip(UNIQUE 위반 회피).
- **수동 확정본**(`apply_manual_item_codes.py`): 자동이 못 푼 것의 운영자 확정(2026-07-17).
  `CODE_FIXES`(약어 자재 코드 20종, `:30`), `DELETE_TYPOS`(오타 중복→정본, `:57`),
  `DELETE_PLAIN`(오등록 삭제, 참조 남으면 비활성 대체, `:63`), `DEACTIVATE`(색소류, `:69`).
  이미 코드 있으면 skip → 재실행 안전.

---

## 7. 남은 운영 항목

`docs/ops-item-code-migration.md` §4 및 코드 주석 기준 미결 항목:

- **PB-APB 보류**(`apply_manual_item_codes.py:13-14`): 정체 불명 — 어느 레시피가 쓰는지 운영에서
  확인 후 결정. 이 스크립트는 건드리지 않음.
- **분류 충돌 7건**(리허설): 기존 레시피 분류 ≠ 마스터 제품구분(hint). 자동으로 덮지 않으므로
  운영자가 어느 쪽이 맞는지 판단(`ops-item-code-migration.md:116-117`).
- **레시피 미매칭(변형명) 18종**: SBCT-1, N2-TOP, APB17 등. difflib 유사 후보만 뜨고 자동 확정은
  안 됨 → 현황 인라인 `1차 레시피`/product-code 지정으로 수동 처리(추후).

---

## 8. 검토 중 발견한 결함/갭 (코드 수정 없음 — 보고용)

### BUG — 코드 이동 이력이 신규 등록 경로에서 대상 id 를 잃음 — ✅ 해결(2026-07-22)
`item_code_routes.py` (A6 `create_material`, force 이동)
- 이제 기존 보유 자재의 code 를 NULL 로 비우는 UPDATE 는 그대로 INSERT 앞에 두되(부분 UNIQUE
  회피), `material_code_cleared` **audit 을 INSERT 뒤로 옮겨** `moved_to_material_id` 에 새 자재
  `new_id` 를 채운다. 이동 원본→대상을 id 로 추적 가능. 회귀 방지 `test_material_create.py::
  test_create_material_force_move_audit_carries_new_material_id`.
  (과거: INSERT 가 audit 뒤라 `moved_to_material_id: None` 이었다.)

### GAP — 마스터 orphan: 자재 삭제/해제 후 manual 행 잔존 — ✅ 해결(2026-07-22)
`item_code_routes.py` (A5 delete, A3 해제/이동) — 헬퍼 `_cleanup_orphan_master`
- 자재가 **삭제**(A5)되거나 코드가 **해제/다른 코드로 교체**(A3)되어 그 코드를
  `materials.code`·`recipes.product_code` 어디에서도 안 쓰게 되면, `source='manual'` 마스터 행을
  삭제한다. **ERP 임포트분(source != 'manual')은 권위 데이터라 건드리지 않는다.** force 이동은
  코드가 새 자재로 옮겨가 여전히 쓰이므로 정리 대상이 아니다. 마이그 전 DB 는 조용히 무시.
  회귀 방지 `test_material_create.py::test_delete_material_cleans_orphan_manual_master`,
  `::test_clear_material_code_cleans_manual_master_but_keeps_erp`,
  `::test_force_move_keeps_manual_master_since_code_still_used`.

### GAP — erp_code 해석 키가 정규화 불일치 — ✅ 해결(2026-07-22)
`blend_service.py` (`_material_code_map` ↔ `_resolve_erp_code`)
- `_material_code_map` 이 이제 `normalize_token(name)`(대문자화 + 공백·기호 제거)을 키로 만들고,
  `_resolve_erp_code` 도 `normalize_token(name)`으로 조회한다 — 마스터 매칭 전반과 같은 정규화.
  기록의 material_name 이 자재명과 대소문자/내부 공백만 달라도(`HEMA (Lotte)` vs `HEMA(Lotte)`)
  1순위 materials.code 매핑이 잡힌다. 회귀 방지 `test_blend_material_code.py::
  test_material_usage_periods_resolves_code_across_name_normalization`,
  `::test_material_code_map_keys_by_normalize_token`.
  (별칭 맵 `_erp_code_map` 은 이번 범위 밖 — 원문 name 키 유지.)

### GAP — 신규 등록 중복 검사가 별칭·정규화를 보지 않음
`item_code_routes.py:449-457` (A6 `create_material`)
- 중복 검사는 `lower(name)=lower(?)` 뿐. (1) 다른 자재의 **별칭**과 같은 이름을 새 자재로 등록하는
  것을 막지 못한다 — 임포트 매칭은 name+alias 를 `normalize_token` 동일 키로 보므로 토큰 충돌
  (한 쪽이 다른 쪽을 가림)을 만든다. (2) `lower()` 만으로는 `normalize_token` 이 같아지는 이름
  (`HEMA (Lotte)`/`HEMA(Lotte)`)을 서로 다르게 보아 둘 다 등록 가능 → 임포트 시 매칭 모호.
  → 중복 검사를 `normalize_token` 기준 + `material_aliases` 조회로 확장 권장.

### GAP — A4(반제품)는 product_name 이 아니라 revision_of 체인만 갱신
`item_code_routes.py:375` (`_revision_chain_ids`) ↔ `match_item_codes.py:357-368` (apply)
- 화면 A4 는 `revision_of` 그래프로만 전파한다. 그러나 자동 매칭 apply 는 **같은 `product_name`**
  전체에 부여한다. `revision_of` 링크 없이 같은 이름으로 독립 등록된 형제 레시피가 있으면, 화면
  지정은 그들을 빼놓아 "같은 반제품인데 일부만 코드 있음" 상태가 생긴다(두 경로의 전파 정의
  불일치). 1차/2차 링크(`stage1_recipe_id`)도 체인 판정에 고려되지 않는다.

### GAP — 도구/수동확정이 화면 코드 규칙·마스터 동기화를 우회
`apply_manual_item_codes.py:30-52` / `import_item_codes.py`
- (1) 임포트·수동확정은 코드 형식 검증(`_validate_code` 정규식)을 거치지 않는다. 예: `메탄올→BT000`,
  `Oligomer→BT0001`(마스터 4종 밖 코드, `:46-47`). 화면 정규식은 이들을 대체로 통과하지만, ERP/운영자
  코드가 `^[A-Z]{1,2}[A-Z0-9]{2,8}$` 를 벗어나면 그 자재의 코드를 **화면에서 재지정·수정할 때 400**
  이 된다(도구는 넣을 수 있는데 화면은 못 고침). (2) `apply_manual_item_codes.py` 는 직접 SQL
  UPDATE 라 `_ensure_master_entry` 를 호출하지 않는다 → BT000/BT0001 처럼 마스터 4종에 없는 코드는
  `item_code_master` 행이 생기지 않아 A1 제안·임포트 인식에 안 잡힌다.

### POLISH — normalize_token 이 한글을 제거하지 않음(주석과 실제 불일치) — ✅ 해결(2026-07-23)
`db/queries.py` `normalize_token`, `tests/test_match_item_codes.py:197-204`
- `normalize_token` 은 `str.isalnum()` 기반인데 파이썬에서 한글 음절은 `isalnum()==True` 라 **한글이
  보존된다**(검증: `'카본블랙'` → 비어있지 않은 토큰). `normalize_token` 에 이 전제를 명시하는
  docstring 을 추가하고, 사실과 다르던 테스트 주석("알파벳/숫자만 남으므로 빈 토큰"/"빈 문자열 →
  close 매칭 불가")을 바로잡았다 — 미매칭 사유는 "빈 토큰"이 아니라 한글 토큰이 영문 마스터 토큰과
  겹치지 않기 때문. 기능은 불변(주석·docstring 만 수정). 향후 한글 정규화(공백/괄호 차이) 도입 시
  이 전제를 먼저 바로잡아야 한다는 점도 docstring 에 남겼다. (tools/match_item_codes.py 소스 주석은
  이번 범위 밖 — 파일 미소유.)

### POLISH — 코드 이동 후 마스터 이름이 옛 자재명으로 고착 — ✅ 해결(2026-07-23)
`item_code_routes.py` (A3 `set_material_code`, A6 `create_material`) — 헬퍼 `_refresh_manual_master_name`
- force 이동 시 `_ensure_master_entry`(INSERT OR IGNORE)가 기존 마스터 행을 안 바꿔 `item_code_master.name`
  이 부여 당시 원 보유 자재명에 고착됐다. 이제 force 이동이 일어나면(A3·A6 모두) `source='manual'`
  마스터 행의 이름을 **새 보유 자재명으로 UPDATE** 한다. **ERP 임포트분(source != 'manual')은 권위
  데이터라 건드리지 않는다.** 마이그 전 DB 는 조용히 무시. 회귀 방지 `test_material_create.py::
  test_a3_force_move_refreshes_manual_master_name`, `::test_a6_force_move_refreshes_manual_master_name`,
  `::test_force_move_keeps_erp_master_name`.

---

## 검증 못 한 항목

- 상위(외부) 재고 대시보드가 `erp_code` 를 실제로 어떻게 소비/매칭하는지는 이 저장소 밖이라
  확인 불가. §5 는 IRMS 가 **내보내는** 쪽만 근거.
- `apply_manual_item_codes.py` 의 `CODE_FIXES` 코드값(AS0031 등)이 현재 운영 마스터에 실재하는지는
  운영 DB 접근이 없어 미검증(스크립트는 충돌/부재 시 skip·보고하도록 방어됨).
- 실제 ERP `code*.xlsx` 열 순서는 소스 파일이 저장소 밖(`data/master/`, gitignore)이라 코드가 가정한
  열 인덱스(대분류=7·중분류=8, 제품구분=6)만 근거로 함.
