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
            '<tr><td colspan="8"><div class="empty-state">조건에 맞는 레시피가 없습니다.</div></td></tr>';
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

        // 품목코드 셀(item-code-admin §B3) — 분류 셀과 동일 방식의 인라인 편집.
        // canManage 면 코드 표시 + "지정/수정" 작은 버튼. 클릭 시 input + 마스터 제안(A1, kind=product),
        // Enter/저장 → A4 PUT. 행 확장(accordion) 충돌 방지용 stopPropagation.
        const productCodeCell = (recipe) => {
          const code = recipe.productCode || "";
          if (!ctx.canManage) {
            return `<td class="recipe-code-cell">${code ? IRMS.escapeHtml(code) : '<span class="muted">-</span>'}</td>`;
          }
          const codeHtml = code
            ? `<span class="code-value">${IRMS.escapeHtml(code)}</span>`
            : '<span class="muted">-</span>';
          const btnLabel = code ? "수정" : "지정";
          return `<td class="recipe-code-cell">
            ${codeHtml}
            <button class="btn btn-sm recipe-code-edit-btn" data-recipe-id="${recipe.id}" type="button">${btnLabel}</button>
          </td>`;
        };

        dom.historyBody.innerHTML = rows
          .map(
            (recipe) => `
              <tr class="history-row" data-recipe-id="${recipe.id}">
                <td>${recipe.id}</td>
                <td class="product-cell">${IRMS.escapeHtml(recipe.productName)}${recipe.isDhr ? ' <span class="chip-dhr">DHR 전용</span>' : ''}</td>
                ${productCodeCell(recipe)}
                ${categoryCell(recipe)}
                <td><span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span></td>
                <td>${IRMS.escapeHtml(recipe.createdBy || "-")}</td>
                <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
                <td>${(recipe.items || []).length}</td>
              </tr>
            `,
          )
          .join("");

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

        // 품목코드 인라인 지정(item-code-admin §B3) — 분류 드롭다운과 동일한 자리/패턴.
        // 행 확장(accordion) 충돌 방지용 stopPropagation. x-csrftoken 헤더 직접 부착.
        dom.historyBody.querySelectorAll(".recipe-code-edit-btn").forEach((btn) => {
          btn.addEventListener("click", (e) => {
            e.stopPropagation();
            const cell = btn.closest(".recipe-code-cell");
            startProductCodeEdit(cell);
          });
          btn.addEventListener("mousedown", (e) => e.stopPropagation());
        });

        async function loadProductCodeSuggestions(q, suggestList, input) {
          try {
            const data = await IRMS._core.request("/item-codes/master", {
              query: { q, kind: "product" },
            });
            const items = data.items || [];
            if (!items.length) {
              suggestList.hidden = true;
              suggestList.innerHTML = "";
              return;
            }
            suggestList.innerHTML = items
              .map(
                (it) =>
                  `<li class="code-suggest-item" data-code="${IRMS.escapeHtml(it.code)}">${IRMS.escapeHtml(it.code)} — ${IRMS.escapeHtml(it.name)}</li>`,
              )
              .join("");
            suggestList.hidden = false;
            suggestList.querySelectorAll(".code-suggest-item").forEach((li) => {
              li.addEventListener("mousedown", (ev) => {
                ev.preventDefault();
                input.value = li.dataset.code;
                suggestList.hidden = true;
                input.focus();
              });
            });
          } catch (_err) {
            suggestList.hidden = true;
          }
        }

        const debouncedProductSuggest = IRMS.debounce(loadProductCodeSuggestions, 300);

        function startProductCodeEdit(cell) {
          // 이미 편집 중이면 무시(중복 진입 방지)
          if (cell.querySelector(".code-edit-wrap")) return;
          const recipeId = Number(cell.querySelector(".recipe-code-edit-btn").dataset.recipeId);
          const current = cell.querySelector(".code-value");
          const currentValue = current ? current.textContent.trim() : "";

          cell.innerHTML = `
            <div class="code-edit-wrap">
              <input class="input compact code-inline-input" value="${IRMS.escapeHtml(currentValue)}" placeholder="코드 입력 (예: BC0001)" />
              <button class="btn btn-sm success code-save-btn" type="button">저장</button>
              <button class="btn btn-sm code-cancel-btn" type="button">취소</button>
              <ul class="code-suggest-list" hidden></ul>
            </div>`;

          const input = cell.querySelector(".code-inline-input");
          const suggestList = cell.querySelector(".code-suggest-list");

          input.focus();
          input.select();
          input.addEventListener("input", () => {
            const q = input.value.trim();
            if (q.length < 1) {
              suggestList.hidden = true;
              return;
            }
            debouncedProductSuggest(q, suggestList, input);
          });
          input.addEventListener("keydown", (ev) => {
            if (ev.key === "Enter") {
              ev.preventDefault();
              saveProductCode(recipeId, input.value, cell);
            } else if (ev.key === "Escape") {
              ev.preventDefault();
              renderHistory(); // 원복 — 전체 재렌더로 셀 복구
            }
          });
          input.addEventListener("click", (ev) => ev.stopPropagation());

          cell.querySelector(".code-save-btn").addEventListener("click", (ev) => {
            ev.stopPropagation();
            saveProductCode(recipeId, input.value, cell);
          });
          cell.querySelector(".code-cancel-btn").addEventListener("click", (ev) => {
            ev.stopPropagation();
            renderHistory();
          });
        }

        async function saveProductCode(recipeId, rawValue, cell) {
          const value = String(rawValue || "").trim();
          const productCode = value === "" ? null : value;
          try {
            const headers = { "Content-Type": "application/json" };
            const token =
              IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
            if (token) headers["x-csrftoken"] = token;
            const resp = await fetch(`/api/recipes/${recipeId}/product-code`, {
              method: "PUT",
              credentials: "same-origin",
              headers,
              body: JSON.stringify({ product_code: productCode }),
            });
            if (!resp.ok) {
              let msg = `Request failed (${resp.status})`;
              try {
                const p = await resp.json();
                if (p && p.detail) {
                  msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail);
                }
              } catch (_e) { /* noop */ }
              IRMS.notify(`품목코드 저장 실패: ${msg}`, "error");
              return;
            }
            const result = await resp.json();
            const saved = result.product_code || "";
            IRMS.notify(
              saved ? `품목코드를 '${saved}'(으)로 지정했습니다. (체인 ${result.updated}건)` : "품목코드를 해제했습니다.",
              "success",
            );
            renderHistory();
          } catch (err) {
            IRMS.notify(`품목코드 저장 실패: ${err.message}`, "error");
          }
        }

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
              detailRow.innerHTML = `<td colspan="8">
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
