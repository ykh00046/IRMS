# Recipe Version History Plan

> 레시피 자동 버전업 이력을 나란히 보고 이전 버전으로 되돌리기

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | recipe-version-history |
| Priority | Medium |
| Base | `recipes.revision_of` 컬럼 (이미 존재), 자동 버전업 (구현됨) |
| Goal | 한 제품·위치·잉크 조합에 대한 모든 버전을 시간순으로 나열하고, 버전 간 차이 비교 + 특정 버전 재등록(복제) 가능 |

## 2. Problem Statement

IRMS는 이미 레시피 저장 시 자동 버전업(`revision_of`)으로 이력을 남긴다.
그러나 **UI에서 이력을 볼 수 있는 경로가 없다**:

1. 책임자가 "어제 레시피는 뭐였지?" 확인하려면 SQL 직접 조회
2. 버전 간 어떤 재료/수량이 바뀌었는지 추적 불가
3. "예전 버전으로 되돌려달라"는 현장 요청에 수동 복제 입력 필요

## 3. Feature Items

### 3.1 버전 체인 조회 API

| Item | Detail |
|------|--------|
| 엔드포인트 | `GET /api/recipes/{id}/history` |
| 동작 | 해당 레시피가 속한 버전 체인 전체 반환 (root까지 `revision_of`를 따라가고, 같은 root를 가진 모든 후손 포함) |
| 응답 | `[{ id, version_label, created_by, created_at, item_count, is_current }, ...]` 시간순 |
| 권한 | operator 이상 |

### 3.2 버전 상세 비교

| Item | Detail |
|------|--------|
| 엔드포인트 | `GET /api/recipes/history/diff?base={id}&target={id}` |
| 응답 | `{ base: recipe, target: recipe, diff: [{ material_id, material_name, base_value, target_value, change: 'added'|'removed'|'modified'|'same' }] }` |
| 표시 | UI에서 2열로 나란히, 변경 항목 하이라이트 |

### 3.3 Management "레시피 조회" 탭 확장

| Item | Detail |
|------|--------|
| 위치 | 기존 `#tab-lookup` 확장 — 레시피 선택 시 "버전 이력" 버튼 노출 |
| 모달 | 버전 목록 + 선택 시 diff 비교 패널 |
| 액션 | 각 버전 행에 "이 버전 복제" 버튼 → 기존 `lookup-clone-btn` 로직 재사용 |

### 3.4 버전 라벨 규칙

| Item | Detail |
|------|--------|
| 형식 | `v1`, `v2`, ... (root=v1, revision chain 순서대로 증가) |
| 계산 | 백엔드에서 체인 순서 기준 index 부여 (DB 스키마 변경 없음) |
| 표시 | 버전 목록 + history 모달 타이틀 + 레시피 상세에 배지 |

### 3.5 "현재 버전" 판정

| Item | Detail |
|------|--------|
| 규칙 | 체인의 가장 최신(= 가장 큰 created_at) 레시피가 current |
| 표시 | current 행에 녹색 배지 "현재 사용" |

## 4. Scope

### In Scope
- 백엔드: 2개 신규 API (history, diff)
- 프론트: Lookup 탭에 버전 이력 모달 + 비교 UI
- 버전 라벨 자동 계산 (DB 변경 없음)

### Out of Scope
- 버전별 태그/코멘트
- 버전 삭제/병합
- 버전 승인 워크플로
- 전역 타임라인 뷰

## 5. Success Criteria

1. Lookup 탭에서 레시피 선택 → "버전 이력" 버튼 클릭 → 전체 체인 표시
2. 두 버전 선택 → diff 패널에 재료별 차이 표시 (추가/제거/변경/동일)
3. 특정 버전 "복제" → 해당 시점 내용이 편집기에 seed됨
4. 현재 버전 배지가 올바르게 표시됨

## 6. Open Questions

1. **체인 탐색 방향** — `revision_of`는 "이 레시피의 부모"만 가리킴. 정방향 후손을 찾으려면 전체 테이블에서 `WHERE revision_of IN (...)` 재귀 조회 필요. 성능상 괜찮은가? (일반적으로 체인 길이 < 20)
2. **같은 제품의 독립 레시피 구분** — product+position+ink가 같아도 revision_of 연결이 없는 별개 레시피가 있을 수 있다. 이력에 포함할 것인가, 순수 revision 체인만 볼 것인가?
3. **비교 기준** — 3개 이상 버전 비교 필요한가, 2개 버전 비교로 충분한가?
