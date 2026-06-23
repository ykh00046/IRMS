# 배합 실적(잉크 계량 재구축) 설계서 — blend-overhaul

원본: `C:/X/Program-estimation/v3` (PySide6). 데이터모델/검증/LOT/문서 로직을 웹으로 이식.

## 1. 데이터 모델
```
blend_records(id, product_lot, recipe_id→recipes, product_name, ink_name, position,
  worker, work_date, work_time, total_amount, scale,
  status[draft|completed|canceled], note, created_by, created_at, updated_at)
blend_details(id, blend_record_id→blend_records(CASCADE), material_id→materials,
  material_code, material_name, material_lot, ratio, theory_amount, actual_amount,
  sequence_order, created_at)
viscosity_readings.blend_record_id  -- 점도 ↔ 배합 연계 FK (nullable)
```
인덱스: blend_records(work_date DESC), (product_lot), (recipe_id); blend_details(record, seq);
viscosity_readings(blend_record_id) WHERE NOT NULL.

## 2. 환산 로직 (blend_service)
- `compute_ratios(weights)` = wᵢ/Σw × 100
- `scale_theory(weights, total)` = wᵢ/Σw × total  → 이론 계량량
- `get_recipe_for_blend(recipe_id, total?)`: 레시피 절대중량을 비율·이론량으로. total 미지정 시 Σw.
- `generate_product_lot(product, work_date)` = {product}{YYMMDD}{max순번+1:02d} (LIKE 스캔, 멱등)
- 편차: actualᵢ−theoryᵢ, 합계(이론/실제/순편차/절대편차)

## 3. 재고 통합
- `deduct_blend_stock(record_id)`: 상세별 실제(없으면 이론) 사용량을 materials.stock_quantity 차감 +
  material_stock_logs(reason='measurement', recipe_id, recipe_item_id NULL, note='배합 #id'). 멱등(note 존재 시 skip).
  reason='measurement' → 소비예측(forecast) 자동 포함.
- `reverse_blend_stock(record_id)`: note 태그 로그로 정확 복원(재고 환원+로그 삭제). 취소 시.
- BlendCreateBody.deduct_stock(기본 True).

## 4. API (무로그인 개방)
| Method | Path | 설명 |
|---|---|---|
| GET | /blend/recipes | 배합용 레시피 목록 |
| GET | /blend/recipes/{id}?total= | 비율·이론량 환산 |
| GET | /blend/workers | 작업자 목록(필터) |
| GET | /blend/records | 기록조회(start/end/worker/search) |
| GET | /blend/records/{id} | 상세(배합상세+편차+연계점도) |
| POST | /blend/records | 저장(+재고차감) |
| POST | /blend/records/{id}/viscosity | 점도 연계 등록 |
| GET | /blend/records/{id}/export | Excel(openpyxl, RFC5987 파일명) |
| DELETE | /blend/records/{id} | 취소(+재고복원) |

## 5. 화면 (/blend, 무로그인)
- 탭: 배합 입력 / 기록 조회
- 입력: 레시피·총량·작업자·일자·시간·저울 + 자재표(비율/이론 자동, 실제·자재LOT 입력, 편차 실시간) + 카드(이론/실제/순편차/LOT예정) + 저장
- 조회: 필터 + 목록 → 상세(DHR) 모달: 헤더 키값 + 상세표 + 합계 + 점도연계(목록/등록) + 인쇄/Excel/취소
- 인쇄: @media print 로 DHR만 출력. 점도 등록폼 등 .no-print 숨김.
- 진입: nav '배합' + 근태 헤더 '배합' + 홈 카드. IRMS 라이트 테마/공용 CSS.

## 6. 이식 제외/대체
- Excel COM/PDF(win32) → openpyxl + 브라우저 인쇄(PDF)
- PySide6 위젯 → HTML/JS
- ⚠️서명 이미지 위조/스캔효과 → 미이식. 정당한 결재 기록(작성/검토/승인 이름+시각)으로 대체.

## 7. 테스트
tests/test_blend.py: 비율/이론, product_lot 순번, 생성/조회/편차, 필터, 재고 차감·복원·멱등,
점도 연계, 라우트 개방. tests/test_viscosity.py: blend_record_id 컬럼 반영.
