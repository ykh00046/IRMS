# 시험 배합(test blend) 설계 · 구현 계약

작성 2026-09-18. 세 작업 패키지(A 백엔드 · B 배합 화면 · C 기록 조회+점도)가 이 문서를
공통 계약으로 삼는다. 여기 적힌 이름·파라미터·규칙을 바꾸려면 문서를 먼저 고친다.

## 1. 목적과 사용자 결정

시험 목적의 배합(새 배합 시도, 기존 레시피 양 조정, 새 원재료 추가)을 **정식 배합과 같은
통제(저울·LOT·편차·서명·수기·증량·폐기)로 기록**하되, 정식 생산 통계·알림을 오염하지
않게 격리한다.

사용자 결정(2026-09-18):

| 항목 | 결정 |
|---|---|
| 입력 방식 | 비율·총량이 아니라 **자재별 목표량(g) 직접 입력**. 총량 = 목표량 합 |
| 화면 | **주소는 따로(`/blend/test`), 코드는 배합 화면 하나**(같은 템플릿·같은 JS, 시험 표시만 켬). 별도 화면 복제 금지 |
| 자재 불출량(상위 재고 대시 공개 API) | **포함** (실제 소모) |
| 점도 | **등록 허용**. 시험 점도 기록 구분 필요. 알림·이상 통계·관리한계 계산에서는 **제외** |
| 배합일지(DHR Excel/PDF) | **시험 표기 없음**. 출력 경로 동일 |
| 기록 조회(/status) | 목록·상세에 '시험' 칩 + 시험만/시험 제외 필터 |
| 대시보드·배합 분석·LOT 이력·다음 LOT·트레이 알림 | **제외** |
| 레시피 초안 승격 | 만들지 않음(사람이 관리 화면에서 직접) |
| 시험 자재 (2차 결정) | **등록된 원재료만**. 품목마다 품목코드로 대조한다. 이름만으로 추가 금지(이름이 여러 개 생겨 맞지 않고 누락된다). 시험 제품 자체는 코드 대조 불필요 |
| 시험 점도 (2차 결정) | 반제품(PB 등)과 무관하게 **시험 LOT 단위로 기록**. 새 레시피 시험도 등록된다. 요약 숫자·통계·규격선은 보여 주지 않는다(비교 대상이 없을 수 있다) |
| 전체 Excel (2차 결정) | 화면 조건(시험 필터 포함)을 그대로 따른다. 기존 R-12 규칙과 같다 |

## 2. 데이터

- `blend_records.is_test INTEGER NOT NULL DEFAULT 0` (+ partial index `WHERE is_test = 1`)
- `blend_records.base_recipe_id INTEGER` (nullable, 불러온 레시피. FK 강제 없음 — 레시피가 개정·삭제돼도 기록은 남는다)
- `test_viscosity_readings` (시험 점도 전용, §9): `blend_record_id` UNIQUE, 시험 LOT 하나에 값 하나.
  정식 `viscosity_readings` 에는 시험 값이 들어가지 않는다(등록 시 거부). 따라서 정식 점도 통계는 시험을 몰라도 된다.
- `blend_details`는 그대로. 시험 행도 **등록된 자재만**: `material_id` 필수, `material_code`·`material_name` 은
  서버가 자재 마스터 값으로 채운다.

## 3. LOT 규칙

- 시험 product_lot = `"T-" + 시험명 + YYMMDD + 순번2자리`. 기존 `generate_product_lot`에
  제품명 자리를 `"T-" + 시험명`으로 넘기면 base 접두가 달라 정식 순번과 자동 분리된다.
- `/api/blend/next-lot?product=...&test=1` 은 위 규칙으로 미리보기.
- `product_name` 컬럼에는 시험명(접두 없음)을 저장한다.
- 시험명 안의 공백은 LOT 에서 제거한다("PB 점도 개선" → `T-PB점도개선26091801`). 손으로 옮겨 적는 값이라 공백이 섞이면 대조가 깨진다.

## 4. 저장 API (`POST /api/blend/records`) — 패키지 A

본문 추가 필드: `is_test: bool = False`, `base_recipe_id: int | None = None`.

`is_test=true` 일 때:

1. `recipe_id`는 서버가 **NULL 로 강제**(보내와도 무시). `base_recipe_id`는 있으면 recipes 에 존재해야 한다(없으면 400).
2. `details[*].theory_amount`가 **목표량**이다. 각 행 `theory_amount > 0` 필수. `material_id` 필수이고 자재 마스터에
   있고 사용 중이어야 한다(아니면 400). `material_code`·`material_name` 은 서버가 마스터 값으로 덮어쓴다.
   같은 자재가 여러 행이어도 된다(분할 계량).
3. `total_amount` = 서버가 `sum(theory_amount)` 로 산출(클라이언트 값 무시). `ratio` = `theory/total*100`
   (정식 경로와 같은 반올림 자릿수).
4. **건너뜀**: 반응기 필수 검사, `enforce_carry_over`, `resolve_chain_tip` 409, `derive_details_from_recipe`,
   `product_uses_reactor`. `reactor`는 None 으로 저장.
5. **유지**: 실제량 필수, 자재 LOT 필수, `collect_lot_acks`, 편차 검사(허용 편차 = `base_recipe_id`가
   있으면 그 레시피의 `tolerance_g`, 없으면 기본 0.05), `validate_rescale_events`, 폐기, 수기, 서명,
   멱등 request_id, 총량 상한, 감사 로그(action 에 시험 여부 details 로 남김).
6. 응답에 `is_test`, `base_recipe_id`, `product_lot` 포함.

`is_test=false` 경로는 **한 줄도 바뀌지 않는다**(기존 테스트 전부 통과가 증거).

## 5. 조회 API — 패키지 A

- `GET /api/blend/records` 에 `test: str = "all" | "only" | "exclude"` (기본 `all`). 잘못된 값은 422.
  `count_blend_records`도 같은 필터. 각 item 에 `is_test`, `base_recipe_id` 포함.
  시험 기록의 `viscosity_state`는 절대 `"missing"` 이 되지 않는다(기존 어휘 유지: 등록 있으면 `done`, 측정 불가면 `skipped`, 그 외 `na`).
- `GET /api/blend/records/{id}` 에 `is_test`, `base_recipe_id`, `base_recipe_name`(없으면 null).
- `GET /api/blend/recent-product-lots` : 기본 `is_test = 0` 만. `test=1` 이면 `is_test = 1` 만
  (시험 모드 2차 배합의 원료 LOT 제안은 시험 LOT 끼리).
- `GET /api/blend/records/export-all` (전체 Excel 백업): `시험` 열 추가, 목록과 같은 `test` 조건을 받는다(화면과 파일 일치).
- DHR Excel/PDF/zip/batch: 시험 기록도 그대로 출력. **표식 없음**. ratio 칸은 저장된 ratio.

## 6. 격리(is_test = 0 만) — 패키지 A

다음은 모두 `COALESCE(is_test,0) = 0` 조건을 붙인다(각 SQL 에 직접, 헬퍼 재사용 권장):

- `src/routers/dashboard_routes.py` 의 blend_records 집계 전부, `src/services/dashboard_export.py`
- `blend_service`: `material_usage`, `product_usage`, `mistake_stats`, `_analysis_core`/`analysis`, `batch_details`
  (= /insight 배합 분석 전체). `material_usage_periods`·`material_usage_details` 는 공개 자재 불출량 API 전용이라
  **포함**(`include_test=True` 기본) — /insight 에서 호출되지 않음을 테스트로 고정.
- `lot_history_service` (LOT 교체 타임라인·교체 목록)
- `trace_material_lot` (자재 LOT 역추적)은 **포함**하되 결과 행에 `is_test` 를 실어 준다(추적성).
- `public_material_usage_routes` (상위 재고 대시 자재 불출량)는 **포함** — 바꾸지 않는다. 테스트로 고정.
- `public_rescale_alert_routes`·`blend_rescale_ack_routes`(증량·수기 미확인 알림/확인)는 **포함** — 통제는 시험에도 적용.
- 점도 쪽 격리(`viscosity_service.daily_reading_reminders` pending_lots, 등록 대기열, 통계)는 패키지 C.

## 7. 페이지 — 패키지 A(라우트) / B(템플릿)

- `GET /blend/test` : `/blend` 와 같은 작업자 가드, `blend.html` 을 `test_mode=True` 로 렌더.
  `/blend`·`/blend/bulk` 는 `test_mode=False`.
- 템플릿은 `<body>`… 가 아니라 `#blend-entry-mode` 에 `data-test-mode="1"` 을 붙이고, JS 는 이 속성으로만 시험 여부를 판단한다.

## 8. 배합 화면(시험 모드) — 패키지 B

파일 소유: `templates/blend.html`, `templates/_base_app.html`(사이드바 '시험 배합' 링크, 배합 아래),
`static/js/blend.js`, `static/js/blend_lib.js`, `static/js/blend_drafts.js`, `static/js/blend_drafts_page.js`,
`templates/blend_drafts.html`, `static/css/blend.css`. **문구·색·간격은 `docs/ui-standard.md` 준수.**

시험 모드에서 바뀌는 것(정식 모드는 DOM·동작 모두 불변):

1. 제목 "시험 배합", 상단 띠 "시험 배합입니다. 정식 기록과 구분되어 저장됩니다."
2. **시험명** 입력칸(제품명 자리). 레시피를 불러오면 기본값 `"{레시피 product_name} 시험"`. 저장 시 `product_name`.
3. 레시피 선택은 **선택사항**. 고르면 그 레시피의 자재·이론량(기준 배합량 첫 값 기준, 기준 자재 레시피는
   value_weight 로 산출)을 목표량으로 채운 표가 된다. 안 고르면 빈 표. 두 경우 모두 표 편집 가능.
   레시피를 불러왔으면 `base_recipe_id` 를 기억한다.
4. 표 편집: **행 추가**(자재 검색 — `GET /api/materials` 의 code/name/aliases 로 찾기. **등록된 자재만** 추가된다.
   못 찾으면 "찾는 자재가 없으면 자재 관리에서 먼저 등록하세요." 안내), **행 삭제**, 이론량 칸이 **목표량 입력칸**. 총 배합량 칸은
   읽기 전용으로 목표량 합 표시. 비율 칸은 목표량/합 으로 실시간 표시.
5. 끔: 기준 자재 파생, 반응기 필드·이월 컨트롤, 기본량 버튼, 레시피 개정 409 처리. 분류 초기값 무관.
6. 유지: 저울 PRINT 입력·저울 대상 지정·나눠담기·추가 계량·LOT 검사(ERP·등록 여부)·편차·증량·폐기·수기
   승인·서명·저장 확인 창·유휴 로그아웃·창 중복 차단·자동 새로고침. 편차 문턱은 불러온 레시피의
   tolerance_g, 없으면 기본.
7. 저장 payload: `is_test: true`, `base_recipe_id`, `recipe_id: null`, `product_name: 시험명`,
   `details[*] = {material_id?, material_code?, material_name, theory_amount(목표량), actual_amount, material_lot, sequence_order, manual_entry, portions…}`.
8. LOT 미리보기는 `next-lot?product=시험명&test=1`. 2차 원료 LOT 제안은 `recent-product-lots?...&test=1`.
9. 임시저장: 초안에 `is_test`, `test_name`, `base_recipe_id`, 행 정의(이름·코드·목표량)까지 저장.
   시험 초안은 `/blend/test` 로 복구. 작성 중 배합 목록(`/blend/drafts`)에 '시험' 칩. 정식 초안 스키마 불변.
10. 검증: Playwright 로 (a) 빈 표에서 자재 2행 추가·목표량·LOT·실제량 입력·저장 → 기록 상세에 is_test=1·LOT `T-` 확인,
    (b) 레시피 불러와서 행 하나 삭제·하나 추가·양 수정 후 저장, (c) 정식 `/blend` 는 종전과 동일(회귀 E2E 1건).
    서버는 `IRMS_DATA_DIR=.tmp-tests/<name>` 로 띄운다. 루트에 tmp_* 금지.

## 9. 기록 조회 + 점도 — 패키지 C

파일 소유: `templates/status.html`, `static/js/status.js`, `static/css/status.css`(있으면),
`src/services/viscosity_service.py`, `src/routers/viscosity_routes.py`, `templates/viscosity.html`,
`static/js/viscosity*.js`, `static/css/viscosity.css`(있으면), `tests/test_viscosity_test_lots.py`.
**문구·색·간격은 `docs/ui-standard.md` 준수.**

기록 조회(/status):

1. 필터 줄에 select "시험" = 전체(기본)/시험만/시험 제외 → `test` 파라미터. 조회 조건은 기존 방식대로 유지·복원.
2. 목록 행: `is_test` 면 중립 칩 '시험' (취소 칩과 같은 급, 완료 칩 대신이 아니라 앞에 추가). 상세 헤더에
   '시험' 칩 + "기준 레시피: {base_recipe_name}"(있을 때만).
3. 시험 기록의 '점도 미입력' 칩은 서버가 `missing` 을 안 주므로 자연히 안 뜬다 — 클라이언트 추가 조건 불필요.
4. 출력(단건·선택·zip·Excel)은 손대지 않는다.

점도(/viscosity) — 2차 결정으로 재설계:

1. 시험 점도는 `test_viscosity_readings` 에만 저장한다. 시험 LOT 하나에 값 하나(정식과 같은 규칙).
   반제품(PB·SBCT·SCRA) 선택과 무관하다. 기준 레시피가 없는 새 레시피 시험도 등록된다.
2. 정식 `add_reading` 은 시험 배합 기록의 LOT 이면 400 으로 거부한다. 정식 점도 통계·관리한계·이상·추세·
   알림·대시 카드는 시험 값을 볼 일이 없으므로 측정값 단위의 시험 필터가 필요 없다.
3. 배합 기록 단위의 제외는 유지한다: 정식 등록 대기열과 `daily_reading_reminders`(트레이)는 `blend_records.is_test = 1` 을 뺀다.
4. 점도 화면 '시험' 탭은 반제품 선택과 무관하게 모든 시험 LOT 을 보여 준다(완료 기록, 최신순, 검색·미등록만).
   열: 작업일·시험명·제품 LOT·기준 레시피·점도·메모·등록. 행을 골라 점도값·측정일·메모를 등록한다.
   이 탭에서는 요약 줄(측정·평균·이상 등)·관리 기준 상자·판정·그래프·규격선을 보여 주지 않는다.
5. 정정·삭제 권한은 정식과 같다(등록 후 10분은 현장 정정·사유 필수, 이후는 책임자). 측정 불가 기록은 없다(시험에는 알림이 없다).
6. 기록 조회 상세의 '점도 측정' 칸은 시험 기록이면 시험 점도를 같은 모양으로 보여 준다(`list_readings_for_blend`).
7. 감사 로그를 남기고 `static/js/admin_users.js` AUDIT_GROUPS 에 한글 라벨을 단다.

## 10. 순서와 검증

1. A 완료 → `python -m pytest tests -q` 전체 통과 + 새 `tests/test_blend_test_mode.py`.
2. B·C 병렬(파일 소유 배타). 각자 pytest 전체 + 자기 E2E.
3. 독립 검증(Fable): pytest 전체, 브라우저 실연(시험 저장 → 기록 조회 필터 → DHR 출력 → 점도 등록 → 대시보드 미반영).
4. 커밋은 패키지별 1개. 메시지 영어.
