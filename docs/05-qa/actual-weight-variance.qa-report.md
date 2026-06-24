# QA Report: actual-weight-variance

> **Date**: 2026-06-18
> **Verdict**: QA_PASS
> **Pass Rate**: 100%
> **Critical Issues**: 0
> **Feature**: actual-weight-variance

## 1. Test Summary

| Level | Type | Status | Pass Rate | Failed |
|-------|------|:------:|:---------:|:------:|
| L1 | Unit Test | PASS | 100% | 0 |
| L2 | API/Contract Static | PASS | 100% | 0 |
| L3 | Regression Suite | PASS | 100% | 0 |
| L4 | JS Module Test | PASS | 100% | 0 |
| L5 | Data Flow Integrity | PASS | 100% | 0 |

## 2. Executed Commands

```powershell
python -m py_compile src\services\variance_service.py src\routers\dashboard_routes.py src\routers\weighing_routes.py src\routers\models.py
node --check static\js\dashboard.js
node --check static\js\work\weighing-actions.js
node --check static\js\work\weighing-render.js
pytest -q tests\test_variance_service.py
pytest -q tests\test_stock_service.py tests\test_forecast_dashboard_alert.py tests\test_recipe_helpers_pure.py
pytest -q
node tests\js\work_pure.test.js
node tests\js\attendance_view.test.js
node tests\js\chat_stage_and_tts.test.js
node tests\js\common_speech_queue.test.js
node tests\js\management_lookup.test.js
```

## 3. Results

- Full Python suite: 197 passed, 1 warning, 10 subtests passed.
- JS scripts: 5 passed.
- New variance service tests: 3 passed.
- `npm test` skipped because this repository has no `package.json`; direct Node scripts are the existing JS test mechanism.

## 4. Critical Issues

None.

## 5. Metrics

| Metric | Value |
|--------|-------|
| M11 QA Pass Rate | 100% |
| M12 Test Coverage (L1) | service summary/top/detail covered |
| M13 E2E Coverage | direct browser E2E not present in repo |
| M14 Runtime Error Count | 0 in automated tests |
| M15 Data Flow Integrity | PASS: actual input -> DB -> variance service -> dashboard API/client |

## 6. Recommendations

- 운영 배포 후 실측 입력률(`coverage_pct`)이 낮으면 작업자 교육 또는 저울 자동 연동을 다음 PDCA로 검토한다.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-06-18 | Final QA report |
