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
            '<tr><td colspan="6"><div class="empty-state">조건에 맞는 레시피가 없습니다.</div></td></tr>';
          return;
        }

        dom.historyBody.innerHTML = rows
          .map(
            (recipe) => `
              <tr class="history-row" data-recipe-id="${recipe.id}">
                <td>${recipe.id}</td>
                <td class="product-cell">${IRMS.escapeHtml(recipe.productName)}${recipe.isDhr ? ' <span class="chip-dhr">DHR 전용</span>' : ''}</td>
                <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
                <td>${IRMS.escapeHtml(recipe.createdBy || "-")}</td>
                <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
                <td>${(recipe.items || []).length}</td>
              </tr>
            `,
          )
          .join("");

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
              detailRow.innerHTML = `<td colspan="6">
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
