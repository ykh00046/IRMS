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
2. **복구 실행** — 아래 한 줄이 검증·대피·복사를 순서대로 처리한다(권장):

   ```
   python tools/restore_backup.py
   ```

   가장 최근 정상 백업을 자동으로 고르고, 다른 파일을 쓰려면
   `--backup backups\irms_YYYYMMDD_HHMMSS.db` 를 붙인다. 백업이 손상됐으면
   **아무것도 건드리지 않고 중단**하며, 기존 `irms.db`·`-wal`·`-shm` 은
   `data
estore-before-<시각>\` 으로 **함께 대피**된다(삭제 아님).

   > ⚠ 손으로 복사하지 말 것. `-wal` 을 남겨두고 `.db` 만 바꾸면 **옛 WAL 프레임이
   > 새 DB 위에 재생돼 데이터가 뒤섞인다**(WAL 은 DB 파일과 묶여 있지 않다).
   > 이 스크립트는 그 실수를 불가능하게 만들려고 만든 것이다.

   손으로 해야 하는 상황이면: 검증(`python tools/verify_backup.py backups\<파일>` → `PASS`)
   → `data\irms.db`·`-wal`·`-shm` **셋 다** 다른 폴더로 이동 → 백업을 `data\irms.db` 로 복사.
3. **재기동**: `run_auto.bat` 실행 → 서버 시작 로그 확인.
4. **정상 확인**:
   - `curl http://127.0.0.1:<PORT>/health` → `200` + `{"status":"ok"}`.
   - 브라우저 `/status`(배합 기록)에서 최신 데이터가 보이는지 육안 확인.
   - `/management`(레시피), `/viscosity`(점도) 도 1회씩 조회 확인.
5. (선택) 대피한 `data
estore-before-*` 는 안정 운영 확인 후(수일 뒤) 삭제.

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
| 2026-07-26 | **PASS** | 개발 PC 실연(`.tmp-tests/restore-drill`). ①`serve.py backup_db()` 로 백업 생성 → 자동 검증 OK ②`irms.db` 를 손상시키고 stale `irms.db-wal` 을 남긴 뒤 복구 ③`.db`·`-wal` 이 **함께** `restore-before-20260726_121807\` 로 대피, 복구본에 재생되지 않음 ④복구 후 `integrity_check=ok`, 19 테이블. 부수 확인: 2026-05 백업(배합 기능 이전)은 핵심 테이블 부재로 **정상 거부** — 스키마가 크게 바뀐 시점 이전 백업으로는 복구할 수 없다는 뜻이므로, 큰 변경 직후에는 새 백업이 하나 이상 쌓인 것을 확인할 것 |

---

**참고**: `serve.py` docstring 의 복구 안내(15~17행)가 본 문서로 승격·확장됨.
`CLAUDE.md` 의 "DB 백업" 절과 본 문서는 함께 갱신한다.
