# 운영 인프라 흐름 (IRMS/BRM)

> 대상: 운영 서버(`serve.py` 가 도는 PC)를 직접 관리하는 운영자. 개발 지식 없이도 읽히도록
> 썼다. 각 항목은 실제 소스 위치(`파일:함수` / `파일:줄`)를 근거로 단다.
> 함께 볼 문서: `docs/ops-backup-restore.md`(백업·복구 상세), `docs/ops-item-code-migration.md`(품목코드 이관).
>
> ⚠️ **운영 PC ≠ 개발 PC**: 이 문서를 읽는 개발 PC 에서 데이터 적재·서버 기동을 실행하지 말 것.
> 아래 명령·절차는 **운영 PC 에서만** 의미가 있다(자세히는 §8).

---

## 1. 운영 기동 체계 — 어떤 배치 파일을 언제 쓰나

운영 상시 가동의 정답은 **`run_auto.bat` → `serve.py`** 하나다. 나머지 배치는 목적이 다르다.

| 파일 | 용도 | 자동 업데이트 | 백업 | 포트 | 비고 |
|------|------|------|------|------|------|
| **`run_auto.bat`** | **운영 상시 가동(권장)** | O (주기 감시) | O (온라인+검증) | 9000 | 창 하나에 서버+감시. 부팅 자동시작은 `shell:startup` 에 바로가기 |
| `serve.py` | run_auto.bat 이 실제로 실행하는 파이썬 | O | O | `IRMS_PORT`(기본 9000) | 감시 루프 본체 |
| `update_and_run.bat` | **단발성** 업데이트+기동 | 1회만(감시 없음) | O (단순 복사·검증 없음) | 9000 고정 | serve.py 도입 전 방식. §1.3 주의 |
| `run_irms.bat` | 개발/수동 실행 | X | X | 8000 | `--reload` 켜짐 |
| `run_irms_intranet.bat` | 인트라넷 수동 실행 | X | X | 8000 | `--host 0.0.0.0`, reload 없음. 내부망 IP 자동 표시 |
| `run_tunnel.bat` | Cloudflare 터널(외부 접근) | X | X | — | 서버와 **별개**로 띄우는 터널. §6 |
| `setup_server.bat` | 최초 1회 설치 | — | — | — | Python/Git 확인 → clone → bootstrap |
| `setup_tunnel.bat` | 최초 1회 터널 설정 | — | — | — | cloudflared 설치·로그인·DNS. §6 |

### 1.1 `serve.py` 감시 루프가 하는 일 (`serve.py:main`)

1. 콘솔 제목 설정 → `_ensure_runtime_self_healing()` 으로 의존성 설치(§5).
2. `free_port()` — 이전에 비정상 종료로 포트를 물고 있는 서버가 있으면 정리(§1.4).
3. `start_server()` — uvicorn 을 자식 프로세스로 띄운다(로그가 같은 창에 출력).
4. `IRMS_AUTO_INTERVAL`(기본 600초=10분)마다 반복:
   - 자식 서버가 죽었으면 `free_port()` 후 재시작(`serve.py:main` 333~336행).
   - 날짜가 바뀌었으면 **일일 백업 1회**(`backup_db()`).
   - `IRMS_AUTO_UPDATE=1`(기본)이고 `has_update()` 가 True 면 `apply_update()`(§2·§1.2).

### 1.2 업데이트 반영 순서 (`serve.py:apply_update`)

`DB 백업 → (백업 게이트) → git pull origin main → pip install → (전부 성공 시) 서버 재시작`.
어느 한 단계라도 실패하면 **재시작을 건너뛴다**. 기존 서버는 메모리에 올라간 옛 코드로
계속 돌기 때문에 **무중단**이며, 다음 주기에 자동 재시도된다.

- **백업 게이트**: `backup_db()` 가 실패(`BACKUP_FAILED`)하거나 검증에 걸려 격리
  (`BACKUP_CORRUPT`)되면 pull 로 넘어가지 않고 **업데이트를 보류**한다("그날 신뢰할 백업
  없이 코드만 갱신"되는 위험 차단). DB 파일이 없을 때(`BACKUP_SKIPPED_MISSING`)는 경고만
  남기고 진행한다(신규 설치 등 정상 가능).
- **git pull 실패 복구**(`serve.py:_recover_and_retry_pull`): 실패가 **로컬 추적/미추적 파일
  변경**으로 인한 것이면 `git stash push --include-untracked -m serve-auto-<타임스탬프>` 로
  안전 보관 후 pull 을 1회 재시도한다. 성공하면 **stash 는 자동 삭제하지 않고**(운영자 데이터
  보존) 위치를 로그로 남긴다(`git stash list` / `git stash apply stash@{0}`). 로컬 변경이
  원인이 아니거나(네트워크·자격증명·충돌) stash 후에도 실패하면 **매 주기 `[CRITICAL] 자동
  업데이트가 멈춰 있습니다`** 경고를 내며 옛 버전으로 계속 서비스한다.
- **상태 파일**: 매 시도 결과를 `<IRMS_DATA_DIR>/update-status.json`(`{ok, last_error, at}`)에
  기록한다 — 콘솔 로그를 놓쳐도 자동 업데이트가 살아있는지 파일로 점검 가능.
- `serve.py` 자체가 갱신돼도 **실행 중에는 옛 serve.py 로 계속 감시**한다(무한 로딩 방지).
  서버 코드(`src/*`)는 매 재시작마다 반영된다(docstring `serve.py` 참조).

### 1.3 `update_and_run.bat` 을 쓸 때 주의

단발성 도구다. serve.py 와 백업 방식이 **다르다**:
- 백업이 PowerShell `Copy-Item`(오프라인 단순 복사, `update_and_run.bat` 26행)이라
  **WAL 체크포인트를 거치지 않는다** → 서버가 켜진 상태에서 실행하면 최근 트랜잭션이
  빠진 사본이 될 수 있고, serve.py 의 자동 검증(`.corrupt` 격리)도 없다.
- 포트가 **9000 하드코딩**(`update_and_run.bat` 66·79행) — `IRMS_PORT` 를 안 따른다.
- 결론: 상시 운영은 `run_auto.bat` 을 쓰고, `update_and_run.bat` 은 서버를 완전히 멈춘
  상태에서의 수동 1회 갱신용으로만 쓴다.

### 1.4 포트 충돌 처리 (`serve.py:free_port`)

`Get-NetTCPConnection` 으로 `IRMS_PORT` 를 리슨 중인 PID 를 찾아 `taskkill /F` 한다.
비정상 종료로 남은 옛 서버가 포트를 물어 재기동이 크래시 루프에 빠지는 것을 막는다.
`.bat` 배치는 정상 종료 처리와 무관하게, 부모/자기 자신(PID 0·me)은 제외한다.

---

## 2. 백업 — 생성·검증·보존·복구

### 2.1 생성 (`serve.py:backup_db`)

- **시점**: 매일 1회(감시 루프) + 업데이트 직전(`apply_update()` 안).
- **방식**: SQLite **온라인 백업 API**(`src.backup(dst)`) — 서버 가동 중에도 트랜잭션
  일관된 사본. 실패 시 `shutil.copy2` 단순 복사로 폴백.
- **대상 DB 경로**: `serve.py:_db_path()`(→ `_data_dir()`)가 `IRMS_DATA_DIR`(상대경로는 프로젝트
  루트 기준, 미설정 시 `data/`)에서 `irms.db` 를 찾는다. `serve.py` 는 기동 시 `.env` 를
  로드하므로(§4·`serve.py:load_env`) 이 경로가 서버 자식과 일치한다.
- **결과 코드**: `backup_db()` 는 `BACKUP_OK`/`BACKUP_CORRUPT`/`BACKUP_FAILED`/
  `BACKUP_SKIPPED_MISSING` 을 돌려주고, `apply_update` 의 백업 게이트(§1.2)가 이를 읽는다.
  **DB 파일이 없으면** 조용히 return 하지 않고 `[경고] 백업 대상 DB 가 없습니다 …
  IRMS_DATA_DIR 설정을 확인하세요` 를 남긴다(경로 오설정으로 백업이 무음 스킵되는 것 방지).

### 2.2 자동 검증·격리 (`serve.py:_verify_backup`)

생성 직후 읽기 전용(`mode=ro`)으로 열어 판정한다:
`PRAGMA integrity_check == 'ok'` **AND** 핵심 테이블 6종
(`recipes, recipe_items, blend_records, blend_details, workers, audit_logs`,
`serve.py:_VERIFY_TABLES`) 존재·COUNT 조회 가능.
- 통과 → `_mirror_backup()`(2차 사본) + `prune_backups()`(보존 규칙).
- **실패 → `irms_*.db.corrupt` 로 개명 격리**(미러 미전달). 원인 분석용으로 최근 2개 유지.
- 수동 검증: `python tools/verify_backup.py [backups\<파일>]`(serve.py 와 동일 로직).

### 2.3 보존 (`serve.py:prune_backups`)

- `IRMS_BACKUP_KEEP_DAYS`(기본 30일)를 넘긴 사본 삭제. 단 **최근 `BACKUP_KEEP_MIN`=5개는
  항상 보존**(`serve.py` 41행) — 서버가 오래 안 떠도 최근 5개는 남는다.
- `.corrupt` 격리본은 보존일수와 무관하게 최근 2개만.

### 2.4 미러 (`serve.py:_mirror_backup`)

`IRMS_BACKUP_MIRROR`(예: `D:\irms-backup`) 설정 시 검증 통과분만 2차 사본 복사.
**미설정 시 백업 폴더 디스크 1대에만 존재** → 그 디스크 고장 시 전량 손실.
외장/네트워크 폴더 지정을 권장한다.

### 2.5 복구 절차 (단계별)

> 라이브 DB 가 손상/삭제된 경우에만. 순서대로, 건너뛰지 말 것. 상세는
> `docs/ops-backup-restore.md` §2.

1. **서버 중지**: 운영 콘솔에서 `Ctrl+C`(서버·감시 루프 함께 정지).
2. **복구 후보 검증**: `python tools\verify_backup.py backups\<선택 파일>` → `PASS` 확인.
   `FAIL` 이면 다른 최근 사본으로 재시도.
3. **현행 DB 대피**: `data\irms.db`(있으면 `-shm`/`-wal` 도)를
   `data\irms.db.pre_restore_<yyyymmdd_HHMMSS>` 로 이름 변경(롤백용).
4. **백업 복사**: `copy backups\<선택 파일> data\irms.db`.
   **`-wal`/`-shm` 은 복사하지 않는다**(백업은 이미 정합 상태의 단일 파일).
5. **재기동**: `run_auto.bat`.
6. **정상 확인**: `curl http://127.0.0.1:<PORT>/health` → `{"status":"ok"}` +
   브라우저 `/status`·`/management`·`/viscosity` 육안 확인.
7. (선택) 대피본은 안정 확인 후(수일 뒤) 삭제.

---

## 3. 마이그레이션 체계

### 3.1 기동 시 적용 순서

`create_app()`(`src/main.py:29`) 맨 처음 `init_db()` 호출 →
`src/db/schema.py:init_db` 가:
1. `PRAGMA journal_mode = WAL` 설정.
2. `executescript(...)` 로 **기본 테이블/인덱스**를 `CREATE TABLE IF NOT EXISTS` 로 보장.
3. `apply_schema_migrations(connection)`(`src/db/migrations.py`) — 컬럼/테이블 증분 반영.
4. `IRMS_SEED_DEMO_DATA` 가 켜져 있으면 데모 시드(운영에선 금지, §4).

즉 **서버가 켜질 때마다 스키마가 스스로 최신화**된다. 운영자가 별도 마이그 명령을 돌릴
필요가 없다(품목코드 테이블 등도 자동 생성 — `ops-item-code-migration.md` §0 참조).

### 3.2 관례 (`src/db/migrations.py`)

- **`ensure_column(conn, table, col, def)`**: 이미 있으면 no-op, 없으면 `ALTER TABLE ADD
  COLUMN`. 테이블명은 화이트리스트(`_ALLOWED_TABLES`), 컬럼명은 정규식 검증 — 임의 SQL 차단.
  → 컬럼 추가는 **여러 번 실행해도 안전(멱등)**.
- **`has_migration` / `record_migration`**: `schema_migrations` 표에 이름표를 남겨 **1회성
  데이터 변환**(백필·정리·시드)이 두 번 돌지 않게 한다. 예: `blend_records_product_lot_unique`,
  `drop_orphan_chat_tables`, `seed_viscosity_products`.
- 새 테이블은 `CREATE TABLE IF NOT EXISTS`, 새 인덱스는 `CREATE INDEX IF NOT EXISTS` —
  이름표 없이도 멱등.
- 데이터 정리형 마이그(예: `dedup_product_lots`)는 실패 시 **init_db 트랜잭션 전체가
  롤백되고 서버 기동이 실패**한다 — 조용히 넘어가지 않는 fail-loud 설계
  (`src/db/migrations.py` 471~482행 주석).

---

## 4. 환경변수 전수표

`.env`(프로젝트 루트) + `src/config.py` 에서 읽는다(환경변수가 `.env` 보다 우선).
**`serve.py` 도 기동 시 같은 `.env` 를 로드**한다(`serve.py:load_env`, python-dotenv
`override=False` — 실제 환경변수 우선). 따라서 서버측·serve.py측 변수를 `.env` 한 곳에
써도 부모/자식이 같은 값을 본다. `.env.example` 를 복사해 `.env` 를 만든다.
값이 없을 때의 **폴백**과 **운영 필수** 여부:

| 변수 | 읽는 곳 | 기본값 | 운영 필수 | 설명 |
|------|---------|--------|:---:|------|
| `IRMS_ENV` | `config.py:21`, `serve.py:warn_if_not_production` | `development` | **필수** | `production` 이어야 보안 강화(HSTS·Secure 쿠키·strict). 누락 시 개발 모드로 떨어지며, serve.py 가 기동 시 눈에 띄는 경고 블록을 출력(동작 변경 없음, §7-5) |
| `IRMS_SESSION_SECRET` | `config.py:31` | 랜덤 생성 | **필수** | 세션 서명 키. 미설정 시 매 기동 랜덤 → 재시작마다 전 세션 무효화. 운영에선 미설정 시 기동 거부(아래) |
| `IRMS_REQUIRE_SESSION_SECRET` | `config.py:32` | 운영=true | 자동 | true 인데 시크릿 없으면 `RuntimeError` 로 기동 거부(안전장치) |
| `IRMS_DATA_DIR` | `config.py:23`, `serve.py:_data_dir` | `./data` | 선택 | DB·백업·`update-status.json` 기준 폴더. serve.py 도 `.env` 를 로드하므로 서버와 일치 |
| `IRMS_SESSION_MAX_AGE` | `config.py:26` | 28800(8h) | 선택 | 세션 쿠키 수명(초) |
| `IRMS_MANAGER_IDLE_TIMEOUT` | `config.py:30` | 900(15분) | 선택 | 책임자 세션 유휴 만료(공용 PC 방치 대비) |
| `IRMS_SEED_DEMO_DATA` | `config.py:33`, `schema.py:122` | 개발=true | **0 필수** | 데모 계정(알려진 비번) 시드. 운영에서 반드시 `0`/false. `production` 이면 켜져 있어도 예외로 차단 |
| `IRMS_TRAY_API_TOKEN` | `config.py:34` | 없음 | **필수** | 트레이·공개 API(`/public/*`) 토큰. 운영에서 미설정 시 기동 거부 |
| `IRMS_REQUIRE_TRAY_API_TOKEN` | `config.py:36` | 운영=true | 자동 | true 인데 토큰 없으면 `RuntimeError` |
| `IRMS_TRUSTED_ORIGINS` | `login_origin.py:57` | 없음 | 선택 | 리버스 프록시가 Host 를 바꿔 로그인이 막힐 때의 **탈출구**(쉼표 구분 호스트). 터널 정상 설정 시 불필요 |
| `IRMS_PORT` | `serve.py` | 9000 | 선택 | serve.py 서버 포트. serve.py 가 `.env` 를 로드하므로 `.env` 로만 지정해도 반영됨 |
| `IRMS_AUTO_INTERVAL` | `serve.py:38` | 600 | 선택 | 업데이트 확인 주기(초). 최소 30 |
| `IRMS_AUTO_UPDATE` | `serve.py:39` | 1(ON) | 선택 | `0` 이면 감시 없이 서버만 |
| `IRMS_BACKUP_KEEP_DAYS` | `serve.py:40` | 30 | 선택 | 백업 보존 일수(최소 1, 최근 5개는 항상 유지) |
| `IRMS_BACKUP_MIRROR` | `serve.py:42` | 없음 | 권장 | 백업 2차 사본 폴더(단일 디스크 손실 대비) |
| `IRMS_PUBLIC_HOST` | (문서/로그 전용) | — | 선택 | 런타임에서 읽지 않음(`.env.example` 37~39행 명시) |

> `run_auto.bat` 은 `IRMS_PORT=9000`·`IRMS_AUTO_INTERVAL=600` 을 배치에서 직접 `set` 하므로
> 이 둘은 `.env` 없이도 값이 잡힌다(`run_auto.bat` 23~24행). 배치 `set` 은 실제 환경변수라
> `.env` 보다 우선하므로, 이 둘을 `.env` 로 바꾸려면 `run_auto.bat` 의 `set` 을 지워야 한다.
> 나머지 서버·백업 변수는 `.env` 로 주면 serve.py 와 서버가 함께 읽는다.

---

## 5. 의존성 잠금 정책

- **운영 설치 우선순위**: `serve.py:_requirements_file` 이 `requirements-lock.txt`(고정 버전)이
  있으면 그것을, 없으면 `requirements.txt`(범위)를 쓴다. 무통제 업그레이드 방지.
- **자가 치유**(`serve.py:_ensure_runtime_self_healing`): 의존성 설치 실패 시 **원격 최신을
  한 번 당겨(pull) 재시도**한다. 잘못 핀된 lock(예: 이 파이썬에서 설치 불가한 버전)이
  커밋돼 서버가 아예 못 뜨는 상황에서, 원격 수정본으로 자동 복구할 여지를 준다
  (2026-07-14 numpy==2.5.0/Python 3.11 운영 중단 교훈, 주석 299~316행).
- **lock 갱신 절차**(개발 PC): `pip install -r requirements.txt` → 전체 테스트/smoke 통과 →
  `pip freeze > requirements-lock.txt` 커밋.
- ⚠️ **파이썬 3.11 dry-run 필수**: lock 갱신 전
  `pip install --dry-run --python-version 3.11 -r requirements.txt` 로 운영 파이썬(3.11)에서
  설치 가능한지 확인. numpy 등 바이너리 휠은 파이썬 마이너 버전에 민감하다.
- 현재 lock 요지: `numpy==2.4.6`(3.11 안전), `fastapi==0.136.3`, `uvicorn==0.48.0`,
  `pillow==12.2.0`, `PyMuPDF==1.27.2.3`, `pywin32==312`.

---

## 6. 터널 / 인트라넷 경로

### 6.1 인트라넷(내부망)
- `run_irms_intranet.bat` — `--host 0.0.0.0 --port 8000`, reload 없음. 내부망 IPv4(192.168./10.)
  자동 탐지해 접속 주소 표시. 같은 LAN 의 다른 PC 가 `http://<IP>:8000` 으로 접근.
- 상시 운영은 `run_auto.bat`(9000)이며, 인트라넷 배치는 임시/보조 실행용.

### 6.2 외부 접근 — Cloudflare Tunnel
- **최초 설정**: `setup_tunnel.bat` — cloudflared 설치(winget) → 로그인 → 터널 생성 →
  DNS 라우팅. 이후 `cloudflared\config.example.yml` 을 `config.yml` 로 복사·치환.
- **상시 실행**: 서버(`run_auto.bat`, 9000)와 **별개**로 터널을 띄운다.
  - 임시: `run_tunnel.bat`(`config.yml`·cloudflared PATH 확인 후 `cloudflared tunnel run`).
  - 영구: `cloudflared service install`(Windows 서비스), 확인 `sc query cloudflared`.
- **config 요지**(`config.example.yml`): 외부 HTTPS → `http://127.0.0.1:9000`(loopback,
  `noTLSVerify: true`), 콜드스타트 대비 `connectTimeout: 30s`, `httpHostHeader` 로 공개 호스트명 전달.
- **주의**: 외부 노출 전 `IRMS_ENV=production` 필수(HSTS·Secure 쿠키·strict). 실제 `config.yml`
  은 저장소에 없다(gitignore) — 운영 PC 에만 존재.

---

## 7. 스모크 · 부트스트랩

- **부트스트랩**(`tools/bootstrap_irms.py`): venv 생성 → `requirements.txt` 설치 →
  (`--run-smoke` 시) 스모크. `setup_server.bat` 이 최초 설치에서 호출.
  `python tools/bootstrap_irms.py --run-smoke`.
- **스모크**(`tools/smoke_irms.py`): `src/**/*.py` 전부 `py_compile` + `create_app()` 임포트 +
  `/health` 라우트·페이로드 확인. 운영 DB 를 건드리지 않도록 `IRMS_DATA_DIR` 을
  `.tmp-tests/smoke_runtime` 로 격리(`smoke_irms.py:configure_env`).
  - 개발: `python tools/smoke_irms.py --mode development --seed-demo-data`
  - 운영: `python tools/smoke_irms.py --mode production --session-secret '<값>'`
    (운영 모드는 `--session-secret` 미지정 시 즉시 실패).
- **위생 검사**(`tools/check_repo_hygiene.py`): 루트 1단계에 임시물(`tmp*`, `__pycache__`,
  `*.png/*.jpg/*.db`, 미허용 폴더)이 있으면 exit 1. 산출물은 `.tmp-tests/` 하위로.
  CI(`.github/workflows/test.yml`)에서도 실행.

---

## 8. 운영 PC ≠ 개발 PC 원칙 · 배포 체크리스트

- **데이터 적재·서버 기동은 운영 PC 에서만**. 개발 PC 에서 돌리면 반영되지 않는다(별도 DB).
- **자동 배포 흐름**: 개발 PC 에서 커밋·push origin/main → 운영 PC 의 `serve.py` 가
  다음 감시 주기(≤10분)에 감지 → 자동 백업 → pull → pip install → 재시작.

### 배포 체크리스트
1. [ ] 개발 PC 에서 전체 테스트/smoke 통과.
2. [ ] 의존성 변경 시 §5 절차(3.11 dry-run → lock 갱신) 수행.
3. [ ] 커밋 후 **origin/main 에 실제로 올라갔는지** 스팟체크(`git status`, `git log origin/main`).
       "커밋했다"와 "반영됐다"는 다르다.
4. [ ] 운영 PC 콘솔에서 다음 주기에 `업데이트 반영 완료` 로그 확인, 또는 즉시 원하면
       `Ctrl+C` 후 `run_auto.bat` 재실행(기동 시 pull).
5. [ ] `/health` 200 + 주요 화면(`/status`, `/management`, `/viscosity`) 육안 확인.
6. [ ] 스키마 변경이 있었으면 서버 기동 로그에 마이그레이션 오류가 없는지 확인(§3.2 fail-loud).

---

## 9. 갭·주의 사항 (GAP/BUG/POLISH 후보)

> 아래는 현재 코드 동작에서 확인된 위험 지점이다. 즉시 수정 지시가 아니라 운영자가
> 인지·모니터링할 항목 + 개발 시 검토 후보다.

1. **[해결됨] `serve.py` 가 `.env` 를 읽지 않던 문제.**
   ~~`serve.py` 는 `os.environ` 만 봤다~~ → **`serve.py:load_env` 가 기동 시 프로젝트 `.env`
   를 로드**한다(`config.py` 와 동일: python-dotenv `override=False` 라 실제 환경변수 우선).
   이제 `IRMS_DATA_DIR`·`IRMS_PORT`·`IRMS_BACKUP_*` 를 `.env` 로만 지정해도 serve.py(부모)와
   서버(자식)가 같은 값을 봐, "백업 대상 DB 경로·포트가 실제 서버와 어긋나는" 정합성 사고가
   제거됐다. 단 `run_auto.bat` 이 `set` 하는 `IRMS_PORT`/`IRMS_AUTO_INTERVAL` 은 실제
   환경변수라 여전히 `.env` 보다 우선한다(§4 각주).

2. **[해결됨] git pull 충돌 시 자동 업데이트 무음 정체.**
   `serve.py:apply_update`→`_recover_and_retry_pull` 로 보강. `git pull` 실패 시 (a) git 출력을
   포함한 **여러 줄 경고 블록**을 남기고, (b) 실패가 **로컬(추적/미추적) 파일 변경** 때문이면
   `git stash push --include-untracked -m serve-auto-<타임스탬프>` 로 안전 보관 후 pull 1회
   재시도 — 성공하면 stash 를 **drop 하지 않고**(운영자 데이터 보존) 위치를 로그로 남긴다.
   로컬 변경이 원인이 아니거나 stash 후에도 실패하면 **매 주기 `[CRITICAL] 자동 업데이트가
   멈춰 있습니다`** 경고를 반복하며 옛 버전으로 계속 서비스한다. (c) 상태는
   `<IRMS_DATA_DIR>/update-status.json` 에 기록.

3. **[해결됨] 백업 실패해도 업데이트가 진행되던 문제.**
   `apply_update` 에 **백업 게이트** 추가. `backup_db()` 가 `BACKUP_FAILED`/`BACKUP_CORRUPT`
   를 돌려주면 pull 로 넘어가지 않고 업데이트를 보류(경고 블록 + `update-status.json` 기록,
   옛 서버 유지). DB 미존재(`BACKUP_SKIPPED_MISSING`)는 경고만 하고 진행(신규 설치 등).
   → "그날 신뢰할 백업 없이 코드만 갱신"되는 위험 차단.

4. **[해결됨] `IRMS_DATA_DIR` 오설정/부재 시 백업 무음 스킵.**
   `serve.py:backup_db` 는 대상 DB 가 없으면 **`[경고] 백업 대상 DB 가 없습니다 …
   IRMS_DATA_DIR 설정을 확인하세요`** 를 남기고 `BACKUP_SKIPPED_MISSING` 을 돌려준다(조용한
   return 제거). 여전히 정기적으로 `dir backups\irms_*.db /O-D` 로 최신 백업 날짜 육안 확인 권장.

5. **[해결됨/부분] `IRMS_ENV` 누락 = 조용한 개발 모드.**
   `serve.py:warn_if_not_production` 이 기동 시 `IRMS_ENV != production` 이면 **눈에 띄는 경고
   블록**(개발 모드로 기동 중 — HSTS·Secure 쿠키·strict 꺼짐)을 출력한다(경고만, **동작 변경
   없음**). `config.py` 폴백 자체는 그대로이므로 운영 PC `.env` 에 `IRMS_ENV=production` 이
   실제로 들어갔는지는 여전히 배포 체크리스트로 확인.

6. **[GAP] 마이그레이션 실패 시 크래시 루프.**
   init_db 예외 → `create_app()` 실패 → uvicorn 부팅 실패 → serve.py 루프가 "서버가
   종료되어 다시 시작"(333~336행)을 `INTERVAL`(기본 600초)마다 반복. fail-loud 는 맞지만
   원인이 제거되기 전까지 자동 복구되지 않는다. 단, §5 자가치유가 원격 수정본을 당겨 푸는
   경로는 **의존성 설치 실패**에만 작동하고 **마이그 실패**엔 해당 없음.
   참고: `schema.py:init_db` 의 `executescript`(CREATE TABLE IF NOT EXISTS)는 즉시 커밋되므로,
   그 뒤 `apply_schema_migrations` 가 실패해도 기본 테이블은 이미 생성돼 있을 수 있다.

7. **[GAP] `free_port` 가 9000 을 쓰는 무관한 프로세스도 강제 종료.**
   `serve.py:free_port`(251행)는 자기/PID 0 을 제외한 **모든** 리슨 프로세스를 `taskkill /F`.
   9000 을 정당하게 쓰는 다른 앱이 있으면 함께 죽는다. 운영 PC 를 IRMS 전용으로 유지 권장.

8. **[해결됨] 설치 안내 파이썬 버전 불일치.**
   `setup_server.bat`(15행)의 설치 안내 문구를 **Python 3.11** 로 맞췄다 — lock 검증·운영 기준
   (§5, CLAUDE.md 교훈)과 일치. 바이너리 휠(numpy 등)은 파이썬 마이너 버전에 민감하므로 운영
   기준 버전으로 통일한다.

9. **[POLISH] lock 과 requirements 범위는 호환하나 마커 차이 존재.**
   `requirements.txt` 는 `pywin32; platform_system=='Windows'` 마커가 있으나
   `requirements-lock.txt` 는 `pywin32==312`(무조건). 운영은 Windows 라 문제없지만, lock 을
   비Windows 에서 설치하면 실패한다(운영 경로 밖이라 실무 영향 없음).

---

### 검증 불가/미확인 항목

- **운영 `.env` 실제 값**: 저장소에 커밋되지 않으므로(gitignore) 운영 PC 의 실제
  `IRMS_ENV`·시크릿·토큰·`IRMS_BACKUP_MIRROR` 설정 여부는 이 문서로 확인 불가. §4·§8 로 점검.
- **운영 `cloudflared\config.yml`**: 저장소에 없음(예시만 존재). 실제 호스트·토큰·서비스
  설치 여부는 운영 PC 에서 확인해야 한다.
- **개발 PC 의 `backups/` 최신 사본이 오래됨**(현재 최신 `irms_20260527_*.db`): 이는 이 PC 가
  개발 PC 라 serve.py 상시 가동을 안 하기 때문으로 보이며, 운영 PC 의 백업 상태와는 무관.
  운영 PC 에서 별도 확인 필요.
