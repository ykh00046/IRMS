/**
 * import-validate module — Import tab: validate / register / clear flow.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createImportValidate(ctx)
 * Returns: { syncRegisterState, markPreviewStale, renderIssues,
 *            renderValidationMeta, handlePreview, handleRegister, handleClear }
 *
 * ctx dependencies:
 *   dom:   previewBtn, registerBtn, previewMeta, errorList, warningList
 *   state: currentPreview, confirmedRawText, previewIsStale,
 *          pendingRevisionOf, suppressDirtyTracking, sheet
 *   other: ctx.spreadsheet.getSpreadsheetDataAsText / .initSpreadsheet
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createImportValidate = function (ctx) {
    const { dom, state } = ctx;

    // 품목코드 마스터 제안(code-edit-relocate §2) — item-codes.js 의 제안 패턴 재사용.
    // GET /api/item-codes/master (kind=product), 300ms 디바운스, 클릭 시 코드 채움.
    // 입력칸·제안 목록은 management.html 의 #imp-product-code · #imp-product-code-suggest.
    const productCodeInput = document.getElementById("imp-product-code");
    const productCodeSuggest = document.getElementById("imp-product-code-suggest");

    async function loadProductCodeSuggestions(q) {
      if (!productCodeSuggest || !productCodeInput) return;
      try {
        const data = await IRMS._core.request("/item-codes/master", {
          query: { q, kind: "product" },
        });
        const items = data.items || [];
        if (!items.length) {
          productCodeSuggest.hidden = true;
          productCodeSuggest.innerHTML = "";
          return;
        }
        productCodeSuggest.innerHTML = items
          .map(
            (it) =>
              `<li class="code-suggest-item" data-code="${IRMS.escapeHtml(it.code)}">${IRMS.escapeHtml(it.code)} — ${IRMS.escapeHtml(it.name)}</li>`,
          )
          .join("");
        productCodeSuggest.hidden = false;
        productCodeSuggest.querySelectorAll(".code-suggest-item").forEach((li) => {
          li.addEventListener("mousedown", (ev) => {
            ev.preventDefault(); // input blur 보존
            productCodeInput.value = li.dataset.code;
            productCodeSuggest.hidden = true;
            productCodeInput.focus();
          });
        });
      } catch (_err) {
        productCodeSuggest.hidden = true;
      }
    }

    const debouncedProductSuggest = IRMS.debounce(loadProductCodeSuggestions, 300);

    if (productCodeInput) {
      productCodeInput.addEventListener("input", () => {
        const q = productCodeInput.value.trim();
        if (q.length < 1) {
          if (productCodeSuggest) productCodeSuggest.hidden = true;
          return;
        }
        debouncedProductSuggest(q);
      });
      // 포커스 벗어나면 제안 목록 닫기(blur 이후 클릭이 먼저 처리되도록 약간 지연).
      productCodeInput.addEventListener("blur", () => {
        setTimeout(() => {
          if (productCodeSuggest) productCodeSuggest.hidden = true;
        }, 150);
      });
    }

    function syncRegisterState() {
      const canRegister =
        Boolean(state.currentPreview) &&
        !state.previewIsStale &&
        (state.currentPreview.errors || []).length === 0 &&
        state.currentPreview.rows.length > 0 &&
        state.confirmedRawText.trim().length > 0;
      dom.registerBtn.disabled = !canRegister;
    }

    function markPreviewStale() {
      if (state.suppressDirtyTracking || !state.currentPreview || state.previewIsStale) {
        return;
      }
      state.previewIsStale = true;
      syncRegisterState();
      renderValidationMeta(state.currentPreview);
      IRMS.notify("시트가 수정되어 검증이 무효화되었습니다. 다시 검증하세요.", "warn");
    }

    function renderIssues(list, target, emptyText) {
      if (!list || !list.length) {
        target.innerHTML = `<li class="muted">${emptyText}</li>`;
        return;
      }
      target.innerHTML = list
        .slice(0, 12)
        .map(
          (item) =>
            `<li>L${item.level} · ${IRMS.escapeHtml(item.message)}${item.row ? ` (행 ${item.row})` : ""}</li>`,
        )
        .join("");
    }

    function renderValidationMeta(result) {
      const rows = result?.rows || [];
      const badges = [
        `<span class="meta-badge meta-ok">등록 ${rows.length}건</span>`,
        `<span class="meta-badge meta-warn">확인 ${(result?.warnings || []).length}건</span>`,
        `<span class="meta-badge meta-error">오류 ${(result?.errors || []).length}건</span>`,
      ];
      if (state.previewIsStale) {
        badges.push('<span class="meta-badge meta-warn">재검증 필요</span>');
      }
      dom.previewMeta.innerHTML = badges.join("");
    }

    // 기준 자재 후보를 검증 미리보기의 자재 이름으로 채운다. 없음(빈 값) 옵션은 항상 첫 줄.
    // 이전 선택값(있으면) 유지 — 시트 수정·재검증 사이에 선택이 풀리지 않게.
    function populateAnchorOptions(preserveName) {
      const sel = document.getElementById("imp-anchor");
      if (!sel) return;
      const names = [];
      const seen = new Set();
      for (const row of (state.currentPreview?.rows || [])) {
        for (const item of (row.items || [])) {
          const n = item.materialName;
          if (n && !seen.has(n)) {
            seen.add(n);
            names.push(n);
          }
        }
      }
      const wanted = preserveName || sel.value || "";
      sel.innerHTML =
        '<option value="">없음</option>' +
        names.map((n) => `<option value="${IRMS.escapeHtml(n)}">${IRMS.escapeHtml(n)}</option>`).join("");
      if (wanted && names.includes(wanted)) {
        sel.value = wanted;
      }
    }

    async function handlePreview() {
      const raw = ctx.spreadsheet.getSpreadsheetDataAsText();

      if (!raw) {
        IRMS.notify("데이터를 입력하거나 붙여넣은 후 검증하세요.", "warn");
        return;
      }

      IRMS.btnLoading(dom.previewBtn, true);
      try {
        const result = await IRMS.previewImport(raw);
        state.currentPreview = result;
        state.confirmedRawText = raw;
        state.previewIsStale = false;
        renderValidationMeta(result);
        renderIssues(result.errors, dom.errorList, "오류 없음");
        renderIssues(result.warnings, dom.warningList, "확인 사항 없음");
        // 검증 결과(자재 이름)로 기준 자재 후보를 다시 채운다 — 시트가 바뀌면 후보도 바뀐다.
        populateAnchorOptions(null);
        syncRegisterState();

        if (!result.errors.length && result.rows.length > 0) {
          IRMS.notify(`검증 완료: ${result.rows.length}건 등록 가능`, "success");
        }
      } catch (error) {
        IRMS.notify(`검증 실패: ${error.message}`, "error");
      } finally {
        IRMS.btnLoading(dom.previewBtn, false);
      }
    }

    async function handleRegister() {
      if (
        !state.currentPreview ||
        state.previewIsStale ||
        state.currentPreview.errors.length > 0 ||
        state.currentPreview.rows.length === 0 ||
        !state.confirmedRawText.trim()
      ) {
        if (state.previewIsStale) {
          IRMS.notify("검증본이 무효화되었습니다. 다시 검증 후 등록하세요.", "warn");
        }
        return;
      }

      IRMS.btnLoading(dom.registerBtn, true);
      try {
        const baseEl = document.getElementById("register-base-total");
        let baseTotals = null;
        if (baseEl && baseEl.value.trim()) {
          baseTotals = baseEl.value.split(",").map((t) => Number(t.trim())).filter((v) => v > 0);
          if (!baseTotals.length || baseTotals.length > 3
              || baseEl.value.split(",").some((t) => t.trim() && !(Number(t.trim()) > 0))) {
            IRMS.notify("기준 배합량은 양수 숫자를 쉼표로 최대 3개까지 입력하세요. (예: 3924.38, 2000)", "error");
            return;
          }
        }
        // 기준 자재: 선택한 이름이 있으면 본문에 포함. api-recipes.importRecipes 가
        // anchor_material 을 받지 않으므로, 기준 자재가 있을 때는 직접 POST 한다
        // (CSRF 토큰은 core.js 의 request 패턴과 동일하게 x-csrftoken 헤더로 직접 부착).
        const anchorEl = document.getElementById("imp-anchor");
        const anchorMaterial = anchorEl ? anchorEl.value.trim() : "";
        // 허용 편차(tolerance_g): 값이 있을 때만 본문에 포함. 빈 값은 서버 기본값(0.05)
        // 또는 수정 등록 시 부모 승계. 레시피 편차가 있든 없든 기준 자재 POST 경로와
        // 동일한 본문을 쓴다(anchor 가 없는 경로로 분기되더라도 tolerance_g 만 있으면
        // 직접 POST 경로를 탄다 — 검증 일관성).
        const toleranceEl = document.getElementById("imp-tolerance");
        const toleranceRaw = toleranceEl ? toleranceEl.value.trim() : "";
        const toleranceG = toleranceRaw ? Number(toleranceRaw) : null;
        const hasTolerance = toleranceG != null && Number.isFinite(toleranceG) && toleranceG > 0;
        // 품목코드(code-edit-relocate §2): 값이 있을 때만 본문에 product_code 포함(strip).
        // 빈 값은 미포함 — 기존 자동 인식/승계 동작 유지.
        const productCodeEl = document.getElementById("imp-product-code");
        const productCode = productCodeEl ? productCodeEl.value.trim() : "";
        const hasProductCode = productCode.length > 0;
        // 반응기 사용(reactor-ownership): 체크 시 본문에 use_reactor=true 포함. 체크 해제는
        // 명시적 false 이므로 역시 직접 POST 경로로 보낸다(ImportRequest.use_reactor).
        const useReactorEl = document.getElementById("imp-use-reactor");
        const useReactor = useReactorEl ? !!useReactorEl.checked : false;
        // 파생(derived): 체크 시 is_derived=true 를 명시 전송(반응기와 독립). ImportRequest.is_derived.
        const isDerivedEl = document.getElementById("imp-is-derived");
        const isDerived = isDerivedEl ? !!isDerivedEl.checked : false;
        // 1차 레시피 연계(stage1): 고르면 stage1_recipe_id 전송(현황 가족 묶음 + 재료 인식).
        const stage1El = document.getElementById("imp-stage1");
        const stage1RecipeId = stage1El && stage1El.value ? Number(stage1El.value) : null;
        let result;
        if (anchorMaterial || hasTolerance || hasProductCode || useReactor || isDerived || stage1RecipeId) {
          result = await importWithAnchor(
            baseTotals,
            anchorMaterial,
            hasTolerance ? toleranceG : null,
            hasProductCode ? productCode : null,
            useReactor,
            isDerived,
            stage1RecipeId,
          );
        } else {
          result = await IRMS.importRecipes(
            state.confirmedRawText, "레시피 관리",
            state.pendingRevisionOf, baseTotals,
          );
        }
        IRMS.notify(
          `${result.created_count}건 레시피를 등록했습니다.`,
          "success",
        );

        // 등록으로 새로 자동 등록된 자재(예: 2차 BOM 에서 인식된 1차 반제품)를 BOM 코드
        // 배지·자재명 datalist 에 즉시 반영한다 — 전체 새로고침 없이 품목코드가 바로 뜨도록.
        // (handleClear 가 initSpreadsheet(state.materials) 를 호출하므로 그 전에 최신화한다.)
        if (ctx.refreshMaterials) {
          try { await ctx.refreshMaterials(); } catch (_e) { /* 조용히 무시 — 등록은 이미 성공 */ }
        }

        handleClear();
      } catch (error) {
        IRMS.notify(`등록 실패: ${error.message}`, "error");
      } finally {
        IRMS.btnLoading(dom.registerBtn, false);
      }
    }

    // 기준 자재·허용 편차·품목코드·반응기를 포함해 임포트 — core.js 의 request 와 동일한 CSRF 부착
    // 패턴으로 /api/recipes/import 에 직접 POST 한다.
    async function importWithAnchor(baseTotals, anchorMaterial, toleranceG, productCode, useReactor, isDerived, stage1RecipeId) {
      const body = {
        raw_text: state.confirmedRawText,
        created_by: "레시피 관리",
      };
      if (state.pendingRevisionOf != null) {
        body.revision_of = state.pendingRevisionOf;
      }
      if (Array.isArray(baseTotals) && baseTotals.length) {
        body.base_totals = baseTotals.slice(0, 3);
      }
      if (anchorMaterial) {
        body.anchor_material = anchorMaterial;
      }
      // tolerance_g: 숫자 값만 전송(null 은 미지정과 동일 — 보내지 않음).
      if (toleranceG != null && Number.isFinite(Number(toleranceG)) && Number(toleranceG) > 0) {
        body.tolerance_g = Number(toleranceG);
      }
      // product_code(code-edit-relocate §2): 값이 있을 때만 전송. 빈 값은 미지정과
      // 동일 — 기존 자동 인식/승계 동작 유지.
      if (productCode) {
        body.product_code = productCode;
      }
      // use_reactor(reactor-ownership): 항상 명시적으로 전송(체크=true, 해제=false).
      body.use_reactor = !!useReactor;
      // is_derived(파생): 항상 명시적으로 전송(체크=true, 해제=false). 반응기와 독립.
      body.is_derived = !!isDerived;
      // stage1_recipe_id(1차 연계): 고른 값이 있을 때만 전송(없음=미지정, 수정 등록 시 부모 승계).
      if (stage1RecipeId) {
        body.stage1_recipe_id = stage1RecipeId;
      }
      const headers = { "Content-Type": "application/json" };
      const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
      if (token) headers["x-csrftoken"] = token;
      const resp = await fetch("/api/recipes/import", {
        method: "POST",
        credentials: "same-origin",
        headers,
        body: JSON.stringify(body),
      });
      if (!resp.ok) {
        let detail = "";
        try {
          const payload = await resp.json();
          const d = payload && payload.detail;
          detail = d && typeof d === "object" && d.message ? d.message
            : (d !== undefined ? String(d) : `Request failed (${resp.status})`);
        } catch (_e) {
          detail = await resp.text().catch(() => `Request failed (${resp.status})`);
        }
        throw new Error(String(detail || `Request failed (${resp.status})`));
      }
      return resp.json();
    }

    function handleClear() {
      state.confirmedRawText = "";
      state.previewIsStale = false;
      state.pendingRevisionOf = null;
      // 수정 등록에서 프리필된 기준 배합량이 다음 신규 등록에 새어들지 않게 비움.
      const baseTotalEl = document.getElementById("register-base-total");
      if (baseTotalEl) baseTotalEl.value = "";
      // 기준 자재 후보·선택도 초기화 — 다음 등록에 이전 자재가 남지 않게.
      const anchorEl = document.getElementById("imp-anchor");
      if (anchorEl) {
        anchorEl.innerHTML = '<option value="">없음</option>';
      }
      // 허용 편차 입력도 초기화 — 수정 등록 프리필 값이 다음 신규 등록에 남지 않게.
      const toleranceEl = document.getElementById("imp-tolerance");
      if (toleranceEl) {
        toleranceEl.value = "";
      }
      // 품목코드 입력도 초기화 — 수정 등록 프리필 값이 다음 신규 등록에 남지 않게.
      const productCodeEl = document.getElementById("imp-product-code");
      if (productCodeEl) {
        productCodeEl.value = "";
      }
      // 반응기 체크도 초기화 — 수정 등록 프리필 값이 다음 신규 등록에 새어들지 않게.
      const useReactorEl = document.getElementById("imp-use-reactor");
      if (useReactorEl) {
        useReactorEl.checked = false;
      }
      // 파생 체크도 초기화 — 반응기와 동일.
      const isDerivedEl = document.getElementById("imp-is-derived");
      if (isDerivedEl) {
        isDerivedEl.checked = false;
      }
      // 1차 레시피 선택도 초기화(수정 프리필 값이 신규 등록에 새어들지 않게).
      const stage1El = document.getElementById("imp-stage1");
      if (stage1El) {
        stage1El.value = "";
      }
      if (productCodeSuggest) {
        productCodeSuggest.hidden = true;
        productCodeSuggest.innerHTML = "";
      }
      if (ctx.recipeEditLoader) {
        ctx.recipeEditLoader.clearRevisionBanner();
      }
      if (state.sheet) {
        ctx.spreadsheet.initSpreadsheet(state.materials);
      } else if (dom.rawInput) {
        dom.rawInput.value = "";
      }
      state.currentPreview = null;
      state.previewIsStale = false;
      renderValidationMeta({ rows: [], warnings: [], errors: [] });
      renderIssues([], dom.errorList, "오류 없음");
      renderIssues([], dom.warningList, "확인 사항 없음");
      syncRegisterState();
    }

    // 1차 레시피 드롭다운 채우기 — 완료 레시피 목록. selectedId 지정 시 그 값 선택.
    // 조회 실패는 조용히(없음만). 수정 프리필에서 다시 호출해 값 반영.
    async function populateStage1Options(selectedId) {
      const sel = document.getElementById("imp-stage1");
      if (!sel) return;
      let recipes = [];
      try { recipes = await IRMS.getRecipes({ status: "completed" }); } catch (_e) { recipes = []; }
      const cur = selectedId != null ? String(selectedId) : sel.value;
      sel.innerHTML = '<option value="">없음</option>'
        + recipes.map((r) => `<option value="${r.id}"${String(r.id) === cur ? " selected" : ""}>${IRMS.escapeHtml(r.productName)}</option>`).join("");
    }
    populateStage1Options();  // 최초 1회 채움

    return {
      syncRegisterState,
      markPreviewStale,
      renderIssues,
      renderValidationMeta,
      populateAnchorOptions,
      populateStage1Options,
      handlePreview,
      handleRegister,
      handleClear,
    };
  };
})();
