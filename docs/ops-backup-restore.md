# 백업·복구 운영 절차 (IRMS/BRM)

> 대상: 운영 서버(`serve.py` 가 가동되는 PC). serve.py 의 일일 자동 백업 + 업데이트 직전
> 백업은 이제 **생성 직후 자동 검증**(`_verify_backup`)되며, 검증 실패 사본은
> `irms_*.db.corrupt`로 격리된다(미러 대상에서 제외). 본 문서는 장애 시 수동 복구 절차와
> 분기 리허설 체크리스트를 담는다.

## 1. 백업 구조 (요약)

- **생성**: `serve.py:backup_db()` — 매일 1회(감시 루프) + 업데이트 직전(`apply_update()`).
  SQLite 온라인 백업 API 사용(서버 가동 중에도 트랜잭션 일관된 사본). 실패 시 `shutil.copy2` 폴백.
- **자동 검증**: 생성 직후 `_verify_backup(dest)` 가 읽기 전용(`mode=ro`)으로
  `PRAGMA integrity_check == 'ok'` 와 핵심 테이블(`recipes`, `recipe_items`,
  `blend_records`, `blend_details`, `workers`, `audit_logs`) 존재·COUNT 조회를 수행.
  - 통과 → `_mirror_backup()` 으로 2차 사본(설정 시) + `prune_backups()` 보존 규칙 적용.
  - **실패** → `irms_*.db.corrupt` 로 개명 격리(미러 미전달, 원인 분석용 보존·최근 2개).
- **수동 검증**: `python tools/verify_backup.py [backups\<파일>]` — 인자 없으면 최신 사본.
  `PASS`/`FAIL` 출력, exit 0/1. serve.py 와 **동일 로직** 재사용(이원화 금지).
- **보존**: `IRMS_BACKUP_KEEP_DAYS`(기본 30일) + 최근 `BACKUP_KEEP_MIN`=5개 항상 유지.
- **2차 사본**: `IRMS_BACKUP_MIRROR`(예: `D:\irms-backup`) 미설정 시 단일 디스크 리스크 —
  **백업 폴더가 있는 디스크 고장 시 전량 손실**되므로 외장/네트워크 폴더 설정을 권장.

## 2. 복구 절차 (장애 시)

> 라이브 서버가 손상/삭제된 경우에만 수행. 순서대로, 건너뛰지 말 것.

1. **서버 중지**: 운영 콘솔에서 `Ctrl+C` 로 `serve.py` 종료(서버·감시 루프 함께 정지).
2. **복구 후보 검증**: 복구에 쓸 백업을 읽기 전용으로 검증 —
   `python tools/verify_backup.py backups\<선택 파일>` → `PASS` 확인.
   `FAIL` 이면 다른 백업 후보로 다시 시도(`backups\` 의 다른 최근 사본).
3. **현행 DB 대피**: `data\irms.db`(및 `-shm`/`-wal` 이 있으면 함께)를
   `data\irms.db.pre_restore_<yyyymmdd_HHMMSS>` 로 이름 변경(롤백용 보존).
4. **백업 복사**: 검증 통과한 백업을 `data\irms.db` 로 복사
   (`copy backups\<선택 파일> data\irms.db`). `-wal`/`-shm` 은 복사하지 않는다.
5. **재기동**: `run_auto.bat` 실행 → 서버 시작 로그 확인.
6. **정상 확인**:
   - `curl http://127.0.0.1:<PORT>/health` → `200` + `{"status":"ok"}`.
   - 브라우저 `/status`(배합 기록)에서 최신 데이터가 보이는지 육안 확인.
   - `/management`(레시피), `/viscosity`(점도) 도 1회씩 조회 확인.
7. (선택) 대피한 `data\irms.db.pre_restore_*` 는 안정 운영 확인 후(수일 뒤) 삭제.

## 3. 분기 리허설 체크리스트 (분기 1회, 라이브 무접촉)

> 복구 절차를 실제 DB 에서 훈련하는 것이 아니라 **복사본**에서 리허설. 라이브 서버 무접촉.

- [ ] 최신 백업을 `.tmp-tests\restore-rehearsal\` 에 복사.
- [ ] `python tools/verify_backup.py .tmp-tests\restore-rehearsal\<복사본>` → `PASS`.
- [ ] 복사본을 `.tmp-tests\restore-rehearsal\irms.db` 로 개명.
- [ ] `set IRMS_DATA_DIR=.tmp-tests\restore-rehearsal` 후
      `uvicorn src.main:app --port 9100` 기동(운영 포트와 충돌 피함).
- [ ] `/health` 정상 + 로그인·배합 기록 조회 확인.
- [ ] 서버 종료 후 `.tmp-tests\restore-rehearsal\` 디렉터리 삭제.
- [ ] 아래 이력 표에 1줄 기록(날짜·결과·특이사항).

## 4. 분기 리허설 이력

| 일자 | 결과 | 비고 |
|--------|------|------|
| _(최초 1회 수행 후 기록)_ | | |

---

**참고**: `serve.py` docstring 의 복구 안내(15~17행)가 본 문서로 승격·확장됨.
`CLAUDE.md` 의 "DB 백업" 절과 본 문서는 함께 갱신한다.
