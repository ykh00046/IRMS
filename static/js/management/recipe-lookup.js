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
        renderLookupChips(items);
      } catch (error) {
        IRMS.notify(`제품 목록 로드 실패: ${error.message}`, "error");
      }
    }

    // 빈 상태용 클릭 가능한 반제품 칩 목록 — 검색 결과가 #lookup-result 를 덮어쓰기
    // 전까지 한 번의 클릭으로 비교를 시작할 수 있게. #lookup-chips 가 DOM 에 있을 때
    // (=아직 검색 결과로 교체되지 않음)만 그린다. DHR 모드 전환 시 loadProducts 가
    // 다시 불리므로 모드에 맞는 목록으로 갱신된다.
    function renderLookupChips(items) {
      const wrap = document.getElementById("lookup-chips");
      if (!wrap) return;
      const names = (items || []).slice(0, 40);
      const overflow = items.length - names.length;
      const chipsHtml = names
        .map(
          (name) =>
            `<button type="button" class="btn compact lookup-chip" data-name="${IRMS.escapeHtml(name)}">${IRMS.escapeHtml(name)}</button>`,
        )
        .join("");
      const overflowHtml = overflow > 0
        ? `<span class="muted">외 ${overflow}개 — 이름으로 검색하세요</span>`
        : "";
      wrap.innerHTML = chipsHtml + overflowHtml;
      wrap.querySelectorAll(".lookup-chip").forEach((btn) => {
        btn.addEventListener("click", () => {
          if (dom.lookupProduct) dom.lookupProduct.value = btn.dataset.name;
          handleLookup();
        });
      });
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
        // 분류 줄 — 현재 값 표시 + (책임자) 약품/합성/잉크 선택·저장(PUT /api/recipes/{id}/category).
        // 배합·이어서계량 화면의 2단계 선택(분류→레시피) 필터에 쓰인다.
        const CATS = ["약품", "합성", "잉크", "용수"];
        const catCurrent = detail.category || "";
        const catCurrentText = catCurrent
          ? IRMS.escapeHtml(catCurrent)
          : '<span class="muted">미분류</span>';
        const catOptions = '<option value="">미분류</option>'
          + CATS.map((c) => `<option value="${c}"${c === catCurrent ? " selected" : ""}>${c}</option>`).join("");
        const catEditor = canManage
          ? `<select id="lookup-category-select" class="input">${catOptions}</select>`
            + `<button id="lookup-category-save" class="btn" type="button">저장</button>`
          : "";
        // 속성 한 줄 = [라벨][현재값][편집기]. 종전엔 label·span·select 를 감싸는 요소 없이
        // 평면으로 이어 붙여 "기준 자재현재:없음"처럼 글자가 붙고 [저장]이 다음 항목 라벨과
        // 같은 줄에 엉켰다(.lookup-anchor 에 CSS 가 아예 없었다 — 2026-08-06 검토).
        const attrRow = (labelHtml, currentHtml, editorHtml) =>
          `<div class="lookup-attr-row">${labelHtml}`
          + `<span class="lookup-attr-current"><span class="muted">현재:</span> ${currentHtml}</span>`
          + (editorHtml ? `<span class="lookup-attr-editor">${editorHtml}</span>` : "")
          + `</div>`;
        wrap.innerHTML =
          attrRow(
            `<label class="filter-label" for="lookup-anchor-select">기준 자재</label>`,
            `<span id="lookup-anchor-current">${currentText}</span>`,
            editor,
          ) +
          attrRow(
            `<label class="filter-label" for="lookup-tolerance-input">허용 편차</label>`,
            `<span id="lookup-tolerance-current">${tolCurrentText}</span>`,
            tolEditor,
          ) +
          attrRow(
            `<label class="filter-label" for="lookup-category-select">분류</label>`,
            `<span id="lookup-category-current">${catCurrentText}</span>`,
            catEditor,
          ) +
          renderLossCompEditor(detail, uniq, canManage);
        wrap.hidden = false;
        if (canManage) {
          const saveBtn = document.getElementById("lookup-anchor-save");
          if (saveBtn) saveBtn.addEventListener("click", () => handleSaveAnchor(recipeId));
          const tolSaveBtn = document.getElementById("lookup-tolerance-save");
          if (tolSaveBtn) tolSaveBtn.addEventListener("click", () => handleSaveTolerance(recipeId));
          const catSaveBtn = document.getElementById("lookup-category-save");
          if (catSaveBtn) catSaveBtn.addEventListener("click", () => handleSaveCategory(recipeId));
          wireLossCompEditor(recipeId, uniq);
        }
      } catch (error) {
        wrap.hidden = false;
        wrap.innerHTML = `<span class="muted">기준 자재 정보를 불러오지 못했습니다: ${IRMS.escapeHtml(error.message || String(error))}</span>`;
      }
    }

    // 투입 로스 보정(파우더 투입 손실 보정) 에디터 — [자재 ▼][보정 g][✕] 반복 행 + [+ 보정 추가].
    // BOM 표 자체(엑셀 붙여넣기 포함)는 건드리지 않고, 별도 속성 줄에서 자재별 고정 g 보정을
    // 지정한다. 저장은 PUT /api/recipes/{id}/loss-comp {items:[{material_name, loss_comp_g}]}.
    function renderLossCompEditor(detail, itemNames, canManage) {
      const existing = (detail.items || []).filter(
        (it) => Number(it.loss_comp_g) > 0 && it.material_name,
      );
      const currentText = existing.length
        ? existing.map((it) => `<span class="lookup-losscomp-badge">+${it.loss_comp_g}g 보정</span> ${IRMS.escapeHtml(it.material_name)}`).join(" · ")
        : '<span class="muted">없음</span>';
      if (!canManage) {
        return `<div class="lookup-attr-row"><label class="filter-label">투입 로스 보정</label>`
          + `<span class="lookup-attr-current"><span class="muted">현재:</span> ${currentText}</span></div>`;
      }
      const options = itemNames.length
        ? itemNames.map((n) => `<option value="${IRMS.escapeHtml(n)}">${IRMS.escapeHtml(n)}</option>`).join("")
        : "";
      const rowsHtml = existing.map((it) => lossCompRowHtml(itemNames, it.material_name, it.loss_comp_g)).join("");
      // 보정 줄은 [자재▼][g][✕] 반복 행·설명문까지 딸려 다층이라, 한 줄짜리 속성과 달리
      // 세로로 쌓는 블록 변형(lookup-attr-row-block)으로 감싼다.
      return `<div class="lookup-attr-row lookup-attr-row-block">`
        + `<label class="filter-label lookup-losscomp-label">투입 로스 보정</label>`
        + `<span class="lookup-attr-current"><span class="muted">현재:</span> <span id="lookup-losscomp-current">${currentText}</span></span>`
        + `<div class="lookup-losscomp-rows" id="lookup-losscomp-rows">${rowsHtml}</div>`
        + `<div class="lookup-losscomp-actions">`
        + `<button id="lookup-losscomp-add" class="btn btn-sm" type="button">+ 보정 추가</button>`
        + `<button id="lookup-losscomp-save" class="btn" type="button">저장</button>`
        + `</div>`
        + `<p class="imp-attr-desc lookup-losscomp-desc">지정 자재는 계량 목표가 (비율 환산량 + 보정 g)이 됩니다. 붓는 과정 로스가 있는 파우더용 — 기록·출력엔 보정 포함량이 그대로 남습니다.</p>`
        + `<p class="imp-attr-desc lookup-losscomp-desc">기본은 품목코드 탭의 자재 마스터에서 지정합니다 — 여기는 이 레시피만의 예외값(마스터보다 우선).</p>`
        + (itemNames.length ? "" : '<p class="login-error lookup-losscomp-error">BOM 자재가 없습니다.</p>')
        + `<input type="hidden" id="lookup-losscomp-options" value="" data-options="${IRMS.escapeHtml(options)}" />`
        + `</div>`;
    }

    function lossCompRowHtml(itemNames, selectedName, value) {
      const opts = itemNames.length
        ? itemNames.map((n) => `<option value="${IRMS.escapeHtml(n)}"${n === selectedName ? " selected" : ""}>${IRMS.escapeHtml(n)}</option>`).join("")
        : "";
      return `<div class="lookup-losscomp-row">`
        + `<select class="input lookup-losscomp-mat">${opts}</select>`
        + `<input class="input lookup-losscomp-g" type="number" step="0.1" min="0" max="100" placeholder="보정 g" value="${value != null ? IRMS.escapeHtml(String(value)) : ""}" />`
        + `<button class="btn btn-sm lookup-losscomp-del" type="button" title="삭제">✕</button>`
        + `</div>`;
    }

    function wireLossCompEditor(recipeId, itemNames) {
      const rowsEl = document.getElementById("lookup-losscomp-rows");
      const addBtn = document.getElementById("lookup-losscomp-add");
      const saveBtn = document.getElementById("lookup-losscomp-save");
      if (addBtn) addBtn.addEventListener("click", () => {
        if (!rowsEl) return;
        const tmp = document.createElement("div");
        tmp.innerHTML = lossCompRowHtml(itemNames, "", "");
        rowsEl.appendChild(tmp.firstChild);
      });
      if (rowsEl) rowsEl.addEventListener("click", (e) => {
        const del = e.target.closest(".lookup-losscomp-del");
        if (del) del.closest(".lookup-losscomp-row").remove();
      });
      if (saveBtn) saveBtn.addEventListener("click", () => handleSaveLossComp(recipeId));
    }

    async function handleSaveLossComp(recipeId) {
      const saveBtn = document.getElementById("lookup-losscomp-save");
      const rowsEl = document.getElementById("lookup-losscomp-rows");
      const errEl = document.querySelector(".lookup-losscomp-error");
      if (errEl) errEl.remove();
      const items = [];
      const seen = new Set();
      let bad = "";
      if (rowsEl) {
        rowsEl.querySelectorAll(".lookup-losscomp-row").forEach((row) => {
          const matSel = row.querySelector(".lookup-losscomp-mat");
          const gInput = row.querySelector(".lookup-losscomp-g");
          const name = (matSel && matSel.value || "").trim();
          const rawG = (gInput && gInput.value || "").trim();
          if (!name && !rawG) return;  // 빈 행은 무시
          const g = Number(rawG);
          if (!name) { bad = "자재를 선택하세요."; return; }
          if (!Number.isFinite(g) || g <= 0 || g > 100) { bad = `보정값은 0 초과 100 이하여야 합니다: ${name}`; return; }
          if (seen.has(name)) { bad = `같은 자재가 중복됩니다: ${name}`; return; }
          seen.add(name);
          items.push({ material_name: name, loss_comp_g: g });
        });
      }
      if (bad) {
        IRMS.notify(bad, "error");
        return;
      }
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/loss-comp`, {
          method: "PUT",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ items }),
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
        // 성공 — 패널 다시 그려 현재 값 갱신(편집기도 리셋).
        await renderAnchorPanel(recipeId);
        IRMS.notify(
          items.length
            ? `투입 로스 보정 ${items.length}건을 저장했습니다.`
            : "투입 로스 보정을 모두 해제했습니다.",
          "success",
        );
      } catch (error) {
        IRMS.notify(`투입 로스 보정 저장 실패: ${error.message}`, "error");
      } finally {
        if (saveBtn) IRMS.btnLoading(saveBtn, false);
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

    // 분류 저장 — 빈 값은 null(미분류), 아니면 약품/합성/잉크 중 하나. PUT /api/recipes/{id}/category.
    // 기준 자재/허용 편차 저장과 동일하게 x-csrftoken 헤더를 직접 부착한다.
    async function handleSaveCategory(recipeId) {
      const sel = document.getElementById("lookup-category-select");
      const saveBtn = document.getElementById("lookup-category-save");
      if (!sel) return;
      const category = sel.value ? sel.value : null;
      if (saveBtn) IRMS.btnLoading(saveBtn, true);
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const resp = await fetch(`/api/recipes/${recipeId}/category`, {
          method: "PUT",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ category }),
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
        const cur = document.getElementById("lookup-category-current");
        if (cur) cur.innerHTML = category ? IRMS.escapeHtml(category) : '<span class="muted">미분류</span>';
        IRMS.notify(category ? `분류를 '${category}'(으)로 지정했습니다.` : "분류를 미분류로 되돌렸습니다.", "success");
      } catch (error) {
        IRMS.notify(`분류 저장 실패: ${error.message}`, "error");
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
        // 빈 상태로 원복 — 칩 컨테이너도 함께 넣어 loadProducts 의 renderLookupChips
        // 가 다시 붙일 수 있게(loadProducts 는 이미 위에서 불렸으므로 여기서 직접 채운다).
        dom.lookupResult.innerHTML =
          '<p class="empty-state">반제품명을 선택하면 버전별 자재 구성이 표시됩니다.</p>' +
          '<div id="lookup-chips" class="lookup-chips"></div>';
        const products = (dom.productList && dom.productList.children)
          ? Array.from(dom.productList.children).map((o) => o.value)
          : [];
        renderLookupChips(products);
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
