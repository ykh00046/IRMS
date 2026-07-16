# 품목코드 마스터 이관 운영 절차 (IRMS)

> 대상: 운영 서버(`serve.py` 가 가동되는 PC)를 직접 쓰는 운영자. 개발 지식 불필요.
> 목적: ERP 품목코드 마스터(code*.xlsx)를 IRMS DB 에 넣고, 자재·레시피에 코드를 자동
> 매칭하는 작업을 **안전하게**(백업 → 건조건 확인 → 보고서 검토 → 반영) 수행한다.
> 함께 볼 문서: `docs/ops-backup-restore.md`(백업·복구 기본 절차).

## 0. 사전 조건 (매번 확인)

- **운영 PC 가 최신인지**: 서버가 켜질 때 자동으로 `git pull` 을 하므로, 서버를 한 번
  껐다 켜면 최신 상태가 된다. 절차 전에 서버를 재기동해 두면 안심.
- **마이그레이션은 자동**: 품목코드에 필요한 테이블(`item_code_master`)·컬럼은 **서버가
  시작될 때 스스로 만든다**. 이 절차에서는 채우는 작업(엑셀 임포트 + 자동 매칭)만 한다.
- **명령줄 창 열기**: `Win+R` → `cmd` 입력 → 엔터. 아래 명령은 모두 IRMS 폴더
  (보통 `C:\X\IRMS`)에서 실행한다. 폴더 이동은 `cd /d C:\X\IRMS`.

> 아래 명령의 `python` 은 **`run_irms.bat` 이 쓰는 파이썬**을 뜻한다. IRMS 폴더에
> `.venv\Scripts\python.exe` 가 있으면 그것을, 없으면 시스템 `python` 을 쓴다. 이 문서의
> 예시는 가상환경이 있는 경우로 통일한다 — 없으면 `python` 으로 바꿔 치면 된다.
> 명령줄에서 확인: `if exist .venv\Scripts\python.exe (echo 가상환경 사용) else (echo python 사용)`

## 1. 백업 확인 (가장 중요)

임포트 전에 **반드시** 최신 백업이 있는지 확인한다. 서버(`serve.py`)는 매일 1회 + 업데이트
직전에 자동으로 `backups\` 폴더에 백업을 만든다. 이 단계에서는 백업을 **직접 만들지 않고**
확인만 한다.

```
dir backups\irms_*.db /O-D
```

- 가장 위(최신) 파일의 날짜·시각이 **오늘 작업 시점과 가까운지** 확인.
- 만약 최신 백업이 며칠 전이라면: 서버를 켜 두고 다음 날 자동 백업을 기다리거나,
  `docs/ops-backup-restore.md` 의 수동 백업 절차를 참고해 한 부 더 만든다.
- 백업이 없으면 이 절차를 **시작하지 말 것** — 문제 시 되돌릴 수 없다.

## 2. ERP 엑셀 받아서 폴더에 두기

ERP 에서 품목코드 마스터 엑셀 **4종**을 받는다. 파일명은 반드시 아래와 같아야 한다.

| 파일 | 내용 |
|---|---|
| `code.xlsx` | 전 품목 마스터(원자재 포함) |
| `code2.xlsx` | 반제품 · 잉크코드 |
| `code3.xlsx` | 반제품 · 합성코드 |
| `code4.xlsx` | 반제품 · 약품코드 |

받은 4개 파일을 운영 PC 의 **`data\master\`** 폴더에 넣는다.

```
mkdir data\master        (폴더가 없을 때만. 있으면 건너뜀)
copy 받은위치\code.xlsx  data\master\
copy 받은위치\code2.xlsx data\master\
copy 받은위치\code3.xlsx data\master\
copy 받은위치\code4.xlsx data\master\
dir data\master\code*.xlsx
```

- `code*.xlsx` 4개가 모두 보이면 다음 단계로.
- 이 폴더는 커밋 대상이 아니다(자동 제외됨) — 원본은 ERP 가 원천.

## 3. 마스터 임포트 — 먼저 건조건(dry-run)

먼저 **변경 없이** 요약만 본다(`--dry-run`). 숫자가 예상과 맞는지 확인하는 단계.

```
.venv\Scripts\python.exe tools\import_item_codes.py --material data\master\code.xlsx --product data\master\code2.xlsx --product data\master\code3.xlsx --product data\master\code4.xlsx --dry-run
```

정상 출력 예시(실제 실행 결과 복사 — 파일 내용이 같으면 동일하게 나온다):

```
[db] 대상: (기본 개발 DB)
[원자재(예정)] data\master\code.xlsx: read=9565 imported=200 skipped(비원자재)=9365 skipped(빈값)=0
[반제품(예정)] data\master\code2.xlsx: read=1722 imported=1722 skipped(빈값)=0 분류={'잉크': 1722}
[반제품(예정)] data\master\code3.xlsx: read=42 imported=42 skipped(빈값)=0 분류={'합성': 42}
[반제품(예정)] data\master\code4.xlsx: read=135 imported=135 skipped(빈값)=0 분류={'약품': 135}
[총계] material=200 product=1899 [DRY-RUN — 변경 없음]
```

확인 포인트:
- **원자재 imported=200** (AS/AC/AH/AW 계열 200행 — 배합 원료 범위). 9,000행대가 skip 되는
  것은 정상(포장재·상품 등 배합과 무관).
- **반제품 product 합계 = 1,899** (잉크 1,722 + 합성 42 + 약품 135).
- 마지막 줄에 `[DRY-RUN — 변경 없음]` 이 있어야 한다 — DB 는 아직 안 건드림.
- 숫자가 크게 다르면(예: 원자재가 0이거나 반제품이 수백 건) 엑셀이 잘못됐을 수 있음 —
  ERP 에서 다시 받아 `data\master\` 에 덮어두고 재실행.

이상 없으면 **실제 임포트** — 같은 명령에서 `--dry-run` 만 빼고 실행.

```
.venv\Scripts\python.exe tools\import_item_codes.py --material data\master\code.xlsx --product data\master\code2.xlsx --product data\master\code3.xlsx --product data\master\code4.xlsx
```

출력은 dry-run 과 같되 마지막 줄이 `[총계] material=200 product=1899`(DRY-RUN 표시 없음).
이것으로 `item_code_master` 표에 마스터가 채워졌다. (재실행해도 안전 — 같은 코드는 갱신만.)

## 4. 자동 매칭 — 보고서 먼저 보고 검토

마스터를 바탕으로, 기존 자재·레시피에 코드를 자동으로 연결한다. **보고서 먼저**(`--apply`
없이) 실행해 눈으로 확인한다.

```
.venv\Scripts\python.exe tools\match_item_codes.py
```

출력은 크게 ① 자재 매칭 ② 레시피 매칭 ③ 총계 로 나뉜다. **반영 전 반드시 아래 항목들을
확인**한다 — `--apply` 를 줘야 실제로 코드가 들어가므로, 이 검토가 끝나기 전엔 절대 apply
하지 말 것.

### 보고서 검토 체크리스트 (운영 실측 기준 — docs/01-plan/features/item-code.plan.md §7)

- [ ] **자재 모호(GMMA 등)**: 마스터에 같은 이름으로 코드가 2개 이상이면 '자재 모호' 에
      나온다. 자동 부여 대상이 아니므로 **사람이 어느 제조사 코드인지 판단**해야 한다.
      대표 사례: **GMMA**(제조사 구분으로 2코드), DMA, MCR-C12 계열. 보고서의 '자재 모호'
      목록에 나온 항목은 수동으로 코드를 지정하거나, 두 코드 중 하나를 쓸지 결정.
- [ ] **미매칭 안료류**: '자재 미매칭' 에 나온 이름 중 안료 계열(카본블랙, RAVEN, BLACK,
      WHITE, CS Pigment 등)은 ERP 마스터 표기와 달라 자동 연결이 안 됐을 수 있다. 유사
      후보가 같이 찍히면 참고해 수동 확정; 후보조차 없으면 ERP 등록 누락이거나 표기가 다른
      것 — 나중에 마스터를 보완하거나 해당 자재에 코드를 수동 입력.
- [ ] **분류 충돌(레시피)**: '분류 충돌' 목록은 기존에 지정된 레시피 분류(잉크/합성/약품)가
      마스터의 제품구분(hint)과 다른 경우. 자동으로 덮지 않으므로(보고만), 운영자가 어느
      쪽이 맞는지 판단. **§7 실측에서는 7건** 분류 충돌이 보고되었다.
- [ ] **자재 코드 중복(Glycerol)**: 같은 ERP 코드에 2개 이상 자재가 매칭되면 `--apply` 시
      첫 자재에만 부여되고 나머지는 '코드 중복 skip' 으로 빠진다. 대표 사례: **Glycerol**
      동일 품목 중복 등록. apply 출력에서 이 skip 이 뜨면 해당 중복 자재는 운영 데이터
      정리 대상(나중에 한 쪽을 비활성화/병합).
- [ ] **레시피 미매칭(변형명)**: '레시피 미매칭' 중 SBCT-1→BC2000 계열, N2-TOP→B0127 계열
      같이 변형명은 유사 후보가 찍히지만 자동 확정은 안 된 것들. 유사 후보를 보고 맞으면
      수동으로 product_code 지정.

> 이 항목들은 모두 **보고서에만 나오고 apply 는 안 된 상태**다. 이상이 없거나(또는 남은
> 것은 수동 처리하겠다고 결정) 그 다음 apply 단계로 간다.

### 검토 완료 후 — 실제 반영

```
.venv\Scripts\python.exe tools\match_item_codes.py --apply
```

출력 맨 아래에 반영 요약이 찍힌다:
- `자재 code 부여: N건` — 확정 자재에만 코드 부여(모호·미매칭 제외).
- `자재 코드 중복 skip: N건` — 같은 코드 충돌(위 체크리스트 Glycerol 등).
- `레시피 product_code 부여: N건 (분류 채움: N건)` — 확정 레시피에 코드 + 비어있는
  분류를 hint 로 채움(기존 분류와 다르면 건드리지 않음 — 충돌은 보고만 했으므로).

## 5. 검증 (반영됐는지 확인)

적용 후 코드가 실제로 보이는지 확인한다. 두 가지 방법.

**A. 브라우저에서 확인** (권장):
- `/status`(배합 기록) 또는 `/management`(레시피) 화면에서 코드가 찍힌 레시피/자재가
  보이는지.
- 레시피 등록·수정 화면에서 마스터에 있는 자재 이름을 넣으면 **자동 인식**(코드 자동 표시).
- `/public/material-usage`(ERP 연동 원자재 사용량) 에서 `erp_code` 가 빈 행이 줄었는지 —
  마스터 매칭이 되면 빈 행이 skip 되던 집계 누락이 사라진다.

**B. 명령줄 카운트** (숫자로):
```
.venv\Scripts\python.exe -c "import sqlite3; c=sqlite3.connect('data/irms.db'); print('자재 코드 보유:', c.execute('SELECT COUNT(*) FROM materials WHERE code IS NOT NULL').fetchone()[0]); print('레시피 코드 보유:', c.execute('SELECT COUNT(*) FROM recipes WHERE product_code IS NOT NULL').fetchone()[0]); print('마스터 행:', c.execute('SELECT COUNT(*) FROM item_code_master').fetchone()[0])"
```
- `자재 코드 보유` / `레시피 코드 보유` 가 apply 요약 숫자와 대략 일치하면 정상.

## 6. 문제 시 복구 (되돌리기)

매칭 결과가 이상하거나 실수로 잘못 넣었을 때. 임포트·apply 모두 "코드를 채우는" 작업이므로,
**백업으로 되돌리면** 깨끗한 상태로 복구된다. 절차는 `docs/ops-backup-restore.md` §2 와
동일(요약만):

1. **서버 중지**: 운영 콘솔에서 `Ctrl+C`.
2. **복구 후보 검증**: `python tools\verify_backup.py backups\<임포트 직전 백업>` → `PASS`.
3. **현행 DB 대피**: `data\irms.db` 를 `data\irms.db.pre_itemcode_<yyyymmdd_HHMMSS>` 로 rename.
4. **백업 복사**: `copy backups\<선택 파일> data\irms.db`.
5. **재기동**: `run_irms.bat`.
6. **정상 확인**: `/health` 200 + 브라우저에서 데이터 확인.

상세는 `docs/ops-backup-restore.md` §2 참고. 임포트 자체는 upsert 이므로, 잘못된 마스터
내용을 다시 임포트해 덮어쓰는 것도 가능하지만, 가장 확실한 방법은 백업 복구다.

## 7. 마스터 갱신 (나중에 다시 할 때)

ERP 품목 마스터가 바뀌면(신규 품목 추가 등) 같은 절차를 **그대로 반복**한다. 임포트·매칭
모두 **안전하게 다시 실행 가능(upsert)**:

- `import_item_codes.py`: 같은 코드는 **갱신**(imported_at 최신화), 새 코드는 추가.
  중복 행이 늘지 않는다.
- `match_item_codes.py --apply`: 이미 코드가 부여된 자재/레시피는 **대상에서 제외**되므로,
  새로 추가된 것에만 코드가 들어간다(멱등). 두 번 돌려도 결과가 같다.

즉 절차: **§1 백업 확인 → §2 새 엑셀을 `data\master\` 에 덮어두기 → §3 dry-run → 실제
임포트 → §4 매칭 보고서 → 검토 → apply → §5 검증**. 되돌리기(§6)는 백업이 있으면 언제든.
