# 배합 실적(잉크 계량 재구축) 기획서 — blend-overhaul

## 목표
외부 PySide6 데스크톱 앱 `C:/X/Program-estimation`(배합/DHR Generator)의 기능·자산을
IRMS 웹(FastAPI+Jinja2+vanilla JS)으로, 우리 UI/형태에 맞게 이식하고, 기존 잉크 계량
시스템을 대폭 개선·재구축한다. 여기서 잡히는 기록과 점도를 연계한다.

## 범위
1. 데이터: blend_records / blend_details + 점도 연계(viscosity_readings.blend_record_id)
2. 배합 입력: 레시피(절대중량)→비율(%) 환산→배치 총량 비례 이론량→실제량·자재LOT·작업자·저울 입력→저장
3. product_lot 자동생성 {제품}{YYMMDD}{순번}
4. 기록 조회: 기간/작업자/검색 + 상세(DHR 실적서)
5. DHR 문서: 웹 인쇄(@media print) + Excel(openpyxl) 내보내기
6. 재고 자동차감(실제/이론) + 취소 시 복원, 소비예측 연계
7. 점도 연계: 배합 product_lot 로 점도 등록, blend_record_id FK
8. 결재 기록(작성/검토/승인 — 이름+시각). ⚠️원본의 서명 이미지 위조/스캔효과는 의도적 제외
9. (후속) 일괄 생성, 자재 LOT 추천(material_lots 활용)

## 의사결정
- 테마: IRMS 라이트 유지(앰버/골드는 절제 강조색). "우리 UI에 맞게".
- 접근: 점도와 동일하게 무로그인 개방(사내 공용 단말). 작성자=로그인 또는 '현장'.
- 데스크톱 전용(Excel COM/win32/PySide6)은 웹 등가물로 대체.
- 서명 위조(손글씨 이미지 무작위 변형+스캔노이즈)는 윤리적 사유로 미이식 → 결재 기록으로 대체.

## 비범위
- Google Sheets 백업(원본 기능) — 후속 검토
- 구버전 weighing 즉시 제거 — 배합이 검증된 뒤 단계적 대체

## 수용 기준
- 레시피→비율→이론량 환산 정확, product_lot 순번 멱등
- 저장 시 재고 차감, 취소 시 정확 복원
- 점도가 배합 기록과 연계
- 전체 pytest 통과
