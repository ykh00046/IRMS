/**
 * recipe-history module — 이력 tab: filters persistence + history table
 * with accordion detail rows.
 *
 * Split from static/js/management.js during the split-management-js
 * PDCA cycle (2026-05). See docs/01-plan/features/split-management-js.plan.md.
 *
 * Factory: IRMS.management.createRecipeHistory(ctx)
 * Returns: { persistHistoryFilters, updateHistorySummary,
 *            restoreHistoryFilters, resetHistoryFilters, renderHistory }
 *
 * ctx dependencies:
 *   dom:   historyBody, historyStatus, historySearch, historyFrom,
 *          historyTo, historySummary
 *   const: preferenceKeys
 *   state: selectedRecipeId
 *   other: ctx.copyToClipboard (.history-copy-btn)
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  IRMS.management.createRecipeHistory = function (ctx) {
    const { dom, state } = ctx;
    const { preferenceKeys } = ctx.const;

    function persistHistoryFilters() {
      IRMS.savePreference(preferenceKeys.status, dom.historyStatus.value);
      IRMS.savePreference(preferenceKeys.search, dom.historySearch.value.trim());
      IRMS.savePreference(preferenceKeys.from, dom.historyFrom.value);
      IRMS.savePreference(preferenceKeys.to, dom.historyTo.value);
    }

    function updateHistorySummary() {
      if (!dom.historySummary) {
        return;
      }

      const parts = [`상태 ${dom.historyStatus.value || "전체"}`];
      const search = dom.historySearch.value.trim();
      const from = dom.historyFrom.value;
      const to = dom.historyTo.value;

      if (search) {
        parts.push(`검색어 "${search}"`);
      }
      if (from || to) {
        parts.push(`기간 ${from || "시작 미지정"} ~ ${to || "종료 미지정"}`);
      }

      dom.historySummary.textContent = `${parts.join(" · ")} 기준으로 레시피 현황을 표시 중입니다.`;
    }

    function restoreHistoryFilters() {
      dom.historyStatus.value = IRMS.loadPreference(preferenceKeys.status, "");
      dom.historySearch.value = IRMS.loadPreference(preferenceKeys.search, "");
      dom.historyFrom.value = IRMS.loadPreference(preferenceKeys.from, "");
      dom.historyTo.value = IRMS.loadPreference(preferenceKeys.to, "");
    }

    function resetHistoryFilters() {
      dom.historyStatus.value = "";
      dom.historySearch.value = "";
      dom.historyFrom.value = "";
      dom.historyTo.value = "";
      IRMS.clearPreference(preferenceKeys.status);
      IRMS.clearPreference(preferenceKeys.search);
      IRMS.clearPreference(preferenceKeys.from);
      IRMS.clearPreference(preferenceKeys.to);
      updateHistorySummary();
      renderHistory();
    }

    async function renderHistory() {
      persistHistoryFilters();
      updateHistorySummary();
      try {
        const rows = await IRMS.getRecipes({
          status: dom.historyStatus.value || undefined,
          search: dom.historySearch.value.trim() || undefined,
          dateFrom: dom.historyFrom.value || undefined,
          dateTo: dom.historyTo.value || undefined,
        });

        if (!rows.length) {
          dom.historyBody.innerHTML =
            '<tr><td colspan="11"><div class="empty-state">조건에 맞는 레시피가 없습니다.</div></td></tr>';
          return;
        }

        // 분류 셀 — 책임자는 목록에서 바로 바꾸는 드롭다운(변경 즉시 저장), 그 외는 텍스트.
        const CATS = ["약품", "합성", "잉크", "용수"];
        const categoryCell = (recipe) => {
          const cat = recipe.category || "";
          if (!ctx.canManage) {
            return `<td>${cat ? IRMS.escapeHtml(cat) : '<span class="muted">미분류</span>'}</td>`;
          }
          const opts = `<option value=""${cat === "" ? " selected" : ""}>미분류</option>`
            + CATS.map((c) => `<option value="${c}"${c === cat ? " selected" : ""}>${c}</option>`).join("");
          return `<td><select class="input recipe-cat-select" data-recipe-id="${recipe.id}">${opts}</select></td>`;
        };

        // 품목코드 셀 — 표시 전용. 인라인 편집은 레시피 등록·수정 탭으로 이관
        // (code-edit-relocate §1). 분류 드롭다운은 이 셀과 무관하게 유지.
        const productCodeCell = (recipe) => {
          const code = recipe.productCode || "";
          return `<td class="recipe-code-cell">${code ? IRMS.escapeHtml(code) : '<span class="muted">-</span>'}</td>`;
        };

        // 반응기 셀 — 책임자는 체크박스로 바로 토글(변경 즉시 저장), 그 외는 읽기 전용 텍스트.
        // 분류 셀 편집 패턴과 동일 — PUT /api/recipes/{id}/use-reactor.
        const reactorCell = (recipe) => {
          const on = !!recipe.useReactor;
          if (!ctx.canManage) {
            return `<td>${on ? "사용" : '<span class="muted">-</span>'}</td>`;
          }
          return `<td><input type="checkbox" class="recipe-reactor-toggle" data-recipe-id="${recipe.id}"${on ? " checked" : ""} title="반응기 진행 여부" /></td>`;
        };

        // 파생 셀 — 반응기 셀과 동일 패턴, PUT /api/recipes/{id}/derived. 파생=이월 사용 레시피.
        const derivedCell = (recipe) => {
          const on = !!recipe.isDerived;
          if (!ctx.canManage) {
            return `<td>${on ? "파생" : '<span class="muted">-</span>'}</td>`;
          }
          return `<td><input type="checkbox" class="recipe-derived-toggle" data-recipe-id="${recipe.id}"${on ? " checked" : ""} title="파생(이전 총량 이월) 여부" /></td>`;
        };

        // 1차 셀 — 책임자는 드롭다운으로 개정 없이 이 레시피(2차)의 1차를 바로 지정
        // (PUT /api/recipes/{id}/stage1). 그 외는 연결된 1차명 텍스트. 옵션은 포커스 시
        // 채운다(행마다 전체 목록을 미리 그리면 N² DOM 이 되므로 지연 로드).
        const stage1Cell = (recipe) => {
          if (!ctx.canManage) {
            return `<td>${recipe.stage1ProductName ? IRMS.escapeHtml(recipe.stage1ProductName) : '<span class="muted">-</span>'}</td>`;
          }
          const cur = recipe.stage1RecipeId != null ? String(recipe.stage1RecipeId) : "";
          const label = cur ? IRMS.escapeHtml(recipe.stage1ProductName || cur) : "없음";
          return `<td><select class="input recipe-stage1-select" data-recipe-id="${recipe.id}" data-cur="${cur}" title="이 레시피(2차)의 1차 레시피 — 개정 없이 바로 지정"><option value="${cur}">${label}</option></select></td>`;
        };

        // 한 레시피 행 — stagePin('1차'/'2차') 이 있으면 가족 멤버로 표시.
        const rowHtml = (recipe, stagePin) => {
          const pin = stagePin
            ? `<span class="stage-pin ${stagePin === "1차" ? "one" : "two"}">${stagePin}</span> `
            : "";
          return `
              <tr class="history-row${stagePin ? " family-member" : ""}" data-recipe-id="${recipe.id}">
                <td>${recipe.id}</td>
                <td class="product-cell">${pin}${IRMS.escapeHtml(recipe.productName)}${recipe.isDhr ? ' <span class="chip-dhr">DHR 전용</span>' : ''}</td>
                ${productCodeCell(recipe)}
                ${categoryCell(recipe)}
                ${reactorCell(recipe)}
                ${derivedCell(recipe)}
                ${stage1Cell(recipe)}
                <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
                <td>${IRMS.escapeHtml(recipe.createdBy || "-")}</td>
                <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
                <td>${(recipe.items || []).length}</td>
              </tr>`;
        };

        // 1차/2차 가족 묶음 — 2차(stage1RecipeId 지정) 와 그 1차를 인접 그룹으로 묶어 표시.
        // 가족은 먼저 등장한 멤버 위치에 나타나고(최신순 반영), 다른 멤버는 그 자리로 끌어온다.
        const byId = new Map(rows.map((r) => [r.id, r]));
        const emitted = new Set();
        const COLSPAN = 11;
        const parts = [];
        rows.forEach((r) => {
          if (emitted.has(r.id)) return;
          let two = null;
          let one = null;
          if (r.stage1RecipeId && byId.has(r.stage1RecipeId)) {
            two = r; one = byId.get(r.stage1RecipeId);           // r = 2차
          } else {
            const child = rows.find((x) => x.stage1RecipeId === r.id);
            if (child) { one = r; two = child; }                 // r = 1차(2차가 뒤에 있음)
          }
          if (two && one) {
            parts.push(`<tr class="family-head-row"><td colspan="${COLSPAN}">◆ ${IRMS.escapeHtml(two.productName)} · 2단 제조 가족</td></tr>`);
            parts.push(rowHtml(one, "1차"));
            parts.push(rowHtml(two, "2차"));
            emitted.add(one.id); emitted.add(two.id);
          } else {
            parts.push(rowHtml(r));
            emitted.add(r.id);
          }
        });
        dom.historyBody.innerHTML = parts.join("");

        // 분류 드롭다운 — 변경 즉시 PUT /api/recipes/{id}/category. 클릭이 행 확장으로
        // 번지지 않게 막는다(행 클릭 = 상세 펼침). x-csrftoken 헤더 직접 부착.
        dom.historyBody.querySelectorAll(".recipe-cat-select").forEach((sel) => {
          sel.addEventListener("click", (e) => e.stopPropagation());
          sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(sel.dataset.recipeId);
            const category = sel.value ? sel.value : null;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/category`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ category }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(category ? `분류를 '${category}'(으)로 지정했습니다.` : "분류를 미분류로 되돌렸습니다.", "success");
            } catch (err) {
              IRMS.notify(`분류 저장 실패: ${err.message}`, "error");
            }
          });
        });

        // 반응기 토글 — 변경 즉시 PUT /api/recipes/{id}/use-reactor. 분류 드롭다운과 동일한
        // CSRF 부착 패턴. 클릭이 행 확장으로 번지지 않게 막는다(행 클릭 = 상세 펼침).
        dom.historyBody.querySelectorAll(".recipe-reactor-toggle").forEach((cb) => {
          cb.addEventListener("click", (e) => e.stopPropagation());
          cb.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(cb.dataset.recipeId);
            const useReactor = !!cb.checked;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/use-reactor`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ use_reactor: useReactor }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(useReactor ? "반응기 진행으로 지정했습니다." : "반응기 진행을 해제했습니다.", "success");
            } catch (err) {
              // 저장 실패 시 체크박스를 이전 상태로 되돌려 표시와 서버를 맞춘다.
              cb.checked = !useReactor;
              IRMS.notify(`반응기 저장 실패: ${err.message}`, "error");
            }
          });
        });

        // 파생 토글 — 변경 즉시 PUT /api/recipes/{id}/derived. 반응기 토글과 동일 패턴.
        dom.historyBody.querySelectorAll(".recipe-derived-toggle").forEach((cb) => {
          cb.addEventListener("click", (e) => e.stopPropagation());
          cb.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(cb.dataset.recipeId);
            const isDerived = !!cb.checked;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/derived`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ is_derived: isDerived }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(isDerived ? "파생 레시피로 지정했습니다." : "파생 지정을 해제했습니다.", "success");
            } catch (err) {
              // 저장 실패 시 체크박스를 이전 상태로 되돌려 표시와 서버를 맞춘다.
              cb.checked = !isDerived;
              IRMS.notify(`파생 저장 실패: ${err.message}`, "error");
            }
          });
        });

        // 1차 지정 드롭다운 — 포커스 시 후보 채움(지연), 변경 즉시 PUT /api/recipes/{id}/stage1.
        // 개정을 만들지 않고 바로 연결/해제하고 가족 묶음을 다시 그린다. 클릭이 행 확장으로
        // 번지지 않게 막는다. 반응기/파생 토글과 동일한 CSRF 부착 패턴.
        dom.historyBody.querySelectorAll(".recipe-stage1-select").forEach((sel) => {
          sel.addEventListener("click", (e) => e.stopPropagation());
          sel.addEventListener("focus", () => {
            if (sel.dataset.filled) return;
            const rid = Number(sel.dataset.recipeId);
            const cur = sel.dataset.cur || "";
            sel.innerHTML = `<option value=""${cur === "" ? " selected" : ""}>없음</option>`
              + rows.filter((o) => o.id !== rid)
                  .map((o) => `<option value="${o.id}"${String(o.id) === cur ? " selected" : ""}>${IRMS.escapeHtml(o.productName)}</option>`)
                  .join("");
            sel.dataset.filled = "1";
          });
          sel.addEventListener("change", async (e) => {
            e.stopPropagation();
            const rid = Number(sel.dataset.recipeId);
            const val = sel.value ? Number(sel.value) : null;
            try {
              const headers = { "Content-Type": "application/json" };
              const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
              if (token) headers["x-csrftoken"] = token;
              const resp = await fetch(`/api/recipes/${rid}/stage1`, {
                method: "PUT",
                credentials: "same-origin",
                headers,
                body: JSON.stringify({ stage1_recipe_id: val }),
              });
              if (!resp.ok) {
                let msg = `Request failed (${resp.status})`;
                try { const p = await resp.json(); if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail); } catch (_e) { /* noop */ }
                throw new Error(msg);
              }
              await resp.json();
              IRMS.notify(val ? "1차 레시피를 연결했습니다." : "1차 연결을 해제했습니다.", "success");
              renderHistory();  // 가족 묶음 즉시 반영
            } catch (err) {
              IRMS.notify(`1차 연결 실패: ${err.message}`, "error");
              renderHistory();  // 실패 시 서버 값으로 다시 그림
            }
          });
        });

        // Accordion: row click to expand detail
        dom.historyBody.querySelectorAll(".history-row").forEach((row) => {
          row.style.cursor = "pointer";
          row.addEventListener("click", async () => {
            const recipeId = Number(row.dataset.recipeId);
            const existing = row.nextElementSibling;
            if (existing && existing.classList.contains("history-detail-row")) {
              existing.remove();
              row.classList.remove("selected");
              return;
            }
            // Close any other open detail
            dom.historyBody.querySelectorAll(".history-detail-row").forEach((r) => r.remove());
            dom.historyBody.querySelectorAll(".history-row.selected").forEach((r) => r.classList.remove("selected"));

            row.classList.add("selected");
            try {
              const detail = await IRMS.getRecipeDetail(recipeId);
              const items = detail.items || [];
              const itemsHtml = items.length
                ? items.map((it) =>
                    `<span class="detail-chip">${IRMS.escapeHtml(it.material_name)}: ${IRMS.escapeHtml(String(it.value))}</span>`
                  ).join("")
                : '<span class="muted">재료 없음</span>';

              const detailRow = document.createElement("tr");
              detailRow.classList.add("history-detail-row");
              const dhrActionLabel = detail.is_dhr ? "DHR 전용 해제" : "DHR 전용 지정";
              detailRow.innerHTML = `<td colspan="11">
                <div class="history-detail-content">
                  <div class="detail-items">${itemsHtml}</div>
                  <div class="detail-actions">
                    <button class="btn btn-sm history-copy-btn" data-recipe-id="${recipeId}">엑셀로 복사</button>
                    <button class="btn btn-sm accent history-edit-btn" data-recipe-id="${recipeId}">수정 등록</button>
                    <button class="btn btn-sm history-version-btn" data-recipe-id="${recipeId}">버전 이력</button>
                    <button class="btn btn-sm history-dhr-btn" data-recipe-id="${recipeId}">${dhrActionLabel}</button>
                    ${detail.status !== "canceled"
                      ? `<button class="btn btn-sm history-cancel-btn" data-recipe-id="${recipeId}">등록 취소</button>`
                      : ""}
                    <button class="btn btn-sm danger history-delete-btn" data-recipe-id="${recipeId}">레시피 삭제</button>
                    <button class="btn btn-sm danger history-delete-with-records-btn" data-recipe-id="${recipeId}">레시피+기록 삭제</button>
                  </div>
                </div>
              </td>`;
              row.after(detailRow);
              if (!ctx.canManage) {
                detailRow
                  .querySelectorAll(
                    ".history-edit-btn, .history-dhr-btn, .history-cancel-btn, .history-delete-btn, .history-delete-with-records-btn",
                  )
                  .forEach((button) => {
                    button.hidden = true;
                    button.disabled = true;
                  });
              }

              detailRow.querySelector(".history-copy-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await ctx.copyToClipboard(detail.tsv);
                  IRMS.notify("클립보드에 복사되었습니다. 엑셀에서 Ctrl+V로 붙여넣으세요.", "success");
                } catch (err) {
                  IRMS.notify(`복사 실패: ${err.message}`, "error");
                }
              });

              detailRow.querySelector(".history-edit-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await ctx.recipeEditLoader.loadRecipeForEdit(recipeId, "레시피 현황");
                } catch (err) {
                  IRMS.notify(`수정 등록 준비 실패: ${err.message}`, "error");
                }
              });

              detailRow.querySelector(".history-version-btn").addEventListener("click", (e) => {
                e.stopPropagation();
                state.selectedRecipeId = recipeId;
                ctx.versionCompare.handleLookupHistory();
              });

              detailRow.querySelector(".history-dhr-btn").addEventListener("click", async (e) => {
                e.stopPropagation();
                try {
                  await IRMS.setRecipeDhr(recipeId, !detail.is_dhr);
                  IRMS.notify(!detail.is_dhr ? "DHR 전용으로 지정했습니다." : "DHR 전용을 해제했습니다.", "success");
                  renderHistory();
                } catch (err) {
                  IRMS.notify(`DHR 변경 실패: ${err.message}`, "error");
                }
              });

              const cancelBtn = detailRow.querySelector(".history-cancel-btn");
              if (cancelBtn) {
                cancelBtn.addEventListener("click", async (e) => {
                  e.stopPropagation();
                  if (!window.confirm("이 레시피를 등록 취소하시겠습니까?")) return;
                  try {
                    await IRMS.updateRecipeStatus(recipeId, "cancel");
                    IRMS.notify("레시피를 취소했습니다.", "success");
                    renderHistory();
                  } catch (err) {
                    IRMS.notify(`취소 실패: ${err.message}`, "error");
                  }
                });
              }

              async function deleteRecipeFromHistory(deleteBlendRecords) {
                const message = deleteBlendRecords
                  ? "이 레시피와 연결된 배합 기록을 함께 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다."
                  : "이 레시피만 삭제하시겠습니까? 연결된 배합 기록은 남고 레시피 연결만 해제됩니다.";
                if (!window.confirm(message)) return;
                try {
                  const result = await IRMS.deleteRecipe(recipeId, deleteBlendRecords);
                  const linkedCount = Number(result.linked_record_count || 0);
                  const suffix = linkedCount
                    ? ` 연결 기록 ${linkedCount}건 ${deleteBlendRecords ? "삭제" : "보존"}`
                    : "";
                  IRMS.notify(`레시피를 삭제했습니다.${suffix}`, "success");
                  renderHistory();
                } catch (err) {
                  IRMS.notify(`삭제 실패: ${err.message}`, "error");
                }
              }

              const deleteBtn = detailRow.querySelector(".history-delete-btn");
              if (deleteBtn) {
                deleteBtn.addEventListener("click", (e) => {
                  e.stopPropagation();
                  deleteRecipeFromHistory(false);
                });
              }

              const deleteWithRecordsBtn = detailRow.querySelector(".history-delete-with-records-btn");
              if (deleteWithRecordsBtn) {
                deleteWithRecordsBtn.addEventListener("click", (e) => {
                  e.stopPropagation();
                  deleteRecipeFromHistory(true);
                });
              }
            } catch (error) {
              IRMS.notify(`상세 조회 실패: ${error.message}`, "error");
            }
          });
        });
      } catch (error) {
        IRMS.notify(`이력 조회 실패: ${error.message}`, "error");
      }
    }

    return {
      persistHistoryFilters,
      updateHistorySummary,
      restoreHistoryFilters,
      resetHistoryFilters,
      renderHistory,
    };
  };
})();
