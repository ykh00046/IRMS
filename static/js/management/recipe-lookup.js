/**
 * recipe-lookup module — Lookup tab: product recipe pivot, selection,
 * copy, and clone-to-import.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createRecipeLookup(ctx)
 * Returns: { loadProducts, setLookupSelection, handleLookup,
 *            copyToClipboard, handleLookupCopy, handleLookupClone }
 *
 * ctx dependencies (see design §4.4):
 *   dom:   lookup* refs, spreadsheetContainer, rawInput, errorList, warningList
 *   state: selectedRecipeId, pendingRevisionOf, currentPreview,
 *          previewIsStale, confirmedRawText, suppressDirtyTracking
 *   other: ctx.spreadsheet.*, ctx.importValidate.*, ctx.onDirty,
 *          ctx.switchToImportTab
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeLookup = function (ctx) {
    const { dom, state } = ctx;

    function dhrMode() {
      return !!(dom.lookupDhr && dom.lookupDhr.checked);
    }

    async function loadProducts() {
      try {
        const items = await IRMS.getProducts(dhrMode());
        if (dom.productList) {
          dom.productList.innerHTML = items
            .map((name) => `<option value="${IRMS.escapeHtml(name)}">`)
            .join("");
        }
      } catch (error) {
        IRMS.notify(`제품 목록 로드 실패: ${error.message}`, "error");
      }
    }

    function setLookupSelection(recipeId) {
      const canManage = !!ctx.canManage;
      state.selectedRecipeId = recipeId;
      const rows = dom.lookupResult.querySelectorAll("tbody tr");
      rows.forEach((row) => {
        row.classList.toggle("selected", Number(row.dataset.recipeId) === recipeId);
      });
      if (dom.lookupSelectedLabel) {
        dom.lookupSelectedLabel.textContent = recipeId ? `선택: #${recipeId}` : "선택: 없음";
      }
      if (dom.lookupCopyBtn) dom.lookupCopyBtn.disabled = !recipeId;
      if (dom.lookupCloneBtn) dom.lookupCloneBtn.disabled = !canManage || !recipeId;
      if (dom.lookupHistoryBtn) dom.lookupHistoryBtn.disabled = !recipeId;
      if (dom.lookupDhrBtn) {
        dom.lookupDhrBtn.disabled = !canManage || !recipeId;
        dom.lookupDhrBtn.textContent = dhrMode() ? "DHR 전용 해제" : "DHR 전용 지정";
      }
      if (dom.lookupActions) dom.lookupActions.hidden = !recipeId;
      // 기준 자재 패널 — 선택한 레시피의 현재 기준 자재 표시 + (책임자) 변경.
      if (dom.lookupAnchor) {
        if (recipeId) {
          renderAnchorPanel(recipeId);
        } else {
          dom.lookupAnchor.hidden = true;
          dom.lookupAnchor.innerHTML = "";
        }
      }
    }

    // 기준 자재 패널 렌더 — 선택한 레시피 상세를 가져와 현재 기준 자재를 표시하고,
    // 책임자면 자재 select + 저장 버튼으로 PUT /api/recipes/{id}/anchor 를 호출한다.
    // 두 번째 줄로 허용 편차(tolerance_g) 표시 + (책임자) 숫자 입력·저장 버튼을
    // 함께 그린다(PUT /api/recipes/{id}/tolerance).
    async function renderAnchorPanel(recipeId) {
      const wrap = dom.lookupAnchor;
      if (!wrap) return;
      try {
        const detail = await IRMS.getRecipeDetail(recipeId);
        const canManage = !!ctx.canManage;
        const currentName = detail.anchor_material_name || "";
        const itemNames = (detail.items || [])
          .map((it) => it.material_name)
          .filter((n) => !!n);
        const seen = new Set();
        const uniq = [];
        for (const n of itemNames) {
          if (!seen.has(n)) { seen.add(n); uniq.push(n); }
        }
        const currentText = currentName
          ? IRMS.escapeHtml(currentName)
          : '<span class="muted">없음</span>';
        const options =
          '<option value="">없음</option>' +
          uniq
            .map((n) => `<option value="${IRMS.escapeHtml(n)}"${n === currentName ? " selected" : ""}>${IRMS.escapeHtml(n)}</option>`)
            .join("");
        const editor = canManage
          ? `<select id="lookup-anchor-select" class="input">${options}</select>` +
            `<button id="lookup-anchor-save" class="btn" type="button">저장</button>`
          : "";
        // 허용 편차 줄 — 현재 값 표시 + (책임자) 숫자 입력·저장. 미지정 시 기본 0.05g.
        const tolCurrent = detail.tolerance_g != null ? Number(detail.tolerance_g) : null;
        const tolCurrentText = tolCurrent != null && Number.isFinite(tolCurrent)
          ? `±${IRMS.escapeHtml(String(tolCurrent))} g`
          : '<span class="muted">기본 ±0.05 g</span>';
        const tolEditor = canManage
          ? `<input id="lookup-tolerance-input" class="input" type="number" step="0.01" min="0" `
            + `placeholder="선택 · 비우면 기본 0.05" value="${tolCurrent != null && Number.isFinite(tolCurrent) ? IRMS.escapeHtml(String(tolCurrent)) : ""}" />`
            + `<button id="lookup-tolerance-save" class="btn" type="button">저장</button>`
          : "";
        wrap.innerHTML =
          `<label class="filter-label" for="lookup-anchor-select">기준 자재</label>` +
          `<span class="muted">현재:</span><span id="lookup-anchor-current">${currentText}</span>` +
          editor +
          `<label class="filter-label" for="lookup-tolerance-input">허용 편차</label>` +
          `<span class="muted">현재:</span><span id="lookup-tolerance-current">${tolCurrentText}</span>` +
          tolEditor;
        wrap.hidden = false;
        if (canManage) {
          const saveBtn = document.getElementById("lookup-anchor-save");
          if (saveBtn) saveBtn.addEventListener("click", () => handleSaveAnchor(recipeId));
          const tolSaveBtn = document.getElementById("lookup-tolerance-save");
          if (tolSaveBtn) tolSaveBtn.addEventListener("click", () => handleSaveTolerance(recipeId));
        }
      } catch (error) {
        wrap.hidden = false;
        wrap.innerHTML = `<span class="muted">기준 자재 정보를 불러오지 못했습니다: ${IRMS.escapeHtml(error.message || String(error))}</span>`;
      }
    }

    // 기준 자재 저장 — 자재 이름을 material_id 로 변환해 PUT /api/recipes/{id}/anchor.
    // core.js request 와 동일하게 x-csrftoken 헤더를 직접 부착한다(IRMS.request 가 이 화면
    // 컨트롤러에 노출되지 않으므로 직접 fetch).
    async function handleSaveAnchor(recipeId) {
      const sel = document.getElementById("lookup-anchor-select");
      const saveBtn = document.getElementById("lookup-anchor-save");
      if (!sel) return;
      let materialId = null;
      const chosenName = sel.value.trim();
      if (chosenName) {
        try {
          const detail = await IRMS.getRecipeDetail(recipeId);
          const match = (detail.items || []).find((it) => it.material_name === chosenName);
          if (!match || match.material_id == null) {
            IRMS.notify("선택한 자재의 식별자를 찾을 수 없습니다.", "error");
            return;
          }
          materialId = Number(match.material_id);
        } catch (error) {
          IRMS.notify(`기준 자재 저장 실패: ${error.message}`, "error");
          return;
        }
      }
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/anchor`, {
          method: "PUT",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ material_id: materialId }),
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
        await resp.json();
        // 성공 — 현재 값 표시 갱신
        const cur = document.getElementById("lookup-anchor-current");
        if (cur) {
          cur.innerHTML = chosenName ? IRMS.escapeHtml(chosenName) : '<span class="muted">없음</span>';
        }
        IRMS.notify(
          chosenName ? `기준 자재를 '${chosenName}'(으)로 지정했습니다.` : "기준 자재를 해제했습니다.",
          "success",
        );
      } catch (error) {
        IRMS.notify(`기준 자재 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
      }
    }

    // 허용 편차 저장 — 빈 입력은 null(기본값으로 되돌리기), 숫자면 PUT /api/recipes/{id}/tolerance.
    // 기준 자재 저장(handleSaveAnchor) 과 동일하게 x-csrftoken 헤더를 직접 부착한다
    // (IRMS.request 가 이 화면 컨트롤러에 노출되지 않으므로 직접 fetch).
    async function handleSaveTolerance(recipeId) {
      const input = document.getElementById("lookup-tolerance-input");
      const saveBtn = document.getElementById("lookup-tolerance-save");
      if (!input) return;
      const raw = (input.value || "").trim();
      let toleranceG = null;
      let label;
      if (raw !== "") {
        const v = Number(raw);
        if (!Number.isFinite(v) || !(v > 0)) {
          IRMS.notify("허용 편차는 0 초과 숫자여야 합니다. (비우면 기본 0.05g)", "error");
          return;
        }
        toleranceG = v;
        label = `±${v} g`;
      } else {
        label = '<span class="muted">기본 ±0.05 g</span>';
      }
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/tolerance`, {
          method: "PUT",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ tolerance_g: toleranceG }),
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
        await resp.json();
        // 성공 — 현재 값 표시 갱신
        const cur = document.getElementById("lookup-tolerance-current");
        if (cur) cur.innerHTML = label;
        IRMS.notify(
          toleranceG != null
            ? `허용 편차를 ±${toleranceG} g 으로 지정했습니다.`
            : "허용 편차를 기본값(±0.05 g)으로 되돌렸습니다.",
          "success",
        );
      } catch (error) {
        IRMS.notify(`허용 편차 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
      }
    }

    async function handleSetDhr() {
      if (!state.selectedRecipeId) return;
      const target = !dhrMode(); // 일반 보기→지정(true), DHR 보기→해제(false)
      try {
        await IRMS.setRecipeDhr(state.selectedRecipeId, target);
        IRMS.notify(target ? "DHR 전용으로 지정했습니다." : "DHR 전용을 해제했습니다.", "success");
        await loadProducts();
        await handleLookup(); // 현재 목록 갱신(이동된 레시피는 빠짐)
      } catch (error) {
        IRMS.notify(`DHR 지정 실패: ${error.message}`, "error");
      }
    }

    async function handleDhrModeChange() {
      await loadProducts();
      if (dom.lookupProduct) dom.lookupProduct.value = "";
      if (dom.lookupResult) {
        dom.lookupResult.innerHTML =
          '<p class="empty-state">반제품명을 선택하면 버전별 자재 구성이 표시됩니다.</p>';
      }
      setLookupSelection(null);
    }

    async function handleLookup() {
      const productName = dom.lookupProduct ? dom.lookupProduct.value.trim() : "";
      if (!productName) {
        IRMS.notify("반제품명을 입력해주세요.", "warn");
        return;
      }

      IRMS.btnLoading(dom.lookupBtn, true);
      try {
        const data = await IRMS.getRecipesByProduct(productName, undefined, dhrMode());
        const recipes = data.items || [];

        if (!recipes.length) {
          dom.lookupResult.innerHTML = '<p class="empty-state">해당 반제품의 레시피가 없습니다.</p>';
          setLookupSelection(null);
          return;
        }

        // Collect all unique material names across recipes for pivot columns
        const allMaterials = [];
        const materialSet = new Set();
        for (const recipe of recipes) {
          for (const item of recipe.items || []) {
            if (!materialSet.has(item.material_name)) {
              materialSet.add(item.material_name);
              allMaterials.push(item.material_name);
            }
          }
        }

        // Build pivot table
        const headerCells = [
          "<th>ID</th>",
          ...allMaterials.map((m) => `<th>${IRMS.escapeHtml(m)}</th>`),
          "<th>항목수</th>",
          "<th>상태</th>",
          "<th>등록일</th>",
          "<th>등록자</th>",
        ].join("");

        const bodyRows = recipes
          .map((recipe) => {
            const valueMap = {};
            for (const item of recipe.items || []) {
              valueMap[item.material_name] = item.value;
            }
            const materialCells = allMaterials
              .map((m) => {
                const val = valueMap[m];
                return val != null && val !== ""
                  ? `<td class="value-cell">${IRMS.escapeHtml(String(val))}</td>`
                  : '<td class="value-cell muted">-</td>';
              })
              .join("");

            return `<tr data-recipe-id="${recipe.id}">
              <td>${recipe.id}</td>
              ${materialCells}
              <td class="value-cell">${(recipe.items || []).length}</td>
              <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
              <td>${IRMS.formatDateTime(recipe.created_at)}</td>
              <td>${IRMS.escapeHtml(recipe.created_by || "-")}</td>
            </tr>`;
          })
          .join("");

        dom.lookupResult.innerHTML = `<table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>`;

        // Row click to select
        dom.lookupResult.querySelectorAll("tbody tr").forEach((row) => {
          row.addEventListener("click", () => {
            setLookupSelection(Number(row.dataset.recipeId));
          });
        });

        setLookupSelection(null);
        if (dom.lookupActions) dom.lookupActions.hidden = false;
      } catch (error) {
        IRMS.notify(`조회 실패: ${error.message}`, "error");
      } finally {
        IRMS.btnLoading(dom.lookupBtn, false);
      }
    }

    function copyToClipboard(text) {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        return navigator.clipboard.writeText(text);
      }
      // Fallback for non-HTTPS or older browsers
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      return Promise.resolve();
    }

    async function handleLookupCopy() {
      if (!state.selectedRecipeId) return;
      try {
        const detail = await IRMS.getRecipeDetail(state.selectedRecipeId);
        await copyToClipboard(detail.tsv);
        IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
      } catch (error) {
        IRMS.notify(`복사 실패: ${error.message}`, "error");
      }
    }

    async function handleLookupClone() {
      if (!state.selectedRecipeId) return;
      try {
        await ctx.recipeEditLoader.loadRecipeForEdit(state.selectedRecipeId, "버전 비교");
      } catch (error) {
        IRMS.notify(`수정 등록 준비 실패: ${error.message}`, "error");
      }
    }

    return {
      loadProducts,
      setLookupSelection,
      handleLookup,
      handleSetDhr,
      handleDhrModeChange,
      copyToClipboard,
      handleLookupCopy,
      handleLookupClone,
    };
  };
})();
