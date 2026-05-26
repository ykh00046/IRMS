/**
 * recipe-table module — 처리 대기 레시피 테이블 렌더링 + 행 액션(완료/원복).
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.createRecipeTable(ctx)
 * Returns: { render, bindRowActions, countRecipeMaterials }
 * ctx deps:
 *   - ctx.dom.{tableHead, tableBody, statsCount, statsStatus}
 *   - ctx.state.loadingToken (race 방지 토큰; 모듈이 ++)
 *
 * `countRecipeMaterials`는 순수 함수로 노출(테스트용).
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  NS.work.createRecipeTable = function (ctx) {
    const { dom, state } = ctx;

    function buildHeader() {
      dom.tableHead.innerHTML = [
        '<th class="sticky-left">제품명</th>',
        "<th>위치</th>",
        "<th>잉크명</th>",
        "<th>원재료</th>",
        "<th>상태</th>",
        "<th>등록시각</th>",
        '<th class="sticky-right">처리</th>',
      ].join("");
    }

    function countRecipeMaterials(recipe) {
      return (recipe.items || []).length;
    }

    function buildRows(recipes) {
      if (!recipes.length) {
        dom.tableBody.innerHTML =
          '<tr><td colspan="7"><div class="empty-state">현재 조건에서 처리할 레시피가 없습니다.</div></td></tr>';
        return;
      }

      dom.tableBody.innerHTML = recipes
        .map((recipe) => {
          const status = `<span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span>`;
          const materialCount = countRecipeMaterials(recipe);
          const materialCell = materialCount > 0
            ? `<span class="material-count-badge">${materialCount}종</span>`
            : "-";

          return `
            <tr class="recipe-row" data-id="${recipe.id}">
              <td class="sticky-left product-cell">${IRMS.escapeHtml(recipe.productName)}</td>
              <td>${IRMS.escapeHtml(recipe.position || "-")}</td>
              <td>${IRMS.escapeHtml(recipe.inkName)}</td>
              <td>${materialCell}</td>
              <td>${status}</td>
              <td>${IRMS.formatDateTime(recipe.createdAt)}</td>
              <td class="sticky-right">
                ${recipe.status === "in_progress"
                  ? `<button type="button" class="btn success complete-btn" data-id="${recipe.id}">완료</button>
                     <button type="button" class="btn reset-btn" data-reset-id="${recipe.id}">원복</button>`
                  : `<span class="status-chip ${IRMS.statusClass(recipe.status)}">${IRMS.statusLabel(recipe.status)}</span>`
                }
              </td>
            </tr>
          `;
        })
        .join("");
    }

    function renderStats(recipes) {
      const pendingCount = recipes.filter((recipe) => recipe.status === "pending").length;
      const inProgressCount = recipes.filter((recipe) => recipe.status === "in_progress").length;
      dom.statsCount.textContent = String(recipes.length);
      dom.statsStatus.textContent = `대기 ${pendingCount} / 진행 ${inProgressCount}`;
    }

    async function render() {
      const token = ++state.loadingToken;

      try {
        const activeRecipes = await IRMS.getRecipes({});

        if (token !== state.loadingToken) {
          return;
        }

        const working = activeRecipes.filter(
          (recipe) => recipe.status === "pending" || recipe.status === "in_progress"
        );

        buildHeader();
        buildRows(working);
        renderStats(working);
      } catch (error) {
        IRMS.notify(`데이터 로드 실패: ${error.message}`, "error");
        dom.tableBody.innerHTML =
          '<tr><td colspan="7"><div class="empty-state">데이터를 불러오지 못했습니다.</div></td></tr>';
      }
    }

    function bindRowActions() {
      dom.tableBody.addEventListener("click", async (event) => {
        const target = event.target;
        if (!(target instanceof HTMLElement)) return;

        // Complete button
        if (target.classList.contains("complete-btn")) {
          const recipeId = Number(target.dataset.id);
          if (!Number.isFinite(recipeId)) return;
          if (!window.confirm("선택한 레시피를 완료 처리하시겠습니까?")) return;

          const row = dom.tableBody.querySelector(`tr[data-id="${recipeId}"]`);
          try {
            if (row) row.classList.add("removing");
            await IRMS.updateRecipeStatus(recipeId, "complete");
            IRMS.notify("레시피를 완료 처리했습니다.", "success");
            window.setTimeout(render, row ? 280 : 0);
          } catch (error) {
            if (row) row.classList.remove("removing");
            const msg = error.message || "";
            if (msg.includes("WEIGHING_INCOMPLETE")) {
              const remaining = msg.split(":")[1] || "?";
              IRMS.notify(`미계량 ${remaining}건이 남아있습니다. 계량 모드에서 진행해 주세요.`, "error");
            } else {
              IRMS.notify(`완료 처리 실패: ${msg}`, "error");
            }
          }
          return;
        }

        // Reset button
        if (target.classList.contains("reset-btn")) {
          const recipeId = Number(target.dataset.resetId);
          if (!Number.isFinite(recipeId)) return;
          if (!window.confirm("이 레시피의 계량을 모두 초기화(원복)하시겠습니까?")) return;

          try {
            await IRMS.resetWeighingRecipe(recipeId);
            IRMS.notify("레시피 계량을 원복했습니다.", "success");
            render();
          } catch (error) {
            IRMS.notify(`원복 실패: ${error.message}`, "error");
          }
        }
      });
    }

    return { render, bindRowActions, countRecipeMaterials };
  };
})();
