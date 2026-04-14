# Excel Recipe Migration & Compatibility Plan

> 기존 엑셀 레시피 파일(Excel_imge/*.png, excel/*.xlsx)과 IRMS 레시피 시스템의 호환성 개선 및 일괄 이관 계획서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | excel-recipe-migration |
| Priority | High |
| Base | IRMS v0.2+ (2026-04-14, recipe editor 기반) |
| Goal | 현장 엑셀 원본 7개 파일의 전 패턴을 IRMS에 무손실로 이관 가능하게 만든다 |
| Scope | 스키마 확장 + 엑셀 파서 + UI 정책 + TTS 보정 |

## 2. Problem Statement

현장에서 수년간 축적된 엑셀 레시피 파일은 IRMS가 가정한 "숫자 전용 / 단일 제품" 모델보다
훨씬 자유로운 형태로 작성되어 있다. 이 원본을 그대로 이관하려면 현재 스키마와 에디터가
수용하지 못하는 패턴이 여러 개 존재한다.

### 현재 Pain Points

1. **비고 필드 누락** — 엑셀 모든 시트의 마지막 컬럼 "비고"에 색상 별명(AMETHYST, BISQUE, TOYOTA 등)이 들어 있으나 `recipes` 테이블에 대응 컬럼 없음
2. **재료명 대소문자/공백 차이** — `TTO-55(B)` vs `TTO-55(b)` vs `TTO-55(b) ` 같은 변형이 혼재, `materials.name UNIQUE` 제약 때문에 정규화 필요
3. **혼합 값 입력** — `12.50 (HR10)`, `33 (RFC-5)`, `APB(17) 360` 같이 숫자+텍스트 혼재 셀이 실제로 존재
4. **파생 컬럼 셀 참조** — `55%(solution).xlsx`가 `=(D4*0.75)+(E4*0.8)+...` 같은 A1 셀 참조 수식을 광범위하게 사용하는데 IRMS 수식 엔진의 A1 참조 지원 여부 미검증
5. **일괄 이관 수단 없음** — 제품 수십 개를 한 번에 DB로 넣는 경로가 없어 수작업 등록 부담
6. **TTS 소음** — 값이 `-`(미사용)인 재료도 음성으로 읽어 현장 소음 유발

## 3. Feature Items

### 3.1 `recipes.remark` 컬럼 추가 (우선순위 🔴 1)

| Item | Detail |
|------|--------|
| 목표 | 엑셀 "비고" 컬럼의 색상 별명/메모를 레시피 단위로 저장 |
| 관련 파일 | `src/database.py`(스키마 + 마이그레이션), `src/routers/recipe_routes.py`, `static/js/spreadsheet_editor.js`, `templates/management.html` |
| DB 변경 | `ALTER TABLE recipes ADD COLUMN remark TEXT` (apply_schema_migrations에 등록) |
| UI | 스프레드시트 마지막 컬럼 "비고" 허용, 등록 시 remark에 저장 |

### 3.2 재료명 정규화 & alias 자동 매칭 (우선순위 🔴 2)

| Item | Detail |
|------|--------|
| 목표 | `TTO-55(B)` / `TTO-55(b)` / 공백차이를 같은 material로 흡수 |
| 관련 파일 | `src/database.py`, `src/services/material_resolver.py`(신규 또는 기존 import 파서), `material_aliases` 테이블 활용 |
| 전략 | 조회 시 `UPPER(TRIM(name))` 정규화 + `material_aliases`에 표기 변형 등록 |
| 검증 | 엑셀 7개 파일의 모든 재료명을 스크립트로 뽑아 unique 리스트 생성 → 기존 materials와 매칭 |

### 3.3 수식 엔진 A1 셀 참조 검증 & 보완 (우선순위 🟡 3)

| Item | Detail |
|------|--------|
| 목표 | `=(D4*0.75)+(E4*0.8)+(F4*0.75)` 같은 A1 참조가 동작하는지 확인, 미지원 시 추가 |
| 관련 파일 | `src/routers/spreadsheet_formulas.py` |
| 검증 항목 | `=SUM(D4:J4)` 범위 참조 / 개별 셀 곱셈 / `=70*0.9` 상수 산술 |
| 결과에 따라 | 이미 지원되면 파일럿 import 문서화, 미지원이면 파서 확장 |

### 3.4 혼합 값(숫자+메모) UI 정책 결정 (우선순위 🟡 4)

| Item | Detail |
|------|--------|
| 목표 | `12.50 (HR10)`, `APB(17) 360` 같은 혼재 셀을 어떻게 저장할지 정책 확정 |
| 옵션 A | 파싱 분리: `value_weight=12.50` + `value_text="HR10"` (이미 두 컬럼 존재) |
| 옵션 B | 전체 텍스트 저장: `value_text="12.50 (HR10)"`, `value_weight=NULL` |
| 권장 | **옵션 A** — TTS/계량 진행에서 숫자를 써야 하므로 분리가 유리 |
| 관련 파일 | `spreadsheet_editor.js` 셀 파싱, `recipe_routes.py` 저장 로직 |

### 3.5 TTS `-` 값 발화 생략 (우선순위 🟢 5)

| Item | Detail |
|------|--------|
| 목표 | 재료 값이 `-` / 빈 값이면 TTS 발화 생략, 음성 소음 감소 |
| 관련 파일 | `static/js/work.js` (renderWeighingPanel) |
| 변경량 | 2~3줄 |

### 3.6 xlsx 일괄 import 스크립트 (우선순위 🟢 6)

| Item | Detail |
|------|--------|
| 목표 | `excel/*.xlsx` 파일을 openpyxl로 파싱해 DB에 직접 주입하는 one-off 스크립트 |
| 관련 파일 | `scripts/import_excel_recipes.py`(신규), `src/database.py` |
| 처리 | 세로 병합 풀기, 섹션 분리(연속 배치), 재료명 정규화, 비고 매핑, 혼합값 파싱 |
| 안전장치 | `--dry-run` 플래그, 기존 레시피 중복 시 자동 버전업 활용 |

## 4. Out of Scope

- 엑셀 이미지(Excel_imge/*.png) OCR — 원본 xlsx가 있으면 그걸 쓴다
- 엑셀 파일 쌍방향 실시간 동기화 — 현재 목표는 일회성 이관
- 웹에서 xlsx 업로드 UI — 일단 CLI 스크립트로 충분, 필요 시 후속 작업

## 5. Implementation Order (권장 착수 순서)

1. **3.1 remark 컬럼** — 스키마 변경 먼저. 이후 작업들의 기준점
2. **3.5 TTS `-` 스킵** — 독립 변경, 빠르게 끝남, 바로 현장 체감
3. **3.3 수식 엔진 검증** — 읽기만 하면 되는 조사성 작업. 결과가 3.6 설계에 영향
4. **3.2 재료명 정규화** — 3.6 전에 필수. 엑셀에서 재료명 추출 스크립트 포함
5. **3.4 혼합 값 UI 정책** — 3.6의 파서 구현에 직접 영향
6. **3.6 일괄 import 스크립트** — 앞 5개를 기반으로 마무리

## 6. Risks & Unknowns

- **수식 엔진 A1 참조 미지원 시** — `spreadsheet_formulas.py` 확장이 필요해 범위 증가 가능
- **재료명 표기 불일치가 예상보다 클 경우** — material 테이블 대청소 필요, 현장 재료 담당자 확인 요청
- **혼합 값 파싱 규칙 충돌** — `12.50 (HR10)`는 분리 가능하지만 `APB(17) 360`처럼 숫자가 뒤에 오는 케이스는 파싱 룰 모호함
- **현장 운영 중단 없음 원칙** — 마이그레이션은 반드시 기존 레시피 데이터를 건드리지 않는 additive 변경

## 7. Success Criteria

- [ ] `recipes.remark`로 엑셀의 비고 컬럼 전체가 손실 없이 저장됨
- [ ] `excel/*.xlsx` 2개 파일 + `Excel_imge/*.png`가 가리키는 제품 7개가 import 스크립트로 한 번에 등록됨
- [ ] 등록된 레시피의 파생 컬럼(TOTAL/BINDER/분산제)이 에디터에서 수식으로 재계산됨
- [ ] 현장 계량 페이지에서 `-` 값 재료에 대해 TTS가 울리지 않음
- [ ] 기존 운영 중 레시피와 재료 데이터에 영향 없음

## 8. Deliverables

- 이 Plan 문서
- 후속: `docs/02-design/features/excel-recipe-migration.design.md`
- 구현: 위 3.1~3.6 코드 변경
- 파일럿 결과 보고서(수식 엔진 A1 참조 검증)
