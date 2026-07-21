(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeEditLoader = function (ctx) {
    const { dom, state } = ctx;

    function setRevisionBanner(detail, sourceLabel) {
      if (!dom.revisionBanner) return;
      const product = IRMS.escapeHtml(detail.product_name || `#${detail.id}`);
      const source = sourceLabel ? ` · ${IRMS.escapeHtml(sourceLabel)}` : "";
      dom.revisionBanner.innerHTML =
        `<b>수정 등록 중</b><span>${product} #${detail.id}${source}</span>` +
        '<span class="muted">검증 후 등록하면 기존 레시피의 새 버전으로 연결됩니다.</span>';
      dom.revisionBanner.hidden = false;
    }

    function loadRowsIntoSpreadsheet(tsvRows, tsvText) {
      // 세로 BOM 편집기(item-code P5): TSV → 편집기 역직렬화. 다중 반제품 등
      // 편집기로 담을 수 없는 형태는 loadFromTsvRows 가 raw 텍스트 모드로 폴백한다.
      if (typeof ctx.spreadsheet.loadFromTsvRows === "function") {
        if (!ctx.spreadsheet.loadFromTsvRows(tsvRows, tsvText) && dom.rawInput) {
          dom.rawInput.value = tsvText;
          ctx.spreadsheet.setRawInputMode(true);
        }
        return;
      }
      // (안전망) 구 편집기 인터페이스 — raw 텍스트로 폴백
      if (dom.rawInput) {
        dom.rawInput.value = tsvText;
        ctx.spreadsheet.setRawInputMode(true);
      }
      state.suppressDirtyTracking = false;
    }

    async function loadRecipeForEdit(recipeId, sourceLabel) {
      const detail = await IRMS.getRecipeDetail(recipeId);
      const tsvRows = detail.tsv.split("\n").map((row) => row.split("\t"));

      ctx.switchToImportTab();
      loadRowsIntoSpreadsheet(tsvRows, detail.tsv);

      state.pendingRevisionOf = recipeId;
      state.currentPreview = null;
      state.previewIsStale = false;
      state.confirmedRawText = "";
      // 기준 배합량 프리필 — 수정 등록 시 저장된 값(최대 3개)이 그대로 승계되도록.
      const baseEl = document.getElementById("register-base-total");
      if (baseEl) {
        baseEl.value = detail.base_totals
          ? String(detail.base_totals).split(",").map((t) => t.trim()).join(", ")
          : (detail.base_total != null ? String(detail.base_total) : "");
      }
      // 기준 자재 후보를 불러온 레시피의 자재로 채우고, 기존 기준 자재를 미리 선택.
      // 수정 등록은 검증(미리보기)을 거치지 않고 시트를 곧바로 채우므로 여기서 후보를 구성한다.
      const anchorSel = document.getElementById("imp-anchor");
      if (anchorSel) {
        const itemNames = (detail.items || [])
          .map((it) => it.material_name)
          .filter((n) => !!n);
        const seen = new Set();
        const uniq = [];
        for (const n of itemNames) {
          if (!seen.has(n)) { seen.add(n); uniq.push(n); }
        }
        anchorSel.innerHTML =
          '<option value="">없음</option>' +
          uniq.map((n) => `<option value="${IRMS.escapeHtml(n)}">${IRMS.escapeHtml(n)}</option>`).join("");
        anchorSel.value = detail.anchor_material_name || "";
      }
      // 허용 편차 프리필 — 수정 등록 시 부모의 tolerance_g 를 미리 채운다.
      // (서버는 tolerance_g 미지정 시 부모 값을 자동 승계하므로, 빈 칸으로 두면 기본값
      // 또는 부모 승계로 처리된다. 사용자가 명시한 값이 있으면 그것을 우선.)
      const toleranceEl = document.getElementById("imp-tolerance");
      if (toleranceEl) {
        toleranceEl.value = detail.tolerance_g != null ? String(detail.tolerance_g) : "";
      }
      // 품목코드 프리필(code-edit-relocate §2) — 수정 등록 시 부모의 product_code 를
      // 미리 채운다. 레시피 상세 응답에 product_code 가 있음(mapRecipe.productCode 와
      // 동일 필드). 빈 칸으로 두면 서버가 부모 값을 자동 승계한다.
      const productCodeEl = document.getElementById("imp-product-code");
      if (productCodeEl) {
        productCodeEl.value = detail.product_code || "";
      }
      // 반응기 프리필(reactor-ownership) — 수정 등록 시 부모의 use_reactor 를 미리 채운다.
      // 서버는 use_reactor 미지정 시 부모 값을 자동 승계하므로, 체크를 그대로 두면
      // 부모 값이 유지된다(tolerance_g/product_code 승계와 동일 구조).
      const useReactorEl = document.getElementById("imp-use-reactor");
      if (useReactorEl) {
        useReactorEl.checked = !!detail.use_reactor;
      }
      // 파생 프리필 — 반응기와 동일 구조(미지정 시 부모 is_derived 승계).
      const isDerivedEl = document.getElementById("imp-is-derived");
      if (isDerivedEl) {
        isDerivedEl.checked = !!detail.is_derived;
      }
      // 1차 레시피 프리필 — 목록을 다시 채우며 부모의 stage1_recipe_id 를 선택.
      if (ctx.importValidate.populateStage1Options) {
        ctx.importValidate.populateStage1Options(detail.stage1_recipe_id != null ? detail.stage1_recipe_id : "");
      }
      ctx.importValidate.renderValidationMeta({ rows: [], warnings: [], errors: [] });
      ctx.importValidate.renderIssues([], dom.errorList, "오류 없음");
      ctx.importValidate.renderIssues([], dom.warningList, "확인 사항 없음");
      ctx.importValidate.syncRegisterState();
      setRevisionBanner(detail, sourceLabel);

      IRMS.notify(`레시피 #${recipeId}을 수정 등록 탭으로 불러왔습니다.`, "info");
    }

    function clearRevisionBanner() {
      if (dom.revisionBanner) {
        dom.revisionBanner.hidden = true;
        dom.revisionBanner.innerHTML = "";
      }
    }

    return {
      loadRecipeForEdit,
      clearRevisionBanner,
    };
  };
})();
