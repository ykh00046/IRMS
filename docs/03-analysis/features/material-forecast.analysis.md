# 자재 소모량 예측·발주 추천 — Analysis (Gap + Quality)

| 항목 | 값 |
|------|------|
| Feature | `material-forecast` |
| Phase | Check (Gap + 품질 분석) |
| 분석일 | 2026-06-01 |
| Match Rate | **98% → ~99%** (보강 후) |
| 품질 점수 | **88/100** (Critical/High 0건) |

## 1. Gap 분석 (설계 ↔ 구현)

`bkit:gap-detector` 수행. 설계 문서 §2~§8 전 항목 대조.

| 섹션 | 일치율 | 비고 |
|------|:-----:|------|
| §2 데이터 모델 | 100% | 컬럼 2개 + 마이그레이션 위치 일치 |
| §3 알고리즘 | ~95%→100% | 클램프·params 키 설계서 반영으로 해소 |
| §4 API | 100% | 3개 엔드포인트 + Body + CSV 컬럼/파일명 일치 |
| §5 UI | 100% | 탭/패널/배너/모달/스크립트 일치 |
| §6 권한·보안 | 100% | manager 전용, audit, 입력검증 |
| §7 테스트 | ~90%→100% | T7(export only_reorder) 라우트 테스트 추가로 해소 |
| §8 변경 파일 | 100% | 9개 파일 모두 일치, 미문서 파일 0 |

### 보강 조치 (Iterate)
1. **T7 미커버 분기 해소** — `test_export_only_reorder_filters_rows` 추가(인증 우회 + 실 DB 시드). `only_reorder` CSV 필터 분기 커버.
2. **설계서 정합화** — 소진 예상일 클램프(`max(0, int(...))`) 명문화, `params` 반환 키 명시.

## 2. 코드 품질·보안 분석

`bkit:code-analyzer` 수행. 점수 88/100, **Critical/High 0건**.

| 점검 항목 | 결과 |
|----------|------|
| SQL 인젝션 / 입력 검증 | PASS (전부 파라미터 바인딩, Query ge/le, Field ge=0 + 서버 재검증) |
| 0 나눗셈 / None 처리 | PASS (avg_daily<=0 조기 분기, `or 0` 코얼레싱) |
| 권한 경계 | PASS (라우터 전체 manager, operator 403) |
| XSS (forecast.js) | PASS (escapeHtml 전 필드 적용) |
| stock 패턴 일관성 | STRONG |
| 성능 (집계 쿼리) | PASS (쿼리 2회, idx_stock_logs_material 활용, N+1 없음) |

### 반영한 Low 권고 2건
1. `predicted_stockout_date`에 `escapeHtml` 적용 (forecast.js).
2. CSV 수식 인젝션 방어 — `=,+,-,@,\t,\r` 시작 텍스트에 `'` 접두 (`_csv_safe`).

### 보류(트레이드오프 명시)
- `material_stock_logs` 대용량 시 `created_at` 단독 인덱스 — 현 데이터량에서 불필요, 향후 과제.
- 소모 집계 시 `unit_type='weight'` 사전 필터 — 마이크로 최적화, 정확성 영향 없어 보류.

## 3. 결론

설계-구현 정합 우수(~99%), 품질 양호(88), 보안 차단요소 없음. 배포 적합.
