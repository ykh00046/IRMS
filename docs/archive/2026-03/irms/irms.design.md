# IRMS — 설계서 (Design Document)

> **Summary**: IRMS 시스템의 기술적 설계 — 아키텍처, 데이터 모델, API 명세, UI/UX, 에러 처리, 보안, 테스트 계획
>
> **Author**: IRMS Team
> **Created**: 2026-03-06
> **Last Modified**: 2026-03-06
> **Status**: Approved

---

## 1. 아키텍처 설계

### 1.1 전체 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│  Client Layer (Browser)                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Work     │  │ Mgmt     │  │ Insight  │  │ Admin    │       │
│  │ Mode     │  │ Mode     │  │ Mode     │  │ Panel    │       │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └────┬─────┘       │
│       └──────────────┼──────────────┼──────────────┘            │
│                      │ HTTP REST + SSE                          │
├──────────────────────┼──────────────────────────────────────────┤
│  Server Layer        │                                          │
│              ┌───────▼────────┐                                 │
│              │   FastAPI App  │                                  │
│              │  ┌───────────┐ │                                  │
│              │  │ Routers   │ │  /api/auth, /api/recipes,       │
│              │  │           │ │  /api/materials, /api/stats,    │
│              │  │           │ │  /api/audit, /api/health        │
│              │  └─────┬─────┘ │                                  │
│              │  ┌─────▼─────┐ │                                  │
│              │  │ Services  │ │  비즈니스 로직                    │
│              │  └─────┬─────┘ │                                  │
│              │  ┌─────▼─────┐ │                                  │
│              │  │ Repository│ │  DB 접근 계층                     │
│              │  └─────┬─────┘ │                                  │
│              └────────┼───────┘                                  │
│                       │                                          │
├───────────────────────┼──────────────────────────────────────────┤
│  Data Layer           │                                          │
│              ┌────────▼───────┐                                  │
│              │   SQLite DB    │  irms.db                         │
│              └────────────────┘                                  │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 프로젝트 디렉토리 구조

```
IRMS/
├── docs/                          # PDCA 문서
│   ├── _INDEX.md
│   ├── 01-plan/features/
│   ├── 02-design/features/
│   ├── 03-analysis/
│   └── 04-report/features/
│
├── src/                           # 소스 코드
│   ├── main.py                    # FastAPI 앱 진입점
│   ├── config.py                  # 설정 (DB경로, 세션, 환경변수)
│   ├── database.py                # SQLite 연결, 테이블 초기화
│   │
│   ├── models/                    # Pydantic 모델 (Request/Response)
│   │   ├── __init__.py
│   │   ├── auth.py                # LoginRequest, UserResponse
│   │   ├── recipe.py              # RecipeCreate, RecipeResponse, ...
│   │   ├── material.py            # MaterialCreate, MaterialResponse, ...
│   │   └── common.py              # Pagination, ErrorResponse
│   │
│   ├── routers/                   # API 라우터
│   │   ├── __init__.py
│   │   ├── auth.py                # 인증 (로그인/로그아웃)
│   │   ├── recipes.py             # 레시피 CRUD + 상태 전이
│   │   ├── materials.py           # 원재료 마스터 CRUD
│   │   ├── stats.py               # 소비 통계 + Export
│   │   ├── audit.py               # 감사 로그 조회
│   │   ├── pages.py               # HTML 페이지 라우트
│   │   └── health.py              # 헬스 체크
│   │
│   ├── services/                  # 비즈니스 로직
│   │   ├── __init__.py
│   │   ├── auth_service.py        # 인증, 세션 관리
│   │   ├── recipe_service.py      # 레시피 관리, 상태 전이
│   │   ├── import_service.py      # Smart Import (파싱, 검증)
│   │   ├── material_service.py    # 원재료 마스터 관리
│   │   ├── stats_service.py       # 통계 집계, 엑셀 Export
│   │   ├── audit_service.py       # 감사 로그 기록/조회
│   │   └── sse_service.py         # Server-Sent Events 관리
│   │
│   ├── repositories/              # DB 접근 계층
│   │   ├── __init__.py
│   │   ├── user_repo.py
│   │   ├── recipe_repo.py
│   │   ├── material_repo.py
│   │   └── audit_repo.py
│   │
│   └── utils/                     # 유틸리티
│       ├── __init__.py
│       ├── header_normalizer.py   # 헤더 정규화 로직
│       ├── parser.py              # 값 파싱 (중량/코드)
│       ├── validator.py           # 3단계 Validation
│       └── excel_export.py        # 엑셀 파일 생성
│
├── static/                        # 프론트엔드 정적 파일
│   ├── css/
│   │   ├── common.css             # 공통 스타일, 변수
│   │   ├── work.css               # Work Mode 전용
│   │   ├── management.css         # Management 전용
│   │   └── insight.css            # Insight 전용
│   ├── js/
│   │   ├── common.js              # 공통 유틸 (API 호출, 알림)
│   │   ├── work.js                # Work Mode 로직
│   │   ├── management.js          # Management 로직
│   │   ├── insight.js             # Insight 로직
│   │   ├── smart-import.js        # 붙여넣기 파싱/매핑
│   │   └── sse-client.js          # SSE 수신기
│   └── img/
│
├── templates/                     # Jinja2 HTML 템플릿
│   ├── base.html                  # 공통 레이아웃 (네비게이션)
│   ├── login.html
│   ├── work.html                  # Work Mode
│   ├── management.html            # Management
│   ├── insight.html               # Insight
│   └── admin/
│       ├── materials.html         # 원재료 관리
│       └── users.html             # 사용자 관리
│
├── tests/                         # 테스트
│   ├── test_import_service.py
│   ├── test_recipe_service.py
│   ├── test_material_service.py
│   └── test_api.py
│
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

### 1.3 데이터 흐름

#### Smart Import 흐름

```
사용자가 엑셀에서 복사
        │
        ▼
┌─── 붙여넣기 (Clipboard) ───┐
│  "제품명  BYK-199  카본..."  │
└───────────┬────────────────┘
            │ (클라이언트)
            ▼
    header_normalizer.py
    ├── 공백/특수문자 제거
    ├── 대문자 통일
    └── Alias 매핑 (DB 조회)
            │
            ▼
    parser.py
    ├── 필수 컬럼 분리 (제품명, 위치, 잉크명)
    ├── 원재료 컬럼 식별
    └── 값 파싱 (숫자 → float, 코드 → string)
            │
            ▼
    validator.py
    ├── Level 1: 미등록 원재료 → ERROR
    ├── Level 2: unit_type 불일치 → ERROR
    └── Level 3: 범위 이탈 → WARN
            │
            ▼
    ┌── 결과 ──┐
    │ ERROR    │ → 사용자에게 오류 표시 → 수정 후 재시도
    │ WARN     │ → 경고 + 미리보기 표시 → 사용자 확인 후 저장
    │ OK       │ → 미리보기 표시 → 사용자 확인 후 저장
    └──────────┘
            │
            ▼
    recipe_service.py
    ├── recipes 레코드 생성 (status: pending)
    ├── recipe_items 레코드 생성 (원재료별)
    ├── raw_input 원문 저장
    └── audit_log 기록 (create)
```

#### Work Mode 완료 흐름

```
작업자가 '완료' 버튼 클릭
        │
        ▼
    확인 팝업 (2단 확인)
        │ (확인)
        ▼
    recipe_service.py
    ├── 상태 검증 (Pending/In-Progress만 허용)
    ├── status → completed
    ├── completed_by, completed_at 기록
    └── audit_log 기록 (complete)
        │
        ▼
    sse_service.py
    └── 전체 접속 클라이언트에 "recipe_completed" 이벤트 발행
        │
        ▼
    각 브라우저
    └── 해당 행 화면에서 제거 (애니메이션)
```

---

## 2. 데이터 모델

### 2.1 DDL (SQLite)

```sql
-- 사용자
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    display_name  TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user'
                          CHECK (role IN ('admin', 'user')),
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 원재료 마스터
CREATE TABLE materials (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT    NOT NULL UNIQUE,
    unit_type     TEXT    NOT NULL CHECK (unit_type IN ('weight', 'count')),
    unit          TEXT    NOT NULL DEFAULT 'kg',
    color_group   TEXT    NOT NULL DEFAULT 'none'
                          CHECK (color_group IN ('black', 'red', 'blue', 'yellow', 'none')),
    category      TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 원재료 동의어
CREATE TABLE material_aliases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    material_id   INTEGER NOT NULL REFERENCES materials(id),
    alias_name    TEXT    NOT NULL UNIQUE
);

-- 레시피
CREATE TABLE recipes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_name   TEXT    NOT NULL,
    position       TEXT,
    ink_name       TEXT    NOT NULL,
    note           TEXT,
    status         TEXT    NOT NULL DEFAULT 'pending'
                           CHECK (status IN ('draft', 'pending', 'in_progress', 'completed', 'canceled')),
    cancel_reason  TEXT,
    created_by     INTEGER NOT NULL REFERENCES users(id),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now', 'localtime')),
    started_by     INTEGER REFERENCES users(id),
    started_at     TEXT,
    completed_by   INTEGER REFERENCES users(id),
    completed_at   TEXT,
    raw_input_hash TEXT,
    raw_input_text TEXT,
    revision_of    INTEGER REFERENCES recipes(id)
);

-- 레시피 투입 항목
CREATE TABLE recipe_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    recipe_id     INTEGER NOT NULL REFERENCES recipes(id) ON DELETE CASCADE,
    material_id   INTEGER NOT NULL REFERENCES materials(id),
    value_weight  REAL,
    value_text    TEXT
);

-- 감사 로그
CREATE TABLE audit_logs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    event_type    TEXT    NOT NULL,
    target_type   TEXT    NOT NULL,
    target_id     INTEGER,
    detail         TEXT,
    ip_address    TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
);
```

### 2.2 인덱스

```sql
-- 레시피 조회 최적화
CREATE INDEX idx_recipes_status ON recipes(status);
CREATE INDEX idx_recipes_created_by ON recipes(created_by);
CREATE INDEX idx_recipes_created_at ON recipes(created_at);
CREATE INDEX idx_recipes_product_name ON recipes(product_name);

-- 레시피 아이템 조회
CREATE INDEX idx_recipe_items_recipe_id ON recipe_items(recipe_id);
CREATE INDEX idx_recipe_items_material_id ON recipe_items(material_id);

-- Alias 조회 (Import 시 빈번)
CREATE INDEX idx_material_aliases_alias ON material_aliases(alias_name);

-- 감사 로그 조회
CREATE INDEX idx_audit_logs_target ON audit_logs(target_type, target_id);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id);
```

### 2.3 초기 데이터

```sql
-- 기본 관리자 계정 (비밀번호: admin123 → bcrypt 해시)
INSERT INTO users (username, password_hash, display_name, role)
VALUES ('admin', '$2b$12$...hashed...', '관리자', 'admin');
```

---

## 3. API 명세

### 3.1 인증 API

| Method | Path               | 설명             | Auth |
| ------ | ------------------ | ---------------- | :--: |
| POST   | `/api/auth/login`  | 로그인           |  ✕   |
| POST   | `/api/auth/logout` | 로그아웃         |  ○   |
| GET    | `/api/auth/me`     | 현재 사용자 정보 |  ○   |

#### POST `/api/auth/login`

```json
// Request
{
  "username": "admin",
  "password": "admin123"
}

// Response 200
{
  "user": {
    "id": 1,
    "username": "admin",
    "display_name": "관리자",
    "role": "admin"
  }
}

// Response 401
{
  "error": "INVALID_CREDENTIALS",
  "message": "사용자명 또는 비밀번호가 올바르지 않습니다."
}
```

---

### 3.2 레시피 API

| Method | Path                          | 설명                | Auth | Role  |
| ------ | ----------------------------- | ------------------- | :--: | :---: |
| GET    | `/api/recipes`                | 레시피 목록 (필터)  |  ○   |  all  |
| GET    | `/api/recipes/{id}`           | 레시피 상세         |  ○   |  all  |
| POST   | `/api/recipes/import`         | Smart Import (생성) |  ○   |  all  |
| POST   | `/api/recipes/import/preview` | Import 미리보기     |  ○   |  all  |
| PATCH  | `/api/recipes/{id}/status`    | 상태 변경           |  ○   |  all  |
| DELETE | `/api/recipes/{id}`           | 삭제 (Draft만)      |  ○   | admin |

#### GET `/api/recipes`

```
Query Parameters:
  status    : string   (pending, in_progress, completed, canceled)
  search    : string   (제품명/잉크명 검색)
  created_by: integer  (등록자 ID)
  date_from : string   (YYYY-MM-DD)
  date_to   : string   (YYYY-MM-DD)
  page      : integer  (기본값: 1)
  per_page  : integer  (기본값: 50)
```

```json
// Response 200
{
  "items": [
    {
      "id": 1,
      "product_name": "제품A",
      "position": "1도",
      "ink_name": "잉크B",
      "status": "pending",
      "created_by": { "id": 2, "display_name": "작업자1" },
      "created_at": "2026-03-06T09:00:00",
      "materials": [
        {
          "material_id": 10,
          "material_name": "BYK-199",
          "color_group": "none",
          "unit_type": "weight",
          "value_weight": 1.5,
          "value_text": null
        }
      ]
    }
  ],
  "total": 25,
  "page": 1,
  "per_page": 50
}
```

#### POST `/api/recipes/import/preview`

```json
// Request
{
  "raw_text": "제품명\t위치\t잉크명\tBYK-199\t카본블랙\n제품A\t1도\t잉크B\t1.5\t0.3"
}

// Response 200 (검증 성공)
{
  "status": "ok",
  "warnings": [],
  "errors": [],
  "preview": {
    "product_name": "제품A",
    "position": "1도",
    "ink_name": "잉크B",
    "items": [
      { "material_name": "BYK-199", "resolved_id": 10, "value": 1.5, "type": "weight" },
      { "material_name": "카본블랙", "resolved_id": 15, "value": 0.3, "type": "weight" }
    ]
  }
}

// Response 200 (검증 실패)
{
  "status": "error",
  "errors": [
    { "level": 1, "message": "미등록 원재료: UNKNOWN-MAT", "column": "UNKNOWN-MAT" }
  ],
  "warnings": [
    { "level": 3, "message": "값 10500 — 이상치 의심", "column": "BYK-199", "row": 1 }
  ],
  "preview": null
}
```

#### PATCH `/api/recipes/{id}/status`

```json
// Request — 작업 시작
{ "action": "start" }

// Request — 완료
{ "action": "complete" }

// Request — 취소
{ "action": "cancel", "reason": "잘못된 배합표 등록" }

// Response 200
{
  "id": 1,
  "status": "completed",
  "completed_by": { "id": 2, "display_name": "작업자1" },
  "completed_at": "2026-03-06T15:30:00"
}

// Response 409 (상태 전이 불가)
{
  "error": "INVALID_STATUS_TRANSITION",
  "message": "Completed 상태에서는 변경할 수 없습니다."
}
```

---

### 3.3 원재료 API

| Method | Path                                     | 설명                | Auth | Role  |
| ------ | ---------------------------------------- | ------------------- | :--: | :---: |
| GET    | `/api/materials`                         | 목록 (필터)         |  ○   |  all  |
| GET    | `/api/materials/{id}`                    | 상세 (aliases 포함) |  ○   |  all  |
| POST   | `/api/materials`                         | 등록                |  ○   | admin |
| PUT    | `/api/materials/{id}`                    | 수정                |  ○   | admin |
| PATCH  | `/api/materials/{id}/deactivate`         | 비활성화            |  ○   | admin |
| POST   | `/api/materials/{id}/aliases`            | Alias 추가          |  ○   | admin |
| DELETE | `/api/materials/{id}/aliases/{alias_id}` | Alias 삭제          |  ○   | admin |

#### POST `/api/materials`

```json
// Request
{
  "name": "BYK-199",
  "unit_type": "weight",
  "unit": "kg",
  "color_group": "none",
  "category": "첨가제",
  "aliases": ["BYK199", "BYK 199"]
}

// Response 201
{
  "id": 10,
  "name": "BYK-199",
  "unit_type": "weight",
  "unit": "kg",
  "color_group": "none",
  "category": "첨가제",
  "is_active": true,
  "aliases": [
    { "id": 1, "alias_name": "BYK199" },
    { "id": 2, "alias_name": "BYK 199" }
  ]
}
```

---

### 3.4 통계 API

| Method | Path                     | 설명           | Auth |
| ------ | ------------------------ | -------------- | :--: |
| GET    | `/api/stats/consumption` | 소비 통계 조회 |  ○   |
| GET    | `/api/stats/export`      | 엑셀 다운로드  |  ○   |

#### GET `/api/stats/consumption`

```
Query Parameters:
  date_from   : string (YYYY-MM-DD, 필수)
  date_to     : string (YYYY-MM-DD, 필수)
  color_group : string (선택)
  category    : string (선택)
```

```json
// Response 200
{
  "period": { "from": "2026-03-01", "to": "2026-03-06" },
  "items": [
    {
      "material_id": 10,
      "material_name": "BYK-199",
      "unit_type": "weight",
      "unit": "kg",
      "color_group": "none",
      "total_weight": 45.7,
      "recipe_count": 12
    },
    {
      "material_id": 20,
      "material_name": "PB-APB",
      "unit_type": "count",
      "unit": "ea",
      "color_group": "black",
      "total_count": 8,
      "recipe_count": 8
    }
  ]
}
```

---

### 3.5 감사 로그 API

| Method | Path         | 설명      | Auth | Role  |
| ------ | ------------ | --------- | :--: | :---: |
| GET    | `/api/audit` | 로그 조회 |  ○   | admin |

---

### 3.6 SSE API

| Method | Path          | 설명            | Auth |
| ------ | ------------- | --------------- | :--: |
| GET    | `/api/events` | SSE 스트림 연결 |  ○   |

#### 이벤트 타입

```
event: recipe_created
data: {"recipe_id": 1, "product_name": "제품A"}

event: recipe_completed
data: {"recipe_id": 1, "completed_by": "작업자1"}

event: recipe_canceled
data: {"recipe_id": 1}

event: recipe_started
data: {"recipe_id": 1, "started_by": "작업자1"}
```

---

### 3.7 기타 API

| Method | Path               | 설명               |   Auth    |
| ------ | ------------------ | ------------------ | :-------: |
| GET    | `/health`          | 헬스 체크          |     ✕     |
| GET    | `/`                | Work Mode 페이지   |     ○     |
| GET    | `/management`      | Management 페이지  |     ○     |
| GET    | `/insight`         | Insight 페이지     |     ○     |
| GET    | `/admin/materials` | 원재료 관리 페이지 | ○ (admin) |
| GET    | `/admin/users`     | 사용자 관리 페이지 | ○ (admin) |
| GET    | `/login`           | 로그인 페이지      |     ✕     |

---

## 4. UI/UX 설계

### 4.1 네비게이션 구조

```
┌───────────────────────────────────────────────────────┐
│  🎨 IRMS    [Work]  [Management]  [Insight]   👤 로그아웃 │
├───────────────────────────────────────────────────────┤
│                                                       │
│                  (각 화면 콘텐츠)                       │
│                                                       │
└───────────────────────────────────────────────────────┘
```

- 관리자 전용: `[Admin▾]` 드롭다운 → 원재료 관리 / 사용자 관리

### 4.2 [화면 1] Work Mode

```
┌─────────────────────────────────────────────────────────────────┐
│  Color Focus:  [전체] [■ Black] [■ Red] [■ Blue] [■ Yellow]     │
│  검색: [___________🔍]   날짜: [from] ~ [to]                     │
├──────────┬────────┬─────────┬──────┬──────┬──────┬──────────────┤
│ 제품명 ⓕ │ 위치   │ 잉크명  │ BYK  │ 카본  │ RED  │ 완료 ⓕ       │
│ (sticky) │        │         │ -199 │ 블랙  │ 안료 │ (sticky)     │
├──────────┼────────┼─────────┼──────┼──────┼──────┼──────────────┤
│ 제품A    │ 1도    │ 잉크B   │ 1.5  │ 0.3  │  —   │ [✓ 완료]     │
│ 제품B    │ 2도    │ 잉크C   │ 2.0  │  —   │ 0.8  │ [✓ 완료]     │
│ ...      │        │         │      │      │      │              │
└──────────┴────────┴─────────┴──────┴──────┴──────┴──────────────┘
```

**핵심 동작:**

- ⓕ = Sticky (가로 스크롤 시 고정)
- Color Focus 클릭 → 해당 color_group 원재료 컬럼만 표시, 나머지 숨김
- `[전체]` 클릭 → 모든 컬럼 표시
- `[✓ 완료]` → 확인 팝업 → 완료 처리 → 행 fade-out
- 원재료 컬럼은 레시피에 포함된 것만 동적 생성 (유동 헤더)

#### Focus Mode 필터 로직

```javascript
// color_group별 material_id 셋
const colorGroups = {
  black: [15, 22, 33], // 카본블랙, ...
  red: [41, 42], // RED 안료, ...
  blue: [51, 52],
  yellow: [61, 62],
};

function activateFocus(color) {
  document.querySelectorAll(".material-col").forEach((col) => {
    const matId = parseInt(col.dataset.materialId);
    col.style.display = colorGroups[color].includes(matId) ? "" : "none";
  });
}
```

### 4.3 [화면 2] Management

```
┌─────────────────────────────────────────────────────────────────┐
│  레시피 등록                                                     │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                                                           │  │
│  │      엑셀 데이터를 여기에 붙여넣으세요 (Ctrl+V)              │  │
│  │                                                           │  │
│  └───────────────────────────────────────────────────────────┘  │
│  [미리보기 검증]                                                 │
│                                                                 │
│  ── 미리보기 결과 ──────────────────────────────────────────     │
│  ⚠️ WARN: 값 10500 — 이상치 의심 (BYK-199, 행 1)                │
│  ┌──────────┬────────┬─────────┬──────┬──────┐                  │
│  │ 제품명   │ 위치   │ 잉크명   │ BYK  │ 카본  │                  │
│  ├──────────┼────────┼─────────┼──────┼──────┤                  │
│  │ 제품A    │ 1도    │ 잉크B   │ 1.5  │ 0.3  │                  │
│  └──────────┴────────┴─────────┴──────┴──────┘                  │
│  [등록]  [취소]                                                  │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│  레시피 조회                                                     │
│  필터: [등록자▾] [상태▾] [from]~[to] [제품명___🔍]              │
│  ┌──────┬──────────┬────────┬─────────┬────────┬────────┐      │
│  │ ID   │ 제품명    │ 잉크명  │ 상태    │ 등록자  │ 등록일  │      │
│  ├──────┼──────────┼────────┼─────────┼────────┼────────┤      │
│  │ 001  │ 제품A    │ 잉크B  │ 🟢대기  │ 작업자1 │ 03-06  │      │
│  │ 002  │ 제품B    │ 잉크C  │ 🔵진행  │ 작업자2 │ 03-05  │      │
│  └──────┴──────────┴────────┴─────────┴────────┴────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### 4.4 [화면 3] Insight

```
┌─────────────────────────────────────────────────────────────────┐
│  원재료 소비 통계                                                │
│  기간: [2026-03-01] ~ [2026-03-06]  [색상▾] [분류▾]  [조회]      │
│  [📥 엑셀 다운로드]                                              │
│                                                                 │
│  ── 중량 집계 (Weight) ────────────────────────────────────────  │
│  ┌──────────┬──────────┬──────┬────────┬───────────┐           │
│  │ 원재료   │ 색상     │ 단위 │ 합계   │ 사용 횟수  │           │
│  ├──────────┼──────────┼──────┼────────┼───────────┤           │
│  │ BYK-199  │ —        │ kg   │ 45.70  │ 12건      │           │
│  │ 카본블랙 │ ■ Black  │ kg   │ 23.40  │ 8건       │           │
│  └──────────┴──────────┴──────┴────────┴───────────┘           │
│                                                                 │
│  ── 횟수 집계 (Count) ─────────────────────────────────────────  │
│  ┌──────────┬──────────┬──────────┐                             │
│  │ 원재료   │ 색상     │ 사용 횟수 │                             │
│  ├──────────┼──────────┼──────────┤                             │
│  │ PB-APB   │ ■ Black  │ 8회      │                             │
│  └──────────┴──────────┴──────────┘                             │
└─────────────────────────────────────────────────────────────────┘
```

### 4.5 터치 단말 고려사항

| 항목           | 적용 사항                                |
| -------------- | ---------------------------------------- |
| 최소 터치 영역 | 44×44px 이상                             |
| 완료 버튼      | 길게 누르기 또는 확인 팝업 (오작동 방지) |
| 폰트 크기      | 최소 16px (테이블 14px, 버튼 16px)       |
| 가로 스크롤    | Sticky 컬럼으로 맥락 유지                |

---

## 5. 에러 처리

### 5.1 HTTP 에러 코드

| 코드 | 용도                                  |
| ---- | ------------------------------------- |
| 400  | Validation 실패, 잘못된 요청          |
| 401  | 인증 실패 / 세션 만료                 |
| 403  | 권한 부족 (일반 사용자 → 관리자 기능) |
| 404  | 리소스 없음                           |
| 409  | 상태 전이 충돌 (Completed → start 등) |
| 500  | 서버 내부 오류                        |

### 5.2 에러 응답 형식

```json
{
  "error": "ERROR_CODE",
  "message": "사용자에게 보여줄 메시지",
  "details": {}
}
```

### 5.3 상태 전이 검증 (서버 사이드)

```python
VALID_TRANSITIONS = {
    'draft':       ['pending', 'canceled'],
    'pending':     ['in_progress', 'canceled'],
    'in_progress': ['completed', 'canceled'],
    'completed':   [],          # 변경 불가
    'canceled':    [],          # 변경 불가
}

def validate_transition(current: str, target: str) -> bool:
    return target in VALID_TRANSITIONS.get(current, [])
```

---

## 6. 보안

### 6.1 인증 / 세션

- **비밀번호**: bcrypt 해시 저장 (cost factor: 12)
- **세션**: 서버 사이드 세션 (UUID 기반 쿠키)
- **만료**: 8시간 자동 만료, 로그아웃 시 즉시 무효화
- **쿠키**: `HttpOnly`, `SameSite=Strict` (폐쇄망이므로 Secure 생략 가능)

### 6.2 접근 제어

```python
# 미들웨어에서 역할 체크
def require_role(role: str):
    def decorator(func):
        async def wrapper(request, *args, **kwargs):
            user = get_current_user(request)
            if user.role != role:
                raise HTTPException(403, "권한이 부족합니다.")
            return await func(request, *args, **kwargs)
        return wrapper
    return decorator
```

### 6.3 입력 검증

- SQL Injection 방지: 파라미터 바인딩 사용 (raw SQL 금지)
- XSS 방지: Jinja2 자동 이스케이프 + 사용자 입력 sanitize
- CSRF: 폐쇄망 + SameSite 쿠키로 최소 대응 (PoC)

---

## 7. 테스트 계획

### 7.1 단위 테스트

| 대상             | 테스트 항목                                        | 파일                       |
| ---------------- | -------------------------------------------------- | -------------------------- |
| Smart Import     | 헤더 정규화, 값 파싱, Alias 매핑, Validation 3단계 | `test_import_service.py`   |
| Recipe Service   | 상태 전이(모든 허용/불허 조합), CRUD               | `test_recipe_service.py`   |
| Material Service | CRUD, 비활성화, Alias 관리                         | `test_material_service.py` |

### 7.2 통합 테스트

| 시나리오                                | 검증 항목             |
| --------------------------------------- | --------------------- |
| 엑셀 붙여넣기 → 등록 → Work 표시 → 완료 | 전체 Happy Path       |
| 미등록 원재료 Import                    | ERROR 반환, 등록 차단 |
| Completed 레시피 수정 시도              | 409 에러              |
| 동시 완료 처리                          | SSE 브로드캐스트 확인 |

### 7.3 실제 엑셀 데이터 테스트

- 현장 엑셀 파일 5~10건 수집
- 다양한 형식(빈 셀, 병합 셀, 특수문자)으로 파싱 테스트

---

## 8. 구현 가이드

### 8.1 구현 순서 (권장)

```
Phase 1: 기반
├── database.py (DDL, 초기 데이터)
├── config.py (설정)
├── models/ (Pydantic 모델 전체)
└── repositories/ (DB CRUD)

Phase 2: 핵심 서비스
├── auth_service.py (인증)
├── material_service.py (마스터)
├── import_service.py (Smart Import — 핵심)
├── recipe_service.py (상태 전이)
└── audit_service.py (로그)

Phase 3: API 라우터
├── auth.py, materials.py, recipes.py
├── stats.py, audit.py
└── health.py

Phase 4: 프론트엔드
├── base.html + login.html
├── work.html + work.js (Focus Mode)
├── management.html + smart-import.js
├── insight.html + insight.js
└── admin/ (materials, users)

Phase 5: 실시간 + 마무리
├── sse_service.py + sse-client.js
├── excel_export.py
├── Docker 설정
└── 테스트
```

### 8.2 의존성 (requirements.txt)

```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
jinja2>=3.1.0
python-multipart>=0.0.6
bcrypt>=4.1.0
openpyxl>=3.1.0
aiosqlite>=0.19.0
```

---

## Related Documents

- Plan: [irms.plan.md](irms.plan.md)
- Analysis: [irms.analysis.md](irms.analysis.md) _(완료)_
- Report: [irms.report.md](irms.report.md) _(완료)_

---

## Version History

| Version | Date       | Changes                                             | Author    |
| ------- | ---------- | --------------------------------------------------- | --------- |
| 1.0     | 2026-03-06 | 초안 작성 — 아키텍처, ERD, API, UI/UX, 보안, 테스트 | IRMS Team |
| 1.1     | 2026-03-06 | 상태 승인 및 연계 문서 링크 갱신                    | IRMS Team |
