# Excel Recipe Migration & Compatibility Design

> 엑셀 레시피 원본의 IRMS 무손실 이관 상세 설계서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | excel-recipe-migration |
| Plan | `docs/01-plan/features/excel-recipe-migration.plan.md` |
| Scope | 스키마 확장(remark) + 재료 정규화 + 혼합값 파서 + TTS 보정 + 일괄 import 스크립트 |
| 구현 순서 | 3.1 → 3.5 → (3.3 조사완료) → 3.2 → 3.4 → 3.6 |

## 2. 수식 엔진 호환성 조사 (3.3) — **사전 검증 완료**

`src/routers/spreadsheet_formulas.py` 분석 결과:

- 셀 참조는 열 문자만 사용 (`B1`, `D4` 모두 현재 행의 B열/D열로 해석됨, 행번호 무시)
- 지원 연산: `+`, `-`, `*`, `/`, 단항 부호, 괄호
- 지원 함수: `SUM(Xn:Yn)`, `ROUND(expr, digits)`
- 표현식 최대 200자

엑셀 원본 수식 분석:

| 원본 수식 | IRMS 평가 결과 | 판정 |
|---|---|---|
| `=SUM(D4:J4)` | D~J열 합산 | ✅ |
| `=(D4*0.75)+(E4*0.8)+(F4*0.75)+...` | 같은 행 D×0.75 + E×0.8 + ... | ✅ |
| `=K4-L4-M4` | K - L - M (같은 행) | ✅ |
| `=(L4*0.15)+(N4*0.1)` | L×0.15 + N×0.1 | ✅ |
| `=70*0.9` | 상수 산술 | ✅ |
| `=K4*0.05` | K×0.05 | ✅ |

**결론**: 파서/엔진 수정 없이 엑셀 수식을 **그대로 문자열 복사**해도 동일 결과가 나온다.
단 표현식 **200자 제한**에 걸리는 긴 수식(`=(D4*0.75)+(E4*0.8)+...` 7항)은 `=SUM` 분해 또는 제한 완화가 필요할 수 있음 → import 스크립트가 길이 체크 후 경고.

## 3. 스키마 변경 (3.1)

### 3.1.1 `recipes.remark` 컬럼 추가

```sql
ALTER TABLE recipes ADD COLUMN remark TEXT;
```

**적용 위치**: `src/database.py::apply_schema_migrations`

```python
def apply_schema_migrations(connection: sqlite3.Connection) -> None:
    ...
    _add_column_if_missing(connection, "recipes", "remark", "TEXT")
```

_`_add_column_if_missing`이 없다면 PRAGMA table_info로 컬럼 존재 여부 확인 후 ALTER 수행하는 헬퍼 신설._

### 3.1.2 영향 범위

| 파일 | 변경 |
|---|---|
| `src/database.py` | migration 추가 |
| `src/routers/recipe_routes.py` | `list_recipes`, `register_recipe`, `by-product` 응답에 `remark` 포함 |
| `src/routers/models.py` | `RecipeCreate` / 응답 모델에 `remark: str \| None = None` |
| `static/js/spreadsheet_editor.js` | 스프레드시트 마지막 컬럼이 "비고" 인식 → 등록 payload에 remark 필드 |
| `templates/management.html` | (필요 시) 레시피 상세/리스트에 비고 컬럼 |

**하위 호환**: `remark`는 NULL 허용. 기존 레시피 데이터 무영향.

## 4. 재료명 정규화 & alias (3.2)

### 4.1 정규화 규칙

```python
def normalize_material_name(name: str) -> str:
    return " ".join(name.strip().upper().split())
```

- 대소문자 통일: `TTO-55(B)` = `TTO-55(b)`
- 연속 공백 압축: `"APB  360"` → `"APB 360"`
- 앞뒤 공백 제거

### 4.2 조회 전략

**신규 함수** `src/services/material_resolver.py` (신규 파일):

```python
def resolve_material(connection, raw_name: str) -> int | None:
    """Return material_id by normalized name or alias. None if not found."""
    normalized = normalize_material_name(raw_name)
    # 1) materials.name 정규화 매칭
    row = connection.execute(
        "SELECT id FROM materials WHERE UPPER(TRIM(name)) = ?",
        (normalized,),
    ).fetchone()
    if row:
        return row["id"]
    # 2) material_aliases 매칭
    row = connection.execute(
        "SELECT material_id FROM material_aliases WHERE UPPER(TRIM(alias_name)) = ?",
        (normalized,),
    ).fetchone()
    return row["material_id"] if row else None
```

### 4.3 엑셀 재료명 prescan 스크립트

`scripts/prescan_excel_materials.py` (신규):

1. `excel/*.xlsx` 모든 시트를 openpyxl로 열기
2. "잉크명" 다음 컬럼부터 "비고" 이전까지를 재료명으로 수집
3. normalize → 고유 집합 생성
4. 기존 `materials` + `material_aliases`와 diff 출력
5. **미등록 재료 목록**을 사용자에게 제시 → 수동으로 alias 등록 또는 새 material 생성

출력 예:
```
[OK] 13 materials already match
[MISS] RAVEN    (suggest: alias of 'Raven Carbon Black')
[MISS] TS-6300  (suggest: new material)
```

## 5. 혼합 값(숫자+메모) 파서 (3.4)

### 5.1 정책 결정: **옵션 A (분리 저장)**

| 원본 셀 | 파싱 결과 |
|---|---|
| `12.50` | `value_weight=12.50`, `value_text=None` |
| `-` | `value_weight=None`, `value_text="-"` |
| `12.50 (HR10)` | `value_weight=12.50`, `value_text="HR10"` |
| `APB(17) 360` | `value_weight=360.0`, `value_text="APB(17)"` |
| `APB` | `value_weight=None`, `value_text="APB"` |
| `BYK-199 : 5` | `value_weight=5.0`, `value_text="BYK-199"` |

### 5.2 파서 알고리즘 (`src/services/cell_value_parser.py` 신규)

```python
_NUMBER_RE = re.compile(r"[-+]?\d+(?:\.\d+)?")

def parse_cell(raw: str) -> tuple[float | None, str | None]:
    raw = (raw or "").strip()
    if not raw or raw == "-":
        return None, raw or None

    # 전체가 순수 숫자면 숫자만
    try:
        return float(raw), None
    except ValueError:
        pass

    # 숫자 토큰 1개 이상 추출
    matches = _NUMBER_RE.findall(raw)
    if not matches:
        return None, raw

    # 마지막 숫자를 value_weight로, 숫자 제거한 나머지를 text로
    numeric = float(matches[-1])
    text = _NUMBER_RE.sub(" ", raw).strip(" :()[]").strip() or None
    return numeric, text
```

**우선순위 규칙**: 마지막 숫자를 채택. `APB(17) 360`에서 최종 계량값은 360, `APB(17)`은 보조 메모.
_이 규칙은 현장 검증 필요 — import 스크립트가 dry-run으로 모든 파싱 결과를 리포트한다._

### 5.3 UI 영향

- 스프레드시트 에디터는 그대로 자유 텍스트 입력 허용
- 등록 시 서버에서 `parse_cell` 호출 → `recipe_items.value_weight` + `value_text` 저장
- 조회 시 렌더링 규칙: `value_weight` 있으면 `"{number} ({text})"` 아니면 `value_text`만

## 6. TTS `-` 스킵 (3.5)

**파일**: `static/js/work.js` renderWeighingPanel

```diff
     const stepKey = `${current.recipeId}:${current.materialId}`;
     if (weighing.lastSpokenStepKey !== stepKey) {
       weighing.lastSpokenStepKey = stepKey;
-      IRMS.speakText(`${current.materialName}, ${current.targetValue}`);
+      const val = String(current.targetValue || "").trim();
+      if (val && val !== "-") {
+        IRMS.speakText(`${current.materialName}, ${val}`);
+      }
     }
```

**추가 고려**: queue 생성 단계에서 `value_weight=NULL && value_text="-"` 스텝 자체를 **제외**할 수도 있음. 하지만 현장 작업자에게 "사용 안 함" 표시가 필요할 수 있어 UI에는 노출 유지, TTS만 생략.

## 7. 일괄 Import 스크립트 (3.6)

### 7.1 위치 & 사용법

```
scripts/import_excel_recipes.py <xlsx_path> [--dry-run] [--created-by USERNAME]
```

### 7.2 처리 파이프라인

```
1. openpyxl로 워크북 열기 (data_only=False 로 수식 문자열 보존)
2. 시트별로 섹션 탐지
   - "제품명" 헤더 row 패턴 인식
   - 섹션 경계 = 빈 row 또는 새 헤더 row
3. 섹션 내 각 레시피 row 추출
   - 제품명 (세로 병합 풀기 → 이전 row 값 계승)
   - 위치 / 잉크명 / 재료별 값 / 비고
4. 재료명 resolver 호출 (4.2)
   - 미등록 재료는 FAIL 처리 → 전체 중단 + 누락 목록 출력
5. 각 셀 값 parse_cell (5.2)
6. 레시피 중복 체크 → 활성 레시피와 동일 product_name이면 자동 버전업
7. --dry-run 모드: INSERT 없이 리포트만 출력
8. 실제 모드: 트랜잭션 단일 commit
```

### 7.3 출력 리포트 예시

```
=== Import Summary (dry-run) ===
File: excel/55%(solution).xlsx
Sheets: 26년 신규 양산 (6 rows → 1 product × 3 positions)

[OK]   Star Glow A / 1도 / LISSE(PL) → 7 materials, 1 formula
[OK]   Star Glow A / 2도 / NOISETTE(PL) → 7 materials, 1 formula
[WARN] Star Glow A / 3도 / TEST-1(PL): 1 cell contained mixed value
       PL-150-2: parsed as weight=63.0 (from "=70*0.9" → precomputed)
       Note: formula preserved as-is in value_text

Materials missing from DB: (none)
Total recipes to create: 3
Total items: 21
```

### 7.4 오류 처리

- 미등록 재료 발견 → 전체 중단, `ERROR: Unknown material 'XXX' in row N` 출력
- 제품명/잉크명 누락 → 해당 row skip, 경고 출력
- 수식 200자 초과 → 경고 + 원본 문자열 그대로 value_text에 저장

## 8. API 변경

### 8.1 POST `/api/recipes/register`

**기존 payload 확장**:

```json
{
  "product_name": "Amethyst",
  "position": "1도",
  "ink_name": "TEST-3",
  "remark": "AMETHYST",          ← 신규
  "items": [
    {"material_name": "RAVEN", "raw_value": "1.25"},
    {"material_name": "BLACK", "raw_value": "2.65"},
    {"material_name": "PB", "raw_value": "APB(17)"}
  ]
}
```

서버가 `raw_value` → `parse_cell` → `value_weight`, `value_text` 저장.

### 8.2 GET `/api/recipes/{id}` 응답

```json
{
  "id": 42,
  "product_name": "Amethyst",
  "position": "1도",
  "ink_name": "TEST-3",
  "remark": "AMETHYST",
  "items": [
    {"material_name": "RAVEN", "value_weight": 1.25, "value_text": null},
    {"material_name": "PB", "value_weight": null, "value_text": "APB(17)"}
  ]
}
```

## 9. 테스트 전략

### 9.1 단위 테스트 (신규)

- `tests/test_cell_value_parser.py`: 5.1 표의 모든 케이스
- `tests/test_material_resolver.py`: normalize + alias 매칭 + 미매칭 None 반환
- `tests/test_schema_migration.py`: remark 컬럼 없는 기존 DB에 마이그레이션 적용 후 컬럼 존재 확인

### 9.2 통합 테스트

- `excel/55%(powder).xlsx` + `excel/55%(solution).xlsx` 두 파일에 대해 `import_excel_recipes.py --dry-run` 실행 → 경고/에러 목록 검토
- Powder 파일은 혼합값("12.50 (HR10)")이 주 테스트 케이스
- Solution 파일은 수식 보존이 주 테스트 케이스

### 9.3 수동 QA

- 운영 DB 복사본에 실제 import → management 탭에서 등록 결과 확인
- 계량 페이지에서 `-` 재료 스텝에 TTS 발화 안 되는지 현장 확인

## 10. Rollout

1. **로컬 개발 환경**에서 schema migration + parser + TTS 변경 커밋
2. **dry-run**으로 2개 xlsx 파일 검증, 리포트 사용자 승인
3. 승인 후 **백업 → 실제 import**
4. management 탭에서 등록 결과 육안 확인
5. 계량 페이지 현장 스모크 테스트

## 11. Open Questions

1. **제품명 세로 병합이 풀렸을 때 동일 product_name+position 조합이 기존 DB에 있으면?**
   - A안: 자동 버전업 (권장, 기존 로직 재사용)
   - B안: import 스크립트가 skip + 경고

2. **비고 컬럼이 NULL인 기존 레시피 UI 표시는?**
   - A안: 빈 셀
   - B안: 대시(-)

3. **`APB(17) 360`에서 "17"을 무시하는 파서 규칙이 모든 케이스에 맞는가?**
   - 현장 재료 담당자 확인 필요

4. **수식 200자 제한 상향?**
   - 현재 7항 × 10자 = 70자 내외라 여유 있음, 유지

## 12. Deliverables

- 이 Design 문서
- 코드: 3.1~3.6 구현
- 테스트: 9.1, 9.2
- Import 리포트 1부 (dry-run 결과)
- 후속: `docs/03-analysis/excel-recipe-migration.analysis.md` (Check 단계)
