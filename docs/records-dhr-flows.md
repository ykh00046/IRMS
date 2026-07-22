# 배합 기록과 DHR 산출물 흐름 (규제 문서)

> 대상: `/status`(배합 기록·배합일지 출력) 화면과 그 뒤의 라우트·서비스.
> 계량·저장(작성) 쪽 자세한 흐름은 [`docs/blend-weighing-flows.md`](blend-weighing-flows.md) 를 보라 — 이 문서는
> **저장된 기록의 수명주기와 DHR(원료배합일지) 산출물**만 다룬다.
> 단위는 모두 `g` 고정. 화면 브랜드는 BRM, 내부 식별자는 IRMS 유지.

관련 파일:
- 라우트: `src/routers/blend_routes.py`, `src/routers/blend_rescale_ack_routes.py`, `src/routers/admin_routes.py`
- 서비스: `src/services/blend_service.py`, `dhr_excel.py`, `dhr_pdf.py`, `dhr_cache.py`, `signature_config.py`, `signature_processor.py`, `record_delete_service.py`
- 감사: `src/db/audit.py`
- 화면: `templates/status.html`, `static/js/status.js`

---

## 1. 기록 수명주기

### 1.1 생성 (참조만 — 계량 문서 소관)

배합 실적 저장은 `POST /blend/records`(`blend_create`, `blend_routes.py:533`),
`POST /blend/records/bulk`(`blend_create_bulk`, `blend_routes.py:846`),
`POST /blend/records/continuous`(`blend_create_continuous`, `blend_routes.py:883`) 세 경로다.
저장 시점의 검증(레시피 파생·자재 LOT 필수·편차·증량 승인)은 계량 문서에서 다룬다.
여기서 기억할 것은 **저장된 기록이 곧 규제 원본**이라는 점: `product_lot` 은 저장 순간 서버가
채번(`generate_product_lot`, `blend_service.py:658`)하며, 이후 수정·취소·삭제 규칙이 이 원본을 지킨다.

### 1.2 수정 (`PUT /blend/records/{id}` — `blend_update`, `blend_routes.py:671`)

- **권한**: `dependencies=[Depends(require_access_level("manager"))]` — 책임자 이상만. 현장 무로그인은 401.
- **수정 가능**: 헤더(작업자·작업일·작업시간·총량·저울·비고·반응기·잉크·position)와 **상세 전량 교체**.
  상세는 `update_blend_record`(`blend_service.py:1131`)가 `blend_details` 를 `DELETE` 후 재`INSERT`.
- **수정 불가(의도적 봉인)**:
  - **제품명**: `body.product_name` 이 기존과 다르면 400(`blend_routes.py:692`). `product_lot` 이
    `{제품명}{YYMMDD}{순번}` 이라 제품명만 바꾸면 LOT 접두사와 어긋나고, 재채번하면 이미 출력·보관된
    DHR 과 어긋난다(감사 F-8/F-1). 제품을 잘못 등록했으면 "취소 후 재등록" 안내.
  - **product_lot·status·생성정보(created_by/at)·서명 3종(worker/reviewed/approved_sign)**: `UPDATE` 문
    (`blend_service.py:1151`)이 이 컬럼들을 건드리지 않아 보존.
- **감사**: `blend_record_update`(`blend_routes.py`) — details 에 `product_name`/`total_amount`/`items`(행 수)만.
- ✅ **수정 경로는 이제 create 와 검증이 대칭이다(GAP-2 해소)**: carry-over(`enforce_carry_over`) 이후
  recipe_id 가 있으면 서버가 비율·이론량을 **재산출**(`derive_details_from_recipe`, F-5)하고, 자재 LOT 필수
  (`missing_lot_names`)·미등록 반제품 LOT(`unregistered_product_lots`, 사유 전달 시 통과)·편차
  (`weighing_tolerance_violations`)를 create 와 동일하게 강제한다. 메타(작업자·작업일·비고 등) 정정 자유는
  유지. recipe_id 없는 옛/수동 기록은 대조 근거가 없어 파생 재산출을 건너뛴다(LOT·편차 검사는 수행).

### 1.3 취소 / 삭제 / 복원 (`DELETE /blend/records/{id}` + `restore`)

`blend_cancel`(`blend_routes.py:1031`)이 `hard` 쿼리로 두 갈래(감사 F-2):

| 행위 | 조건 | DB 효과 | 감사 action | 상태 전이 |
|------|------|---------|-------------|-----------|
| **soft 취소** | `hard=false`(기본) | `status='completed' → 'canceled'` | `blend_record_cancel` | completed→canceled |
| **hard 삭제** | `hard=true` | 행·상세 물리 삭제, 점도 링크 NULL | `blend_record_deleted` | (행 소멸) |
| **복원** | soft 취소분만 | `status='canceled' → 'completed'` | `blend_record_restore` | canceled→completed |

- **권한**: soft·hard **모두 책임자 전용**. 인증을 404 조회보다 **먼저** 수행해 비인증 호출자에게 기록
  존재 여부를 흘리지 않는다(`blend_routes.py:1045`). 미로그인·비책임자 모두 403(기존 관례 보존).
- **hard 삭제 실체**: `record_delete_service.delete_blend_record`(`record_delete_service.py:58`) —
  `viscosity_readings.blend_record_id` 를 NULL 로 끊고, `blend_details`·`blend_records` 행 삭제.
- **복원**: `blend_restore`(`blend_routes.py:1083`) 는 `status != 'canceled'` 면 400. 항상 `completed` 로 되돌림
  (상태값이 completed/canceled 둘뿐이라 안전).
- soft 취소는 목록·대시보드에서 숨김(`list_blend_records` 가 `status != 'canceled'` 필터, `blend_service.py:1361`).
  단, **단건 ID 직접 조회(`get_blend_record`)는 상태 무관 반환** → 취소분도 DHR 출력 가능(§7 POLISH-7).
- `reason`(≤500자)은 감사 details 에만 남고 기록 컬럼에는 저장되지 않는다.

### 1.4 점도 연계 (`POST /blend/records/{id}/viscosity` — `blend_add_viscosity`, `blend_routes.py:408`)

- UI 는 점도 관리 화면 한 곳뿐이며, 이 라우트가 그 화면의 저장 경로(배합 기록에는 점도 입력 폼 없음).
- 제품명으로 점도 제품을 자동 확보(`viscosity_service.ensure_product_by_code`), 첫 상세 행의 `material_lot`
  을 참고 자재 LOT 로, `product_lot` 을 점도 `lot_no` 로 연계. 반응기 값도 물려받음.
- LOT 중복이면 409(`이미 등록된 점도(LOT ...)`). 감사 `blend_viscosity_link`.
- 반대로 기록을 hard 삭제하면 위 링크가 NULL 로 끊긴다(점도값 자체는 남음).

---

## 2. 제품 LOT 규칙

### 2.1 채번 (`generate_product_lot`, `blend_service.py:658`)

- 형식 `{제품명}{YYMMDD}{순번:02d}`. 같은 날 같은 제품의 기존 최대 순번 + 1.
- LIKE 검색 시 `%`·`_`·`\` 를 이스케이프해 접두사 오매칭 방지.
- 미리보기: `GET /blend/next-lot`(`blend_next_lot`, `blend_routes.py:102`) — 저장 시 부여될 값을 화면에 표시(비구속 예측).

### 2.2 중복 방지 (레이스 봉인 — 감사 F-1)

`create_blend_record`(`blend_service.py:1041`)가 3중 방어:
1. **`BEGIN IMMEDIATE`** 로 쓰기 락 선획득(`blend_service.py:1069`) → 동시 요청의 채번을 직렬화
   (WAL 에서 리더는 라이터를 안 막으므로 명시 락 필요).
2. **UNIQUE 인덱스** `idx_blend_records_lot_unique`(`migrations.py:479`) — 전역 유일 봉인.
   기존 중복은 `dedup_product_lots`(`migrations.py:564`)가 마이그레이션 때 정리(감사 `product_lot_dedup`).
3. IntegrityError(`product_lot`) 시 **재채번 3회 재시도**(`blend_service.py:1075`) — 교차 프로세스 방어.

→ LOT 채번 레이스는 견고하게 막혀 있다.

### 2.3 이어서 계량 다중 로트 (`create_continuous`, `blend_service.py:1261`)

- 한 레시피·(기본)동일 총량으로 N개 로트를 **원자적**으로 저장: 저장 전 전 로트를 도출·편차검사하고,
  하나라도 실패하면 아무것도 저장하지 않음(`blend_routes.py:914` 주석). `product_lot` 은 로트마다 연속 채번
  (같은 트랜잭션 내 `create_blend_record` 반복 호출이 순번을 이어감).
- 반응기 이월(carry-over)은 단일 배합 화면 전용 — 연속 화면에서는 400 거부(`blend_routes.py:899`).
- 로트별 총량 오버라이드(`lot_totals`)·로트별 증량(`lot_rescale_events`)은 초과 계량 증량 로트에만 적용.

---

## 3. DHR 출력

DHR(원료배합일지) 산출물은 두 형식이다: **공식 양식 Excel** 과 **스캔효과 PDF**. 둘 다 소스는
`get_blend_record`(`blend_service.py:1321`)가 돌려주는 기록 dict(헤더 + `details[]` + `variance`).

### 3.1 공식 양식 Excel (`GET /blend/records/{id}/export` → `dhr_excel.build_official_dhr_xlsx`)

- 원본 `Program-estimation v3` ExcelWriter 이식. 공식 양식 `src/resources/dhr_template.xlsx`("원 료 배 합 일 지")를
  복사해 셀에 채움(`dhr_excel.py:42`). openpyxl 만 의존 → 서버 어디서나 동작.
- **셀 매핑**(`CELL_MAPPING`, `dhr_excel.py:22`): 작업일 A3 · 저울 A4 · 작업자 C3 · 작업시간 E3 ·
  제품LOT A6 · **총량/100** B6(`total_amount/100`, `dhr_excel.py:73`). 데이터 6행~: 배합원료명 C ·
  원료LOT D · 배합비율 E · 배합량(g) F · 실제배합량(g) G.
- 데이터 이후 빈 행 삭제, A/B 열 데이터 범위 병합·가운데정렬, 테두리, 인쇄영역 `A1:G{end_row}` 로 축소.
- `signature_image_path` 인자를 주면 결재 도장 이미지를 G2(228×65)에 삽입 — 단, HTTP 라우트
  `blend_export`는 이 인자를 **주지 않는다** → Excel 다운로드는 항상 서명 없는 빈 결재칸.
- `sign_failed=True` 인자를 주면(서명 요청인데 합성 실패, POLISH-6) G2 에 `(서명 합성 실패)` 표식을 남긴다.
- ✅ **증량(rescale) 이력**(`rescale_summary_line`, GAP-5): 표 아래(end_row+2) 병합 셀에 한 줄 요약
  `증량 N회: 1000→1050 (승인: 홍길동); 1050→1100 (부재: 사유)` 를 append 한다(양식 레이아웃 불변).
- 파일명: ASCII 폴백 `blend-{id}.xlsx` + RFC5987 `원료배합일지-{product_lot}.xlsx`.

### 3.2 스캔효과 PDF (`GET /blend/records/{id}/pdf` → `dhr_pdf.build_scanned_dhr_pdf`)

`?sign=1` 이면 서명 합성, 기본은 빈 결재칸. 렌더 경로는 두 갈래(`dhr_pdf.py`):

- **정확 경로**(운영 PC): 공식 양식 xlsx → **Excel COM(win32com)** 으로 PDF → **PyMuPDF(fitz)** 로 300dpi
  이미지 → (선택)서명 합성 → 스캔효과 → PDF(`render_exact_form_image`, `dhr_pdf.py:347`). 공식 양식과 픽셀 일치.
  Excel 변환은 별도 스레드 + 90초 타임아웃 + `_excel_lock` 으로 서버 멈춤 방지(`dhr_pdf.py:270`).
- **폴백 경로**(개발/타 환경): win32com·PyMuPDF 없으면 PIL 로 양식을 재현(`render_form_image`, `dhr_pdf.py:92`).
  `exact_available()`(`dhr_pdf.py:213`)가 판정.
- **스캔효과**(`apply_scan_effects`, `dhr_pdf.py:190`): GaussianBlur + 노이즈 + 대비 + 밝기 + 종이톤(미색).
  파라미터는 서명 설정에서 옴.
- 일괄: `GET /blend/records/dhr-batch?ids=...`(`blend_dhr_batch`, `blend_routes.py:354`) → `build_batch_dhr_pdf`
  (기록당 1장, 최대 200건). `?sign=1` 지원.

### 3.3 서명 합성 (작성/검토/승인)

- **표시 위치**: 서명은 **출력물에서만** 합성된다 — DB 의 `worker_sign`(작업자 캔버스 서명 data URL)과
  `reviewed_sign`/`approved_sign`(결재 시 저장) 은 원본이되, DHR 결재 박스의 실제 도장은 렌더 때 합성.
- **작성(담당/charge)**: 작업자가 그린 캔버스 서명(`worker_sign`)을 파란 펜 잉크색(`_INK_RGB`)으로 통일해
  PNG override 로 합성(`_worker_sign_override`, `dhr_pdf.py:312`). 서명 없거나 손상되면 샘플로 폴백.
- **검토/승인(review/approve)**: 결재 도장 템플릿 `resources/signature/image.jpeg` 에 `signature_samples` 의
  샘플 서명을 합성(`_build_signed_stamp`, `dhr_pdf.py:337`). 실제 결재 기록은 `POST .../approve`
  (`blend_approve`, `blend_routes.py:758`, 책임자 전용, `role` = review|approve)로 `reviewed_*`/`approved_*` 컬럼에 저장.
  현장 미사용(구 프로그램 관행상 결재는 문서 출력물로).
- **합성 엔진**: `ImageProcessor`(`signature_processor.py`) — upsample·blur·unsharp·잉크 알파·압력 노이즈·
  mesh warp·회전/스케일 랜덤화로 실제 서명 느낌 재현.

### 3.4 서명 설정 파라미터 (`signature_config.py`, 책임자 전용)

- `GET/PUT /admin/signature-config`(`admin_routes.py:328/337`) — `data/signature_config.json` 에 저장/로드.
  `DEFAULTS`(`signature_config.py:12`) + `RANGES`(입력 클램프). 저장 시 감사 `signature_config_updated`.
- 항목: 합성(gaussian_blur_sigma·pressure_noise_strength·ink_alpha_factor·brightness·contrast·rotation·scale)
  + 스캔(scan_noise_range·blur·contrast·brightness·paper_tone).
- 미리보기: `GET /admin/signature-preview`(`build_signature_preview_png`, 샘플 기록으로 합성).
- 서명 샘플 CRUD: `/admin/signature-samples`(추가/삭제 각각 감사 `signature_sample_added/deleted`).

### 3.5 PDF 캐시 무효화 (`dhr_cache.py`)

- 비서명 PDF 만 디스크 캐시(`data/dhr_cache/blend_{id}.pdf` + `.marker`). **서명본은 캐시하지 않음**
  (`blend_routes.py:826`).
- **마커** = SHA256(`{v:2, record: 전체 기록 dict, sig: signature_config.load()}`)(`dhr_cache.py:17`).
  → **자동 무효화 조건**:
  - 기록 내용이 바뀌면(수정·결재·복원 등 `updated_at` 포함 무엇이든) 마커가 달라져 재생성.
  - 서명 설정(`signature_config`)이 바뀌면 마커가 달라져 재생성.
  - 별도 무효화 훅 불필요(마커가 곧 콘텐츠 해시). 테스트: `tests/test_dhr_cache.py`.
- `id` 없는 기록은 캐시 no-op(안전).

---

## 4. 단건 / 선택 / 전체 / 일괄 재생성 용도 구분 (구 프로그램 관행)

| 용도 | 엔드포인트 | 성격 |
|------|-----------|------|
| **단건 출력** | `GET .../{id}/export`(Excel), `.../{id}/pdf`(PDF) | 한 배치의 배합일지 — 일상 |
| **선택 출력** | `GET /blend/records/dhr-batch?ids=1,2,3`(PDF 한 파일) | 화면에서 고른 여러 건 |
| **전체 Excel 백업** | `GET /blend/records/export-all` | 필터된 기록을 한 시트로(데이터 이관·백업, DHR 아님) |
| **배치 상세 Excel** | `GET /blend/batch-details/export` | 자재별 평면 목록(분석용) |
| **일괄 생성(bulk)** | `POST /blend/records/bulk` | 드문 재생성 — 아래 |

- **기록이 기본**: 일상 배합일지는 저장된 실측 기록에서 단건/선택으로 출력한다.
- **일괄 생성(bulk, `create_bulk` `blend_service.py:1203`)**: 같은 레시피로 (작업일·총량) 조합을 여러 건
  한꺼번에 만든다. **실제량 = 이론량, 자재 LOT = 비움**(docstring: "일괄 계획·문서용"). 실측이 아닌
  문서/계획 재생성 용도 → 드물게 씀. §7 예외 확인 참조.
- **DHR 전용 레시피(인허가 변경본)**: `GET /blend/recipes?dhr=1`(`blend_recipes`, `blend_routes.py:83`)이
  DHR 전용 레시피만 반환. 실제 배합비와 별개로 **인허가 문서에 실리는 변경본**을 일괄 배합일지 생성에 쓴다.
  일반 배합과 분리(점도 연계는 또 별개).

---

## 5. 오늘 추가된 통제의 기록 표면 (증량·수기승인·미등록 LOT)

세 기능은 웹 화면(/status)에 나타난다. 공식 DHR 문서 표면화는 부분 개선됨: **증량 이력은 이제 공식 DHR
Excel 비고에 요약 줄로 실린다(§7 GAP-5 부분 해소)**. 수기 입력 승인·미등록 LOT 사유는 기록 비고(note)로
문서에 노출된다. `manual_entry` 행별 표식의 PDF 표면화는 여전히 웹 화면 전용.

### 5.1 증량(rescale) 이력

- 저장: `validate_rescale_events`(`blend_service.py:1509`)가 검증·정규화 → `apply_rescale_to_record`
  (`blend_service.py:1596`)가 `rescale_events_json`·`rescale_count`·`rescale_unacked` 컬럼에 기록.
  최대 2건, 각 건은 책임자 승인 토큰(`approval_id`, 30분 TTL·1회용) 또는 부재 사유(`absence_reason`) 필요.
  부재 사유로 진행하면 `rescale_unacked=1`.
- **기록 표면**: `get_blend_record` 는 이제 rescale 컬럼(`rescale_events_json`/`rescale_count`/`rescale_unacked`)을
  **함께 SELECT 해 기록 dict 에 실어 준다(GAP-5 해소)** → 공식 DHR Excel 이 요약 줄을 렌더한다.
  목록 배지/상세 모달용 별도 요약 엔드포인트 `GET /blend/rescales/summary`(`blend_rescale_ack_routes.py`,
  무로그인)와 `status.js`(`rescaleMap`) 병합 경로는 그대로 유지(웹 화면 표면).
- **사후 확인(수기 승인)**: 책임자가 `GET /blend/rescales/unacked`(목록) → `POST .../{id}/rescale-ack`
  (`ack_rescale`, `blend_rescale_ack_routes.py:88`)로 `rescale_unacked=0` 처리. 감사 `blend_rescale_acked`(멱등).
- 승인/거부/저장 감사: `blend_rescale_approved`·`blend_manual_entry_approved`·`blend_rescale_approve_denied`·
  `blend_rescale_saved`(`blend_routes.py:502`~`653`).

### 5.2 수동 입력(manual_entry)

- 배치 단위(`blend_records.manual_entry`) + 행 단위(`blend_details.manual_entry`). 저울 PRINT 가 아닌 손입력 표시.
- **책임자 전용 노출**: `_mask_manual_entry`(`blend_routes.py:70`)가 비책임자 응답에서 `manual_entry` 를
  False 로 **가림**(화면 가림이 아니라 응답 자체를 가려 API 직접 조회로도 안 보임). 저장·감사 원본은 불변.
- 화면: `status.js` 가 목록·상세에 ⚠ 표식(`manualTag`/`manual-entry-mark`).
- 저울 전용 모드에서 이 배치만 수기 허용하는 책임자 승인: `POST /blend/manager-verify`(purpose=`manual`),
  감사 `blend_manual_entry_approved`.

### 5.3 미등록 반제품 LOT '사유 적고 진행'

- 2단 제조(1차 중간체→2차)에서 2차 원료 행의 자재 LOT 가 실제 1차 완료 기록의 `product_lot` 인지
  서버가 재검증(`unregistered_product_lots`, `blend_service.py:823`). 미등록이면 `lot_overrides` 에 사유가
  있어야 통과, 없으면 400. 클라이언트 fail-open 우회를 막는 서버 백업 검증.
- ✅ **사유는 이제 이중 보존된다(GAP-1 재정의)**: (1) 클라이언트가 `[미등록 LOT 진행] name/lot: 사유` 를
  기록 비고(`note`)에 직렬화해 저장(`blend.js buildOverrideNote`, DB `blend_records.note` 로 영속),
  (2) 서버가 받은 구조화 사유(`lot_overrides`)를 `blend_record_create`/`_continuous_create` 감사 details 에도
  함께 남긴다(belt-and-braces — 클라이언트 의존 없이 일탈 사유 추적 가능).

---

## 6. 감사 로그 체계

`write_audit_log`(`db/audit.py:9`) → `audit_logs`(action, actor 4필드, target_type/id/label, details_json, created_at).
조회는 `GET /admin/audit-logs`(책임자 전용, `admin_routes.py:78`).

| action | 발생 지점 | actor | target_label |
|--------|-----------|-------|--------------|
| `blend_record_create` | 단건 저장 | 로그인 사용자(무로그인=None) | product_lot |
| `blend_record_bulk_create` | 일괄 생성 | 〃 | "N건" |
| `blend_record_continuous_create` | 이어서 계량 저장 | 〃 | "N건" |
| `blend_record_update` | 수정(PUT) | 책임자 | product_lot |
| `blend_record_cancel` | soft 취소 | 책임자 | product_lot |
| `blend_record_deleted` | hard 삭제 | 책임자 | product_lot |
| `blend_record_restore` | 복원 | 책임자 | product_lot |
| `blend_record_review` / `blend_record_approve` | 결재(검토/승인) | 책임자 | product_lot |
| `blend_viscosity_link` | 점도 연계 | 사용자 | product_lot |
| `blend_rescale_approved` / `blend_manual_entry_approved` | 증량·수기 책임자 승인 | 책임자 | approver명 |
| `blend_rescale_approve_denied` | 승인 실패(비책임자/오인증) | (있으면)계정 | 입력 이름 |
| `blend_rescale_saved` | 증량 포함 저장 | 사용자 | product_lot |
| `blend_rescale_acked` | 미승인 증량 사후 확인 | 책임자 | product_lot |
| `dhr_exported` | DHR 출력·다운로드(단건 Excel/PDF·일괄 PDF·전체 Excel) | 사용자(무로그인=None) | product_lot / "N건" |
| `signature_config_updated` | 서명 설정 변경 | 책임자 | signature_config |
| `signature_sample_added` / `signature_sample_deleted` | 서명 샘플 CRUD | 책임자 | 파일명 |
| `product_lot_dedup` | LOT 중복 정리(마이그레이션) | (시스템) | old→new |

- ✅ **DHR 출력 감사(GAP-4 해소)**: `blend_export`(xlsx)·`blend_pdf`(pdf/pdf_signed)·`blend_dhr_batch`(pdf_batch)·
  `blend_export_all`(xlsx_all) 모두 `_audit_dhr_export` 로 `dhr_exported` 를 남긴다. details 에
  `format`·`count`·`record_ids`(≤200)·`actor_name`(현장 포함). GET 이지만 감사만 별도 커밋.
- **여전히 감사에 남지 않는 행위**: 배치상세 Excel(`/blend/batch-details/export`, 분석용 평면 목록 — DHR 아님).

---

## 7. 갭 헌트 (BUG / GAP / POLISH)

> 규제 관점(기록 불변성·추적성·일탈 문서화) 우선.

### GAP-1 — ✅ 해소(재정의) — 미등록 LOT '진행 사유' 영속
검증 결과 **사유는 이미 기록 비고로 영속되고 있었다**: 클라이언트(`blend.js buildOverrideNote`)가
`[미등록 LOT 진행] name/lot: 사유` 를 `note` 에 직렬화 → `create_blend_record` 가 `blend_records.note` 에 저장.
따라서 원래 GAP 은 '소실'이 아니라 '클라이언트 의존 + 구조화 미보존'이었다.
- **조치(belt-and-braces)**: 서버가 받은 구조화 `body.lot_overrides` 를 `blend_record_create`·
  `blend_record_continuous_create` 감사 details 에도 함께 남긴다(`blend_routes.py`). 이제 클라이언트가
  비고 직렬화를 빠뜨려도(직접 API 호출 등) 서버 감사에서 일탈 사유를 추적할 수 있다.

### GAP-2 — ✅ 해소 — 수정(PUT)이 create 통제를 대칭으로 강제
`blend_update`(`blend_routes.py`)에 create 와 동일한 통제를 추가했다:
- recipe_id 가 있으면 `derive_details_from_recipe`(F-5)로 비율·이론량을 **서버 재산출**(클라이언트 값 폐기).
- `missing_lot_names`(자재 LOT 필수) → 비면 400.
- `unregistered_product_lots`(미등록 반제품 LOT, 사유 전달 시 통과) → 400.
- `weighing_tolerance_violations` 는 재산출 상세 기준으로 검사 → 초과 시 400.
- carry-over(`enforce_carry_over`)는 derive 보다 먼저(기존 유지). 메타 정정 자유는 보존.
- ⚠ **의도적 비대칭 유지**: create 의 `resolve_chain_tip` 개정 409 가드는 PUT 에 넣지 않았다 — 옛 레시피로
  저장된 기록의 benign 메타 정정이 409 로 막히는 것을 피하기 위함(derive 는 내부적으로 최신 개정판으로 귀결).
- 테스트: `test_blend_update_clearing_lot_returns_400`, `test_blend_update_off_recipe_amount_returns_tolerance_400`,
  `test_blend_update_benign_metadata_edit_still_works`(tests/test_blend.py).

### GAP-3 — 수정 전 원본(before-image) 미보존, 변경 이력 빈약
`update_blend_record`(`blend_service.py:1167`)가 `blend_details` 를 `DELETE` 후 재`INSERT`. 감사
`blend_record_update`(`blend_routes.py:748`)는 신규 product_name/total/행수만 기록 — **이전 값·이전 상세가
어디에도 남지 않는다**.
- **규제 영향**: 이미 출력·보관된 DHR 과 현재 기록이 다를 때 무엇이 언제 어떻게 바뀌었는지 재구성 불가.
  기록 불변성/변경관리(change control) 관점의 핵심 약점.

### GAP-4 — ✅ 해소 — DHR 산출물 출력·다운로드 감사
`blend_export`(Excel)·`blend_pdf`(PDF)·`blend_dhr_batch`(일괄)·`blend_export_all`(전체 Excel)에
`_audit_dhr_export` 헬퍼를 붙여 `dhr_exported` 감사를 남긴다(actor=로그인 사용자/현장, details 에
format·count·record_ids). 테스트: `test_dhr_exports_are_audited`(tests/test_blend.py).

### GAP-5 — ✅ 부분 해소 — 증량 이력이 공식 DHR Excel 에 표시됨
- `get_blend_record` 가 `rescale_events_json`/`rescale_count`/`rescale_unacked` 를 기록 dict 에 실어 준다.
- `dhr_excel.build_official_dhr_xlsx` 가 `rescale_summary_line` 으로 표 아래 비고에 한 줄 요약을 append
  (`증량 N회: before→after (승인:…/부재:…)`). 양식 레이아웃은 불변.
- 수기 입력 승인 메모는 기록 비고(`note`)에 실려 있고, 비고가 인쇄되면 함께 노출된다(별도 조치 불요).
- 테스트: `test_official_dhr_includes_rescale_summary`(openpyxl read-back).
- ⚠ **남은 범위**: 스캔효과 PDF(`dhr_pdf`)의 `render_form_image` 폴백 표는 아직 증량 줄을 그리지 않는다
  (정확 경로 PDF 는 Excel→PDF 변환이라 요약 줄이 자동 반영됨). `manual_entry` 행별 표식·미등록 LOT 사유의
  PDF 표면화는 이번 범위 밖(비고 note 로는 노출됨).

### POLISH-6 — ✅ 해소 — 서명 합성 실패를 표면화
`sign=True` 인데 합성이 실패하면 이제 **가시 표식 + 서버 로그**를 남긴다(무언의 미서명 출력 금지):
- 정확 경로: `_build_signed_stamp` 실패(`stamp_path=None`) → `build_official_dhr_xlsx(sign_failed=True)` 가
  G2 에 `(서명 합성 실패)` 기입 + `logging.warning`.
- 폴백 경로: `create_signed_image` 실패 → `render_signed_dhr` 가 양식 이미지에 붉은 `(서명 합성 실패)` 를
  그려넣음 + `logging.warning`.
- 테스트: `test_official_dhr_marks_signature_failure`(Excel sign_failed 표식 read-back). 정확 경로 PDF 는
  `exact_available()`=False 인 개발 환경에서 검증 불가 → 정적 확인.

### POLISH-7 — 취소(canceled) 기록도 DHR 출력 가능 + hard 삭제 시 캐시 잔류
- `get_blend_record` 는 상태 무관 반환 → soft 취소분도 `blend_pdf`/`blend_export` 로 배합일지 출력 가능
  (목록에서는 숨겨져 있음). 캐시(`dhr_cache`)도 남는다.
- `record_delete_service.delete_blend_record`(`record_delete_service.py:58`)는 `dhr_cache` 를 지우지 않아
  hard 삭제 후 `data/dhr_cache/blend_{id}.pdf` 가 잔류(디스크 litter). id 재사용은 없어 오배급 위험은 낮음.

### 예외 확인 — bulk 재생성이 LOT 필수·편차·증량 통제를 전면 우회 (의도됨, 단 표식 부재)
`create_bulk`(`blend_service.py:1203-1258`)는 **의도적으로** `material_lot=None`, `actual=theory` 로 만들고
LOT 필수·편차·미등록·증량 검사를 전혀 하지 않는다(docstring "일괄 계획·문서용", "자재 LOT 은 비움").
- 이는 구 프로그램 관행의 문서/계획 재생성 예외로 **설계된 우회**가 맞다.
- 다만 규제 관점 주의: bulk 기록은 LOT 공백·편차 0 이지만 목록/DHR 에서 **실측 기록과 구분하는 표식이 없다**.
  bulk 산출물이 실제 계량 기록으로 오인될 여지 → 표식(예: 배지·비고 자동기입) 도입 검토 권장.

---

## 8. 확인 불가 항목 (정적 분석 한계)

- **Excel COM 정확 경로**(`win32com` + `PyMuPDF`)는 운영 PC(Excel 설치)에서만 동작. 이 개발 환경에서는
  `exact_available()` 이 False → **PIL 폴백만** 정적으로 확인 가능. 서명 합성의 실제 픽셀 결과·스탬프 삽입
  위치는 미검증.
- 지시에 따라 **서버 기동·pytest·마이그레이션 실행을 하지 않았다** — 위 흐름·갭은 모두 소스 정적 분석 기준.
  런타임에서 감사 로그가 실제로 쌓이는지, 캐시 파일이 생성/무효화되는지는 코드 경로로만 판단.
- 서명 샘플(`resources/signature/`)·양식 템플릿(`resources/dhr_template.xlsx`)의 실물은 열어보지 않음
  (바이너리) — 셀 매핑은 `CELL_MAPPING` 코드 기준.
