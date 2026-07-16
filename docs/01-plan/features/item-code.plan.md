# 품목코드 도입 (item-code) — 설계 초안

> 상태: **설계 검토 중** (2026-07-16). 운영 DB 스냅샷 매칭률 분석 대기.
> 배경: 자재·레시피에 정식 품목코드가 없어 (1) 이름 표기 차이로 중복 등록,
> (2) 상위 재고 대시보드(Dashboard-Raw_material)가 erp_code 빈 행을 **skip** —
> 원가편차 집계 누락, (3) 화면 '자재코드'가 실은 분류(category)인 문제.

## 0. 소스 데이터 (ERP 품목 마스터, 루트 code*.xlsx — 커밋 금지, 이관 후 data/master/ 보관)

| 파일 | 내용 | 행수 | 비고 |
|---|---|---|---|
| code.xlsx | 전 품목 마스터 | 9,565 | **대분류=원자재는 117행** (AS 소프트 50 · AC 42 · AH 하드 24 · AW 1). 포장재/소모품 등은 배합과 무관 |
| code2.xlsx | 반제품 · 잉크코드 | 1,722 | BC… 위주 |
| code3.xlsx | 반제품 · 합성코드 | 42 | B0/B1/BW… — **PB=B0020** |
| code4.xlsx | 반제품 · 약품코드 | 135 | B0/B1/BW… |

- 마스터 품질: 원자재 117행 중 이름 중복 3건(GMMA, DMA, MCR-C12 — 제조사 구분 계열) → 자동 매칭 제외, 수동 확정.
- 제품구분(잉크/합성/약품코드) ↔ IRMS 레시피 분류(잉크/합성/약품) 1:1 대응. **용수는 ERP에 없음**(IRMS 자체 분류로 유지).
- 코드 표기 정규화 필요(소문자 bc, 공백 등).

## 1. 데이터 모델

```
item_code_master (신설)
  code TEXT PRIMARY KEY        -- 예: AS0001, B0020
  name TEXT NOT NULL           -- 품목명
  spec TEXT                    -- 규격
  unit TEXT                    -- 기준단위(g)
  kind TEXT NOT NULL           -- 'material'(원자재) | 'product'(반제품)
  category_hint TEXT           -- 반제품: 잉크|합성|약품 (제품구분에서), 원자재: 중분류
  source TEXT                  -- code|code2|code3|code4 (재임포트 추적)
  imported_at TEXT
  + INDEX (kind, name)

materials.code TEXT UNIQUE     -- ERP 품목코드 (NULL=미부여)
recipes.product_code TEXT      -- 반제품 코드 (개정 체인이 공유 → UNIQUE 아님, 승계 대상)
```

- 기존 `material_aliases`(RM 별칭)는 유지 — `_resolve_erp_code` 폴백 체인에 남긴다.
- `blend_details.material_code`: 기존 기록은 불변(기록 원칙). **새 기록부터** 진짜 코드 저장
  (`get_recipe_for_blend` 의 `m.category AS material_code` → `m.code` 교체).

## 2. 이관 (마스터 임포트 + 자동 매칭)

1) `tools/import_item_codes.py` — code*.xlsx 4종 → item_code_master upsert.
   원자재는 대분류=원자재만 기본(--all 옵션으로 전체). 재실행=갱신.
2) `tools/match_item_codes.py` — 읽기 전용 보고서 우선:
   - materials.name(+aliases) ↔ 마스터(material) 정규화 일치 → materials.code 후보
   - recipes.product_name ↔ 마스터(product) → product_code + 분류 자동 후보
     (기존 category 지정과 충돌 시 보고만)
   - 모호(마스터 이름 중복 3건 등)·미매칭은 목록으로. `--apply` 로만 실제 반영.
3) 운영 스냅샷(.tmp-tests/prod-snapshot/irms.db)으로 리허설 → 매칭률 확정 → **운영 적용은 사용자 실행**.

## 3. 등록·수정·복사 흐름 변화 (중복 예방의 핵심)

임포트 검증(preview) 시 자재 헤더를 3단 판정:
- **A. 기존 자재(코드 보유)** → 그대로 (화면에 코드 표시)
- **B. 마스터에만 존재** → 자동 등록 + 코드 자동 부여 (경고 아님, 안내)
- **C. 어디에도 없음** → 🔴 **기본 차단**: "마스터에 없는 품목" + 유사 이름 후보 제시
  ("'카본블럭' — 혹시 '카본블랙'(AS00xx)?") → 명시적 확인(force)일 때만 코드 없이 등록

반제품명도 동일: 마스터 매칭 시 product_code·분류 자동 부여, 개정 시 승계(분류 승계는 이미 구현).

## 4. API / 화면

- `_resolve_erp_code` 우선순위: **materials.code** > RM 별칭 > RM형 코드/이름 > 별칭.
  → `/public/material-usage` **필드 구조 불변**(Dashboard 호환), erp_code 커버리지만 상승.
- 관리 화면: 자재 코드 열 + 레시피 현황 코드 열(+ 수동 부여 UI — 마스터에서 선택).
- 배합/기록 화면 '자재코드' 표기가 진짜 코드로.

## 5. 단계 분할 (zcode 위임 계획)

| 단계 | 내용 | 담당 |
|---|---|---|
| P1 | 마이그레이션(item_code_master, materials.code, recipes.product_code) + 임포트 스크립트 + 테스트 | zcode |
| P2 | 매칭 스크립트(보고서/--apply) + 스냅샷 리허설 | zcode(스크립트) + Claude(분석) |
| P3 | 임포트 검증 3단 판정 + product_code 승계 + 테스트 | zcode |
| P4 | erp_code 우선순위 + material_code 교체 + 테스트 | zcode |
| P5 | UI(검증 화면 코드/경고 표시, 관리 코드 열·부여) | Claude |
| P6 | 운영 이관 절차서(사용자 실행) | Claude |

## 6. 확정 사항 (2026-07-16 사용자 결정)

1. **마스터 임포트 범위**: 원자재는 대분류=원자재 **117행만** + 반제품(code2~4) 전부.
2. **마스터에 없는 신규 품목**: 기본 차단 + 유사 이름 안내, **명시적 확인 시 코드 없이 등록 허용**(나중에 코드 부여). ERP 등록 지연이 현장을 막지 않게.
3. **마스터 갱신**: 필요할 때 ERP 에서 엑셀 받아 **임포트 스크립트 재실행(upsert)**. 관리 UI 는 후순위.
