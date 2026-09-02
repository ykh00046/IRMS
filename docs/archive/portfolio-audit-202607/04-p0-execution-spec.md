# IRMS(BRM) P0 실행 지시서 — 백업 무결성 자동 검증 + 레포 대청소

> 작성일: 2026-07-12 · 대상: `C:\X\IRMS` · 선행 문서: `01-improvement-plan.md`(P0-1, P0-2), `02-design.md`(§3.1, §3.2)
> 본 지시서의 경로·함수명·라인 번호는 2026-07-12 시점 실제 코드·디스크 상태에서 확인한 것이다.
> ⚠ 이 저장소는 `serve.py`가 origin/main을 주기 감시해 **커밋이 곧 운영 반영**된다(자동 git pull). 커밋 전 게이트 통과가 절대 조건이다.

---

## 1. 목표와 배경

`serve.py`는 매일 1회 + 업데이트 직전에 SQLite 온라인 백업(`backups/irms_*.db`)을 만들지만 **만든 사본을 한 번도 검증하지 않는다** — integrity 확인 없이 미러링·보존까지 진행되며, 온라인 백업 실패 시 `shutil.copy2` 폴백(가동 중 복사라 비일관 사본 위험이 가장 큰 경로)조차 무검증이다. DHR(품질 증빙) 데이터 특성상 "열리지 않는 백업"은 비가역 손실로 직결된다. 또한 저장소 루트(= 운영 서버의 git 작업 디렉터리)에 `tmp_*` 런타임 디렉터리 20여 개, 스크린샷 PNG 약 40개, 119MB `.venv-wsl-backup-*`, 한글 엑셀 4개가 방치되어 탐색·배포·백업 신뢰성을 해친다. 이 지시서는 (a) `serve.py` 백업 직후 사본에 `PRAGMA integrity_check` + 핵심 테이블 확인을 자동 수행하고 실패 사본을 격리하는 검증 로직, (b) 삭제 패턴 목록 + 삭제 전 보존 확인 절차 + `.gitignore`/CI 재발 차단을 갖춘 레포 대청소를 실행 가능한 수준으로 지시한다. **`data\`와 `backups\`는 청소 대상에서 명시적으로 제외한다.**

## 2. 사전 조건

- 저장소: `C:\X\IRMS` (git, origin/main 자동 배포 경로). 작업 브랜치 생성 (예: `p0-backup-verify-hygiene`).
- 게이트 실행 가능 확인: `python -m pytest tests -q` (313개 수집), `node --test tests/js/*.test.js`, `python tools/smoke_irms.py --mode production --session-secret dummy-secret-0000`.
- **대규모 변경 중 자동 배포 차단이 필요하면** 운영 PC에서 `IRMS_AUTO_UPDATE=0`으로 감시를 끄고 수동 반영한다(02-design §4.2). 이번 변경은 serve.py·tools·문서 위주라 위험도는 낮지만, 커밋을 기능 단위로 분리한다: ① 백업 검증 커밋 → ② 위생 스크립트/CI 커밋 → ③ 문서 커밋. 청소 자체는 커밋이 아니라 **로컬 디스크 작업**이다(대상이 전부 untracked — git ls-files로 확인 완료).
- 청소 실행 직전, 수동 백업 1회: 서버 가동 여부와 무관하게 `backups/`의 최신 `irms_*.db` 존재 확인(필요 시 serve.py 가동 중이면 다음 일일 백업 대기 또는 `python -c "import serve; serve.backup_db()"` — 반드시 저장소 루트에서, 라이브 서버가 다른 PC라면 그 PC에서).
- MEMORY 주의사항 준수: 대시보드는 라이브 `schedules.json`/실데이터 — 변경성 기능을 라이브에서 테스트하지 않는다.

## 3. 확정된 현재 구조 (변경 근거)

### 3.1 백업 로직 (`serve.py` 실코드)

- `_db_path()`(101행): `IRMS_DATA_DIR` 환경변수 기준(기본 `ROOT/data`) → `data/irms.db`.
- `backup_db()`(108행): `ROOT/backups` 하드코딩 → `irms_%Y%m%d_%H%M%S.db` 생성. `sqlite3` 온라인 백업 API, 실패 시 `shutil.copy2` 폴백. 성공 후 `_mirror_backup(dest)`(IRMS_BACKUP_MIRROR 2차 사본) → `prune_backups()`(보존 `IRMS_BACKUP_KEEP_DAYS`=30일, 최근 `BACKUP_KEEP_MIN`=5개 항상 유지). **검증 단계 없음.**
- 호출 시점: `main()` 루프에서 일 1회 + `apply_update()`(git pull 직전).
- `serve.py`는 `if __name__ == "__main__"` 가드가 있어 **테스트에서 import 가능**하고, 루트 `conftest.py`가 PROJECT_ROOT를 `sys.path`에 넣는다.
- 핵심 테이블(존재 확인 완료): `schema.py` — `users`, `materials`, `material_aliases`, `recipes`, `recipe_items`, `schema_migrations`, `audit_logs`, `attendance_users` / `migrations.py` — `recipe_steps`, `viscosity_products`, `viscosity_readings`, `blend_records`, `blend_details`, `workers`.

### 3.2 루트 오염 실태 (실측)

- `tmp_*` 디렉터리 (현재 24개 + `tmpivkqp8nt/` + `__pycache__/`): `tmp_chat_test_a913ba40`, `tmp_chat_test_c123bfac`, `tmp_design`, `tmp_e2e_artifacts`, `tmp_e2e_runtime`, `tmp_preview_runtime`, `tmp_rec2`, `tmp_review_runtime`(파생 6종), `tmp_smoke_runtime`, `tmp_test_runtime`, `tmp_ui`, `tmp_ui16`, `tmp_ui18`~`tmp_ui21`, `tmp_ui_runtime`, `tmp_ui_runtime_blend_session`, `tmp_ui_runtime_stock_removal`. 샘플 확인 결과 내용물은 테스트 런타임 DB(`irms.db`/`-shm`/`-wal`) 또는 E2E 스크린샷 PNG — 전부 재생성물 성격.
- 루트 이미지 약 40개: `irms-management-*.png`, `tmp_ui_runtime_*.png`, `management-*.png`, `status-delete-*.png`, `viscosity-records-*.png`, `blend-session-badge-*.png`, `recipe-delete-detail.png`, `1779428060.png`, `2.jpg` 등.
- `.venv-wsl-backup-20260527-210916/` (119MB) — WSL venv 백업, `tools/bootstrap_irms.py`로 재현 가능.
- 루트 한글 엑셀 4개: `레시피.xlsx`, `배합기록.xlsx`, `합성 점도.xlsx`, `합성일지.xlsx` (레거시 수기 원본 — 재생성 불가 자료이므로 삭제가 아닌 이동 대상).
- **git 추적 여부 확인 완료**: 위 전부 untracked (`.gitignore`의 `tmp_*/`, `*.png`, `/*.xlsx`, `.venv-wsl-backup-*/` 등이 이미 커버). 추적 이미지는 `src/resources/signature/**`(서명 자산, gitignore 예외 처리됨)와 `excel/*.xlsx`, `src/resources/dhr_template.xlsx`뿐 — 이들은 보존 대상.
- 오염 재생산 지점: `tools/smoke_irms.py:69` — `--data-dir` 기본값이 `ROOT / "tmp_smoke_runtime"`(루트 직생성). `README.md:88`도 같은 경로를 예시. 나머지 `tmp_ui*` 등은 과거 에이전트/E2E 세션이 `IRMS_DATA_DIR`을 루트 상대경로로 지정한 잔재. pytest는 이미 `conftest.py`가 `.tmp-tests/pytest-data`로 강제하고 있어 안전.
- `data/` 내부에도 `__tmp_status_*` 3개, `audit_ui_test_db/`가 있으나 **본 지시서 지시에 따라 data\ 전체를 청소 대상에서 제외**한다(§7).

## 4. 변경 목록

### 4.1 수정: `serve.py` — 백업 무결성 자동 검증

**(1) 백업 디렉터리 상수 추출** (테스트 격리용 최소 리팩터): `backup_db()`와 `prune_backups()`의 `ROOT / "backups"`를 모듈 상수 `BACKUPS_DIR = ROOT / "backups"`로 추출하고 두 함수가 이를 참조하게 한다. 테스트가 `monkeypatch.setattr(serve, "BACKUPS_DIR", tmp_path)`로 실제 `backups/`를 오염시키지 않고 검증할 수 있게 하기 위함이다.

**(2) 검증 함수 신설** (`_mirror_backup` 위쪽에 배치):

```python
# 검증 대상 핵심 테이블 — 하나라도 없으면 불완전 사본으로 판정 (전부 schema/migrations 확인 완료)
_VERIFY_TABLES = ("recipes", "recipe_items", "blend_records", "blend_details", "workers", "audit_logs")


def _verify_backup(dest: Path) -> bool:
    """백업 사본 무결성 검증 — 읽기 전용(mode=ro)으로 열어 원본·사본 모두 무수정.

    판정: PRAGMA integrity_check == 'ok' AND 핵심 테이블 전부 존재·COUNT 조회 가능.
    실패 사본은 호출부(backup_db)가 .corrupt 로 격리한다.
    """
    try:
        conn = sqlite3.connect(f"file:{dest.as_posix()}?mode=ro", uri=True)
        try:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                return False
            counts = {}
            for table in _VERIFY_TABLES:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if row is None:
                    log(f"백업 검증: 핵심 테이블 누락 — {table}")
                    return False
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            log(f"백업 검증 OK: {dest.name} (blend_records={counts['blend_records']}, recipes={counts['recipes']})")
            return True
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        log(f"백업 검증 중 오류: {exc}")
        return False
```

주의: `_VERIFY_TABLES`는 f-string으로 SQL에 들어가지만 하드코딩 튜플 상수만 허용(외부 입력 없음). `mode=ro` URI로 열어 검증이 백업 파일에 락/저널을 만들지 않게 한다(02-design §3.2의 리스크 완화 그대로).

**(3) `backup_db()` 흐름 변경** — 기존 "생성 → 미러 → prune"을 "생성 → **검증** → (통과 시) 미러 → prune / (실패 시) 격리"로:

```python
    # ... 기존 생성 로직(온라인 백업 → 실패 시 copy2 폴백) 그대로 ...
    if _verify_backup(dest):
        _mirror_backup(dest)
    else:
        corrupt = dest.with_name(dest.name + ".corrupt")
        try:
            dest.rename(corrupt)
        except Exception as exc:  # noqa: BLE001
            log(f"손상 백업 격리 실패({dest.name}): {exc}")
        log(f"[경고] 백업 검증 실패 — 오늘 백업을 신뢰하지 마세요: {corrupt.name}")
    prune_backups()
```

- 검증 실패본은 `irms_*.db.corrupt`로 개명 → `prune_backups()`의 `glob("irms_*.db")`에 걸리지 않아 원인 분석용으로 남는다. `prune_backups()` 끝에 `.corrupt` 별도 보존 규칙 추가: `sorted(BACKUPS_DIR.glob("irms_*.db.corrupt"), key=mtime, reverse=True)[2:]` 삭제(최근 2개만 유지).
- 미러(`_mirror_backup`)는 **검증 통과본만** 받는다 — 손상 사본이 2차 저장소까지 퍼지는 것을 차단.
- copy2 폴백 경로도 동일하게 검증을 거친다(폴백 사본이 가장 위험하므로 검증 가치가 가장 큼).

### 4.2 신설: `tools/verify_backup.py` — 수동/리허설용 CLI

책임: 임의 백업 파일(또는 최신)을 serve.py와 **동일한 로직**으로 검증. 분기 리허설과 장애 시 복구 후보 선별에 사용.

```python
"""verify_backup — backups/ 사본 무결성 수동 검증 (읽기 전용).

사용 (저장소 루트에서):
    python tools/verify_backup.py                 # backups/ 최신 irms_*.db 검증
    python tools/verify_backup.py backups\\irms_20260712_120000.db
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import serve  # noqa: E402  (serve.py 의 _verify_backup / BACKUPS_DIR 재사용 — 로직 이원화 금지)


def main() -> int:
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        candidates = sorted(serve.BACKUPS_DIR.glob("irms_*.db"), key=lambda p: p.stat().st_mtime)
        target = candidates[-1] if candidates else None
    if target is None or not target.exists():
        print("검증할 백업 파일이 없습니다.")
        return 1
    ok = serve._verify_backup(target)
    print(f"{'PASS' if ok else 'FAIL'}: {target}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

주의: `import serve`는 서버를 기동하지 않는다(`__main__` 가드 확인 완료). 단, serve.py 모듈 상단의 환경변수 파싱이 실행되므로 부작용 없는 상수 계산뿐임을 유지할 것(현행 그대로면 문제 없음).

### 4.3 신설: `docs/ops-backup-restore.md` — 복구 절차 + 분기 리허설

내용 골자 (serve.py docstring 15~17행의 기존 복구 안내를 승격·확장):

1. **복구 절차**: 서버 중지(콘솔 Ctrl+C) → `python tools/verify_backup.py backups\<선택 파일>`로 PASS 확인 → `data\irms.db`(및 `-shm`/`-wal`)를 `data\irms.db.pre_restore_<ts>`로 대피 → 선택 백업을 `data\irms.db`로 복사 → `run_auto.bat` 재기동 → `/health` 200 + 배합 기록 화면(`/status`) 최신 데이터 육안 확인.
2. **분기 리허설 체크리스트** (분기 1회, 라이브 서버 무접촉): 최신 백업을 `.tmp-tests\restore-rehearsal\`에 복사 → `verify_backup.py`로 PASS → `set IRMS_DATA_DIR=.tmp-tests\restore-rehearsal` + 복사본을 `irms.db`로 개명 후 `uvicorn src.main:app --port 9100` 기동 → `/health` 및 로그인·기록 조회 확인 → 서버 종료·디렉터리 삭제 → 수행 결과를 이 문서 하단 이력 표에 1줄 기록.
3. `IRMS_BACKUP_MIRROR` 미설정 시 단일 디스크 리스크 경고 1줄.
4. `CLAUDE.md`의 "DB 백업" 절에 "백업은 생성 직후 자동 검증되며 실패본은 `.corrupt`로 격리됨, 상세는 `docs/ops-backup-restore.md`" 1줄 동기화.

### 4.4 레포 대청소 — 삭제 패턴 · 보존 확인 절차 · 실행 순서

**모든 청소는 저장소 루트 1단계에 한정**한다. 하위 디렉터리 내부는 건드리지 않는다.

**(1) 삭제 대상 패턴 (루트 1단계, 전부 untracked 확인 완료)**

| 패턴 | 실측 | 근거 |
|---|---|---|
| `tmp_*/` 디렉터리 전부 | 24개 | 테스트/E2E 런타임 DB·스크린샷 — 재생성물 |
| `tmpivkqp8nt/` | 1개 | tempfile 잔재 |
| `__pycache__/` | 1개 | Python 캐시 |
| `.venv-wsl-backup-*/` | 119MB 1개 | `tools/bootstrap_irms.py`로 venv 재현 가능 |
| 루트 `*.png`, `*.jpg` | 약 40개 | UI 검수 스크린샷 — 보존 가치 있는 것만 `docs/assets/screenshots/`로 이동(선별 기준: docs 문서에서 참조 중인 파일명 grep — 참조 0건이면 전량 삭제 가능) |
| `.pytest_cache/`, `.ruff_cache/` | 각 1개 | 도구 캐시 (선택 — 지워도 다음 실행 시 재생성) |

**(2) 이동 대상 (삭제 금지)**

| 대상 | 조치 |
|---|---|
| `레시피.xlsx`, `배합기록.xlsx`, `합성 점도.xlsx`, `합성일지.xlsx` | `excel\legacy\`로 이동. 수기 시절 원본 자료로 **재생성 불가** — 삭제 아님. `.gitignore`에 `excel/legacy/` 1줄 추가(사내 실데이터이므로 커밋 방지 — 루트 이탈로 기존 `/*.xlsx` 규칙이 더 이상 커버하지 않기 때문) |

**(3) 삭제 전 보존 확인 절차 (순서대로, 건너뛰기 금지)**

1. **추적 파일 교차 확인**: `git ls-files | findstr /I "tmp_ .png .jpg xlsx venv"` 결과에 삭제 대상이 없음을 확인한다(2026-07-12 확인 시 추적물은 `excel/*.xlsx`, `src/resources/dhr_template.xlsx`, `src/resources/signature/*.png`뿐 — 이들은 대상 아님).
2. **tmp_* 재생성물 샘플 확인**: 각 `tmp_*` 디렉터리에 대해 `Get-ChildItem <dir> -Recurse | Select-Object FullName, Length` 실행. 판정 기준 — 내용이 (a) `irms.db`/`-shm`/`-wal` 조합(테스트 런타임 DB, 수백 KB 규모)이거나 (b) `*.png` 스크린샷이면 재생성물로 확정. **판정이 애매한 파일(엑셀·문서·수 MB 이상의 DB 등)이 나오면 그 디렉터리는 삭제하지 말고 4단계 격리로 보낸다.**
3. **최신 백업 존재 확인**: `backups\irms_*.db` 최신 파일의 날짜가 오늘 또는 어제인지 확인(§2 사전 조건).
4. **격리 후 삭제(권장 경로)**: 즉시 삭제 대신 `.tmp-tests\quarantine-<yyyymmdd>\`로 **이동** → 2주 운영 관찰(서버·테스트·현장 이상 없음) → 폴더째 삭제. 단 `.venv-wsl-backup-*`(119MB)와 `__pycache__`, 도구 캐시는 재현 경로가 명백하므로 즉시 삭제 허용.
5. 운영 PC에도 동일 잔재가 있을 수 있다 — 이 절차 문서를 근거로 배포 후 운영 PC에서 1회 동일 청소를 수행한다(코드 배포와 무관한 수동 작업).

### 4.5 재발 차단

**(1) 산출물 경로 통일**

- `tools/smoke_irms.py:69`: `--data-dir` 기본값을 `str(ROOT / "tmp_smoke_runtime")` → `str(ROOT / ".tmp-tests" / "smoke_runtime")`으로 변경.
- `README.md:88`의 `--data-dir ./tmp_smoke_runtime` 예시를 `./.tmp-tests/smoke_runtime`으로 갱신.
- `CLAUDE.md`의 "런타임 산출물" 절에 규칙 명문화: "테스트·E2E·에이전트 산출물(임시 `IRMS_DATA_DIR` 포함)은 반드시 `.tmp-tests/` 하위에 만든다. 루트에 `tmp_*`를 새로 만들지 않는다."
- pytest는 이미 `conftest.py`가 `.tmp-tests/pytest-data`를 강제 — 변경 불필요(확인 완료).

**(2) 신설: `tools/check_repo_hygiene.py` — 위생 검사 (로컬 + CI)**

```python
"""check_repo_hygiene — 저장소 루트 1단계 위생 검사. 위반 시 exit 1.

로컬: python tools/check_repo_hygiene.py   (청소 후 상시 실행 가능)
CI  : .github/workflows/test.yml 스텝 — 커밋된 오염물 유입 차단.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ALLOWED_DIRS = {
    ".antigravitycli", ".bkit", ".claude", ".codegraph", ".git", ".github",
    ".gstack", ".playwright-mcp", ".pytest_cache", ".ruff_cache", ".tmp-tests",
    ".venv", "artifacts", "backups", "cloudflared", "data", "docs", "excel",
    "scale_agent", "scripts", "src", "static", "templates", "tests", "tools",
    "tray_client",
}
BANNED_FILE_GLOBS = ("*.png", "*.jpg", "*.jpeg", "*.db")  # 루트 1단계 한정


def check(root: Path) -> list[str]:
    violations: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir():
            if entry.name.startswith("tmp"):
                violations.append(f"root tmp dir: {entry.name}/")
            elif entry.name == "__pycache__":
                violations.append("root __pycache__/")
            elif entry.name.startswith(".venv-"):
                violations.append(f"stale venv backup: {entry.name}/")
            elif entry.name not in ALLOWED_DIRS:
                violations.append(f"unexpected root dir: {entry.name}/")
        else:
            for pattern in BANNED_FILE_GLOBS:
                if entry.match(pattern):
                    violations.append(f"root artifact file: {entry.name}")
                    break
    return violations


def main() -> int:
    violations = check(ROOT)
    for v in violations:
        print(f"HYGIENE FAIL: {v}")
    if violations:
        print(f"\n{len(violations)}건 — 산출물은 .tmp-tests/ 하위로, 자료는 docs/assets 또는 excel/legacy 로.")
        return 1
    print("repo hygiene OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

구현 재량: 메시지 문구·allowlist 항목명은 실행 시점 실태에 맞춰 조정 가능(단 allowlist에 `tmp*` 계열을 추가해 통과시키는 식의 완화 금지). `check(root)` 함수 분리 형태는 테스트를 위해 유지.

**(3) CI 편입**: `.github/workflows/test.yml`의 `Run pytest` 스텝 **앞**에 추가 —

```yaml
      - name: Repo hygiene check
        run: python tools/check_repo_hygiene.py
```

CI는 커밋된 오염물 유입을 차단하고(체크아웃 트리는 추적 파일만 포함), 로컬 재발은 (1)의 경로 통일 + 같은 스크립트의 수동 실행으로 막는다 — 이 역할 구분을 스크립트 docstring에 명시했다.

**(4) `.gitignore` 갱신**: `excel/legacy/` 1줄 추가. 그 외 규칙은 이미 충분함을 확인했으므로 **변경하지 않는다**(중복 항목 정리 등 "하는 김에" 수정 금지 — diff 최소화).

## 5. 테스트 계획

### 5.1 신규: `tests/test_backup_verify.py`

`import serve` 후 직접 호출(루트 conftest가 sys.path 처리, `__main__` 가드로 부작용 없음 확인). `BACKUPS_DIR`는 monkeypatch.

| 케이스 | 준비 | 기대 |
|---|---|---|
| 정상 사본 PASS | `tmp_path`에 `_VERIFY_TABLES` 6개 테이블 CREATE + 소량 INSERT 한 SQLite 파일 | `_verify_backup()` → `True` |
| **손상 파일 FAIL** | 유효 DB 생성 후 파일 앞부분 수백 바이트를 임의 바이트로 덮어쓰기 | `False` (예외 삼킴·로그 후) |
| **비 SQLite 파일 FAIL** | 텍스트 바이트를 `.db`로 저장 | `False` |
| **핵심 테이블 누락 FAIL** | `blend_records` 없이 나머지만 CREATE | `False` |
| 검증의 무수정 보장 | PASS 케이스 실행 전후 파일 sha256 동일, `-wal`/`-shm` 미생성 | 읽기 전용 확인 |
| **backup_db 격리 흐름** | `monkeypatch.setattr(serve, "BACKUPS_DIR", tmp_path)` + `_db_path` monkeypatch(유효 원본) + `_verify_backup`을 False 반환으로 monkeypatch | `irms_*.db.corrupt` 생성, `_mirror_backup` 미호출(호출 기록 스파이), 원본 `irms_*.db` 부재 |
| backup_db 정상 흐름 | 위와 동일하되 검증 실제 수행 | `irms_*.db` 생성 + 검증 통과 로그, `.corrupt` 없음 |
| `.corrupt` 보존 규칙 | `.corrupt` 3개 생성 후 `prune_backups()` | 최근 2개만 잔존 |
| CLI exit code | `subprocess`로 `tools/verify_backup.py <정상>/<손상>` 실행 | 0 / 1 |

### 5.2 신규: `tests/test_repo_hygiene.py`

`from tools.check_repo_hygiene import check` (또는 importlib 로드) 후 `tmp_path` fixture로:

- 허용 구조만 있는 루트 → `check()` 빈 리스트.
- `tmp_ui99/` 디렉터리 존재 → 위반 1건.
- 루트 `shot.png` / `stray.db` → 위반.
- `.venv-wsl-backup-x/` → 위반.
- `.tmp-tests/` 내부의 png는 위반 아님(루트 1단계 한정 확인).

### 5.3 기존 게이트 (전부 green 필수, 커밋마다)

```
python -m pytest tests -q                                   # 313개 + 신규
node --test tests/js/*.test.js
python tools/smoke_irms.py --mode production --session-secret dummy-secret-0000
python tools/check_repo_hygiene.py                          # 청소 완료 후 OK 여야 함
```

- CI(`.github/workflows/test.yml`)가 push 시 pytest + JS + (신설) hygiene을 자동 실행.
- 수동 확인 1회: 개발 PC에서 `python -c "import serve; serve.backup_db()"` 실행(테스트 `IRMS_DATA_DIR` 지정 상태) → 로그에 "백업 검증 OK" 출력 확인.

## 6. 수용 기준 (체크리스트)

- [ ] `backup_db()`가 생성 직후 사본을 검증하고, 검증 실패 사본은 `irms_*.db.corrupt`로 격리되어 미러·정상 prune 대상에서 빠진다.
- [ ] 손상/테이블 누락/비 SQLite 사본에 대해 `_verify_backup()`이 `False`를 반환하는 실패 시나리오 테스트가 존재하고 통과한다.
- [ ] 미러 폴더에는 검증 통과본만 복사된다.
- [ ] `python tools/verify_backup.py`가 최신 백업에 대해 PASS/FAIL과 exit 0/1을 반환한다.
- [ ] `docs/ops-backup-restore.md`에 복구 절차와 분기 리허설 체크리스트가 있고, 리허설 1회 수행 이력이 기록됐다.
- [ ] 저장소 루트 1단계에 `tmp_*`/`tmpivkqp8nt`/`__pycache__`/`.venv-wsl-backup-*`/PNG/JPG가 없다 (`check_repo_hygiene.py` OK, 디스크 120MB+ 회수).
- [ ] 한글 엑셀 4개가 `excel\legacy\`에 존재하고(내용 무손실), `.gitignore`가 해당 경로를 무시한다.
- [ ] `tools/smoke_irms.py` 기본 data-dir가 `.tmp-tests/smoke_runtime`이고, 실행해도 루트에 새 디렉터리가 생기지 않는다.
- [ ] CI에 hygiene 스텝이 pytest 앞에 추가되어 green이다.
- [ ] `data\`, `backups\`, `.venv\`, `excel\`(legacy 이동분 외), `src/resources/**`, `cloudflared/`가 청소 전과 동일하다(무접촉 — `data\` 내부 `__tmp_*` 포함 그대로).
- [ ] 전체 게이트(§5.3) green + `serve.py` 변경 커밋이 운영 자동 반영된 뒤 일일 백업 로그에 "백업 검증 OK"가 찍힌다.

## 7. 하지 말 것 (스코프 밖)

- **`data\` 디렉터리 무접촉**: 라이브 `irms.db`(-shm/-wal 포함)는 물론, 그 옆의 `data\__tmp_status_*`·`data\audit_ui_test_db\`·`data\signature_samples\`도 이번에 지우지 않는다 — 라이브 DB와 같은 폴더에서의 삭제 작업 자체가 사고 표면이다. data 내부 잔재 정리는 별도 과제로 미룬다.
- **`backups\` 무접촉**: 기존 백업 파일 삭제·개명 금지(`prune_backups`의 기존 자동 정리는 예외 — 로직 변경도 `.corrupt` 보존 규칙 추가 외 금지). `irms_pre_plan_drop_*.db` 같은 수동 스냅샷도 보존.
- `.venv\`(현행 런타임), `excel\`의 기존 파일, `src/resources/signature/**`(gitignore 예외 처리된 서명 자산), `cloudflared/`(터널 시크릿), `artifacts\`, `scripts\`, `docs\` 삭제 금지.
- `serve.py`의 자동 업데이트·포트 정리·재시작 로직(`has_update`, `apply_update`, `free_port`, `main` 루프) 수정 금지 — 이번 변경은 `backup_db`/`prune_backups` 주변과 상수 추출로 한정.
- 파괴적 검증 금지: `_verify_backup`에 쓰기 동작(VACUUM, REINDEX, wal_checkpoint 등)을 넣지 않는다. `mode=ro` 유지.
- ruff/pyproject.toml 도입(P1-2), `OperationalError` 폴백 제거(P0-4), 세션 통합(P1-1), Windows 서비스화(P1-4)는 이 지시서 범위가 아니다 — "하는 김에" 병행 금지.
- 라이브 운영 서버(다른 PC)에서 변경성 기능을 직접 테스트하지 않는다(대시보드 = 라이브 실데이터).
- 커밋 혼합 금지: 백업 검증 / 위생 도구·CI / 문서를 별도 커밋으로 — 자동 배포 특성상 한 커밋 = 한 운영 반영.

## 8. 롤백 방법

- **serve.py 백업 검증**: `git revert <커밋>` → 다음 자동 업데이트 주기에 운영 반영(또는 운영 PC에서 수동 `git pull` + 재시작). 데이터·스키마 변화가 없으므로 revert만으로 완전 원복. 이미 생성된 `.corrupt` 파일은 수동 삭제(무해).
- **위생 스크립트/CI**: 커밋 revert로 CI 스텝·스크립트 제거. hygiene 실패로 CI가 막히면(예: 부득이한 루트 산출물) 우선 해당 파일을 `.tmp-tests/`로 옮기는 것이 원칙이며, 긴급 시 스텝을 임시 주석 처리하는 커밋으로 우회.
- **청소 롤백**: 격리 방식(§4.4-(3)-4)을 따랐다면 `.tmp-tests\quarantine-*\`에서 원위치 복사로 복구 가능(2주 관찰 기간 내). 즉시 삭제 허용 대상(venv 백업·캐시)은 `python tools/bootstrap_irms.py` 및 도구 재실행으로 재현.
- **엑셀 이동 롤백**: `excel\legacy\` → 루트로 되돌리면 끝(이동만 했으므로 무손실).
- **smoke 기본 경로**: revert 시 기존 `tmp_smoke_runtime` 경로로 복귀 — 기능 영향 없음.
