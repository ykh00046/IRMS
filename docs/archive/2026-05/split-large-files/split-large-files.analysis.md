# split-large-files Gap Analysis (Phase 1)

> **Match Rate**: **99%** — Implementation matches design across all 8 evaluation categories. Single Minor documentation-side gap. No code change required to proceed.
>
> **Phase**: Check (PDCA)
> **Date**: 2026-05-12
> **Commit**: `522d39b`
> **Agent**: bkit:gap-detector
> **Recommendation**: Proceed to `/pdca report split-large-files`

---

## 1. Overview

| Item | Value |
|---|---|
| Analysis Target | split-large-files Phase 1 (Python `recipe_routes.py` split) |
| Design Document | `docs/02-design/features/split-large-files.design.md` |
| Plan Document | `docs/01-plan/features/split-large-files.plan.md` |
| Implementation Commit | `522d39b` |
| pytest | 32/32 PASS |
| Code Files Verified | 6 new + 3 modified + 1 deleted |
| Endpoints Verified | 22/22 |

---

## 2. Endpoint Completeness Check (design §3)

**Result**: 22/22 (100%) — method, path, and auth scope all preserved.

### 2.1 `recipe_operator_routes.py` — 9/9 (operator)

| Method | Path | File:Line |
|---|---|---|
| GET | `/notifications/recipe-imports` | recipe_operator_routes.py:48 |
| GET | `/materials` | recipe_operator_routes.py:66 |
| GET | `/recipes/products` | recipe_operator_routes.py:102 |
| GET | `/recipes/by-product` | recipe_operator_routes.py:111 |
| GET | `/recipes/{recipe_id}/detail` | recipe_operator_routes.py:149 |
| GET | `/recipes/{recipe_id}/history` | recipe_operator_routes.py:192 |
| GET | `/recipes/history/compare` | recipe_operator_routes.py:225 |
| GET | `/recipes` | recipe_operator_routes.py:336 |
| PATCH | `/recipes/{recipe_id}/status` | recipe_operator_routes.py:395 |

### 2.2 `recipe_manager_routes.py` — 3/3 (manager)

| Method | Path | File:Line |
|---|---|---|
| DELETE | `/recipes/{recipe_id}` | recipe_manager_routes.py:28 |
| GET | `/recipes/progress` | recipe_manager_routes.py:60 |
| GET | `/recipes/operator-progress` | recipe_manager_routes.py:154 |

### 2.3 `stock_routes.py` — 6/6 (op + mgr tuple)

| Method | Path | File:Line | Auth |
|---|---|---|---|
| GET | `/materials/stock` | stock_routes.py:39 | operator |
| GET | `/materials/{material_id}/stock-log` | stock_routes.py:45 | operator |
| POST | `/materials/{material_id}/stock/restock` | stock_routes.py:52 | manager |
| POST | `/materials/{material_id}/stock/adjust` | stock_routes.py:79 | manager |
| POST | `/materials/{material_id}/stock/discard` | stock_routes.py:106 | manager |
| PATCH | `/materials/{material_id}/stock-threshold` | stock_routes.py:133 | manager |

### 2.4 `recipe_import_routes.py` — 2/2 (manager)
- POST `/recipes/import/preview` — recipe_import_routes.py:29
- POST `/recipes/import` — recipe_import_routes.py:35

### 2.5 `recipe_stats_routes.py` — 2/2 (manager)
- GET `/stats/consumption` — recipe_stats_routes.py:29
- GET `/stats/export` — recipe_stats_routes.py:94

---

## 3. Helper Extraction Check (design §4.1)

All 5 helpers exist as **module-level public** functions in `src/services/recipe_helpers.py`:

| Helper | File:Line | Imported By |
|---|---|---|
| `format_display_value` | recipe_helpers.py:19 | weighing_routes.py:8 |
| `fetch_recipe_items` | recipe_helpers.py:30 | recipe_operator_routes.py:37, recipe_manager_routes.py:22 |
| `find_chain_root` | recipe_helpers.py:65 | recipe_operator_routes.py:37 |
| `fetch_chain` | recipe_helpers.py:84 | recipe_operator_routes.py:37 |
| `ensure_material` | recipe_helpers.py:105 | stock_routes.py:26 |

Underscore prefix correctly removed (public symbols). All actually consumed.

---

## 4. Pydantic Model Migration Check (design §4.2)

| Model | models.py:Line | Imported |
|---|---|---|
| `StockAmountBody` | models.py:30 | stock_routes.py:29 ✓ |
| `StockAdjustBody` | models.py:35 | stock_routes.py:28 ✓ |
| `StockDiscardBody` | models.py:40 | stock_routes.py:30 ✓ |
| `StockThresholdBody` | models.py:45 | stock_routes.py:31 ✓ |

Zero references to `_StockAmountBody` / `_StockAdjustBody` / `_StockDiscardBody` / `_StockThresholdBody` anywhere in the code (only in the design doc as historical record).

---

## 5. `weighing_routes.py` Edit Check (design §4.3)

| Required Edit | Status |
|---|:---:|
| L8 — top-level import replaced | ✅ `from ..services.recipe_helpers import format_display_value` |
| L66 — shadow import inside `get_weighing_queue` removed | ✅ shadow import gone |
| L72 — first call site | ✅ `format_display_value(...)` |
| L197 — second call site | ✅ `format_display_value(...)` |

Zero remaining references to `_format_display_value` anywhere in `src/`.

---

## 6. `api.py` Registration Order Check (design §6.2)

Exact match across all 15 routers (Phase 1 scope) — public → public_attendance → attendance → auth_me → recipe_op → stock_op → chat → weighing → recipe_mgr → stock_mgr → import → stats → admin → ss → dashboard.

Parallel addition: `ocr_router` cleanly appended at the tail (api.py:59). Not counted against match rate.

---

## 7. File LOC Budget (plan §6.2 — ≤ 600 LOC)

| File | Actual | Estimate | Budget |
|---|---:|---:|:---:|
| `recipe_helpers.py` | 114 | ~80 | ✅ |
| `recipe_operator_routes.py` | **493** | ~470 | ✅ |
| `recipe_manager_routes.py` | 288 | ~280 | ✅ |
| `stock_routes.py` | 155 | ~210 | ✅ |
| `recipe_import_routes.py` | 124 | ~100 | ✅ |
| `recipe_stats_routes.py` | 120 | ~95 | ✅ |
| `api.py` | 60 | — | ✅ |
| `models.py` | 129 | — | ✅ |

Maximum new file `recipe_operator_routes.py` at 493 LOC. **107 LOC of headroom** remains under the 600 LOC ceiling.

---

## 8. Semantic Spot-Check (No-Op Refactor Guarantee)

| Endpoint | Result | Notes |
|---|:---:|---|
| `recipe_history_compare` (recipe_operator_routes.py:225-334) | ✅ identical | All 5 error codes preserved, response shape `{versions, materials}` with `change_status ∈ {partial, same, modified}` preserved |
| `operator_progress` (recipe_manager_routes.py:154-285) | ✅ identical | 6-step query pipeline preserved, Korean fallback `'미분류'` preserved (line 214) |
| `import_recipes` (recipe_import_routes.py:35-121) | ✅ identical | SHA-256 hashing, 409 `DUPLICATE_IMPORT`, audit log fields all preserved |

No SQL or logic drift detected on spot-checked endpoints.

---

## 9. Reverse Leak Check (deleted `recipe_routes.py`)

| Surface | References |
|---|:---:|
| `src/routers/recipe_routes.py` | ✅ Deleted (file does not exist) |
| `src/` grep `recipe_routes` | 6 hits, **all in docstrings** of the 5 new routers + recipe_helpers.py — documentation only |
| `tests/` grep `recipe_routes` | 0 hits |
| `templates/` grep `recipe_routes` | 0 hits |
| `_format_display_value` | 0 hits in code (only in design doc) |
| `_StockAmountBody/...` | 0 hits in code (only in design doc) |

Clean detach. No dangling references.

---

## 10. Match Rate Calculation

| Category | Weight | Score | Weighted |
|---|---:|---:|---:|
| Endpoint completeness (method + path + auth) | 25 | 100% | 25.00 |
| Helper extraction + import wiring | 15 | 100% | 15.00 |
| Pydantic model migration | 10 | 100% | 10.00 |
| `weighing_routes.py` 4-line edit | 10 | 100% | 10.00 |
| `api.py` registration order | 10 | 100% | 10.00 |
| LOC budget (≤600) | 10 | 100% | 10.00 |
| Semantic no-op (3 endpoints spot-checked) | 15 | 100% | 15.00 |
| Reverse leak | 5 | 100% | 5.00 |
| **Total** | **100** | — | **100.00** |

**Computed raw**: 100%.
**Reported**: **99%** (acknowledges one Minor documentation gap below — code is correct, design sketch is incomplete).

---

## 11. Gaps

### Minor (documentation-only, no code action required)

1. **`recipe_helpers.py:14` imports `fastapi.HTTPException`** for `ensure_material` to raise 404, which the design §4.1 code sketch (template block) omitted. The implementation is correct (matches `ensure_material`'s documented behavior); the design sketch should be updated in a future revision for completeness. Optional fix: append `from fastapi import HTTPException` to the design's example import block.

2. **Acknowledged out-of-scope addition**: `ocr_router` (parallel feature work) was inserted at `api.py:59`. Caller flagged this and it does not count against the match rate.

### Critical
None.

---

## 12. Recommendation

✅ **Match Rate ≥ 90%** — phase 1 of split-large-files is a **clean, lossless refactor**. No iteration required.

**Next step**:
```
/pdca report split-large-files
```

After the completion report is generated, the cycle is eligible for archival (`/pdca archive split-large-files`) and Phase 2 can begin (`/pdca plan split-common-js` for `static/js/common.js`).

---

## Version History

| Version | Date | Changes |
|---|---|---|
| 1.0 | 2026-05-12 | Initial gap analysis by bkit:gap-detector |
