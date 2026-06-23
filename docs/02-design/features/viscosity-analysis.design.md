# 점도 분석 (viscosity-analysis) 설계서

> 합성 점도 LOT별 등록 + 추세 분석 + 이상 발생 감지 화면

## 1. 배경 / 입력

원본: `합성 점도.xlsx` — 제품군 3종(PB/SBCT/SCRA)이 시트별로 분리, 각 행이
합성 LOT 1건의 점도 측정.

| 제품 | 건수 | 점도 평균 | σ | LOT 형식 |
|------|------|----------|----|----------|
| PB   | 203  | 48.95 | 1.08 | 8자리 YYMMDD+순번 (하루 2로트) |
| SBCT | 29   | 203.89 | 5.45 | 6자리 YYMMDD |
| SCRA | 20   | 90.19 | 4.62 | 6자리 + 일부 날짜형, 메모/레시피/원료LOT 부가 |

**핵심**: 제품마다 정상 점도 대역이 완전히 다르므로(49/204/90) 모든 판정
기준은 제품 단위로 보관·계산한다. SCRA는 이미 현장에서 "점도 변동 ↔ 원료 LOT
교체" 인과를 메모로 추적 중 → 메모/레시피/원료LOT 컬럼을 1급 데이터로 보존.

## 2. 데이터 모델

```
viscosity_products(id, code UNIQUE, name, target, lower_limit, upper_limit,
                   sigma_k DEFAULT 3, is_active, created_at)
viscosity_readings(id, product_id FK, lot_no, viscosity, measured_date,
                   memo, recipe_material, material_lot, created_by, created_at)
  INDEX (product_id, measured_date)
  UNIQUE (product_id, lot_no)   -- 1 LOT = 1 점도, 엑셀 재임포트 멱등
```

제품 시드: PB/SBCT/SCRA (마이그레이션 `seed_viscosity_products`, 1회).

## 3. 이상 판정 (관리상하한 + 통계 결합)

제품별로 두 축을 결합해 측정값을 `normal / warn / anomaly` 로 분류.

1. **관리 상/하한(spec)**: 관리자가 설정한 `lower_limit`/`upper_limit` 위반 → anomaly.
2. **통계 관리한계(sigma)**: 중심선 ± k·σ. 중심선 = `target`(있으면) 또는 표본 평균,
   σ = 표본표준편차(n≥2). ±kσ 위반 → anomaly.
3. **경고 구간**: 2σ 초과 ~ kσ 이하 → warn.
4. **추세 룰**(Western Electric 부분 집합): 말단 구간 연속 N=5회 단조 상승/하락(run),
   중심선 한쪽 연속 M=7회 치우침(shift) → 추세 경보.

`reasons` 에 `spec_high/spec_low/sigma_high/sigma_low/warn_*` 를 누적해 UI 표기.

### 3.1 기간별(분기/월) 추세 분석

측정 시계열을 기간 버킷(`2026-Q1` / `2026-03`)으로 묶어 건수·평균·σ·최소·최대·
이상수·경고수 + **전기대비 평균변화(mean_delta)** 를 산출(`summarize_periods`).
분기 단위로 공정 드리프트(분기 평균 이동)를 본다. `analyze_product(granularity=)` 가
`periods` 로 반환, 상세 API 는 `?granularity=quarter|month`(기본 quarter). 측정일이
없는 측정은 기간 집계에서 제외.

**기간 알림(`_period_alerts`)**: ① anomaly_spike — 직전 기간 대비 이상 건수가 2건
이상으로 급증, ② mean_shift_up/down — 전기대비 평균변화가 전체 σ 이상(공정 평균
드리프트). `period_alerts` 로 반환, UI 빨강 배너 표기.

### 3.2 신규 입력 즉시 경고

등록 시 서버가 **입력 전 표본 기준**으로 새 값을 판정(`classify_value`, 이상값이
자기 자신을 평균에 섞어 둔감해지는 것 방지)하여 응답에 `new_reading`(status/reasons)
을 포함. 화면은 이상=빨강·경고=주황 토스트 + 폼 하단 결과 라인으로 능동 경고.

## 4. API (권한 분리: 등록=operator, 설정=manager)

| Method | Path | Scope |
|--------|------|-------|
| GET | `/api/viscosity/overview` | operator |
| GET | `/api/viscosity/products` | operator |
| GET | `/api/viscosity/products/{id}` | operator (분석 포함) |
| POST | `/api/viscosity/readings` | operator |
| POST | `/api/viscosity/products` | manager |
| PATCH | `/api/viscosity/products/{id}` | manager |
| DELETE | `/api/viscosity/readings/{id}` | manager |
| GET | `/api/viscosity/products/{id}/export` | manager (CSV) |

## 5. 화면 (`/viscosity`, operator 접근)

- 제품 탭(이상 건수 배지) · 요약 카드(건수/최근/평균±σ/이상/경고)
- 추세 차트(Chart.js): 점도 시계열 + 중심선/관리한계(±kσ)/경고선(2σ)/spec 라인,
  이상=빨강·경고=주황 포인트. 추세 경보 배너.
- 점도 등록 폼(작업자). 측정 이력 테이블(상태 배지, 관리자 삭제).
- 제품 설정 모달(관리자): target/상하한/k/사용 + 새 제품 추가.

nav 에 `점도` 링크(작업자 포함 전원 노출).

### 3.3 연도별 기준 (제품별 + 연도별)

같은 제품이라도 연도/공정에 따라 점도 대역이 크게 달라진다(예: N-TOP 2024 평균
165.6·σ24.9 → 2025 144.7 → 2026 131.1). 전 연도를 합치면 σ가 과대해져 이상
판정이 무의미해지므로 **기준(중심선/σ/이상)은 연도별로 계산**한다.

- `analyze_product(year=)` / `_fetch_readings(year=)` 가 연도 표본만 사용.
- `available_years()` 로 제품의 측정 연도 목록 제공.
- 상세 API `?year=YYYY`(미지정=전체). granularity 에 `year` 추가 → 연도간 비교.
- `overview` 카드는 제품별 **최신 연도** 기준으로 평균·이상수 계산.
- 신규 입력 판정(`classify_value`)도 해당 측정의 **연도** 표본 기준.
- 화면: 툴바 연도 셀렉터(기본=최신 연도, '전체(연도비교)' 옵션) + 기간 토글에 연도.

## 6. 엑셀 임포트

`scripts/import_viscosity.py` — 시트 형태 자동 판별(3종) + 제품 자동 생성 + 다중 파일.
- **long-LOT**: PB/SBCT/SCRA(합성 점도.xlsx). A=LOT B=점도 …
- **wide-date**: 'TOP 점도' 시트(날짜×N-TOP/S-TOP/6-1 TOP/K-TOP). 연도 표기 없어 2026 가정.
- **journal**: 합성일지.xlsx 연도시트(2024~2026). 일자·종류·점도·1차·2차·비고, 점도 있는 행만.
날짜 기반은 lot_no='YYYY-MM-DD-NN'(제품·날짜 순번)로 결정적 생성 → 재임포트 멱등.
운영 DB 적재는 서버와 동일한 `IRMS_DATA_DIR` 로 실행:
`python scripts/import_viscosity.py "합성 점도.xlsx" "합성일지.xlsx"`
(제품 ~12종: PB/SBCT/SCRA + N/S/K/6-1 TOP, S3-TOP, NUVBF, NUVBE, ECB, SBC. PB 2건은 동일 LOT 재측정 → 첫 측정만)

## 7. 테스트

`tests/test_viscosity.py` (15): LOT 날짜 파서·sigma/spec 이상·target 중심선·
경고 구간·run 추세·등록 날짜 추론·중복 차단·overview 집계·라우트 인증.
