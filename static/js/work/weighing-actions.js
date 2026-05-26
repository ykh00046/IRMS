/**
 * weighing-actions module — 계량 모드 진입/종료, 큐 조회, 진행/되돌림.
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.createWeighingActions(ctx)
 * Returns: { open, close, loadQueue, advance, undo, isOpen }
 * ctx deps:
 *   - ctx.dom.{weighingMode, weighingModeLabel, liquidColorPicker}
 *   - ctx.state.weighing
 *   - ctx.weighingRender (lazy: render/syncControls/resetProgress)
 *   - ctx.onRefreshTable (lazy: () => Promise<void>)
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  NS.work.createWeighingActions = function (ctx) {
    const { dom, state } = ctx;
    const weighing = state.weighing;

    async function loadQueue(options = {}) {
      const resetProgressFlag = Boolean(options.resetProgress);
      const notifySummary = Boolean(options.notifySummary);
      const selectedGroup = (weighing.colorGroup || "all").trim();

      weighing.loading = true;
      ctx.weighingRender.syncControls();

      try {
        const payload = await IRMS.getWeighingQueue(selectedGroup);
        weighing.colorGroup = payload.colorGroup;
        weighing.queue = payload.items;

        if (resetProgressFlag) {
          ctx.weighingRender.resetProgress(payload.summary.totalSteps);
        } else if (weighing.initialTotal === 0) {
          weighing.initialTotal = payload.summary.totalSteps;
        } else {
          const dynamicTotal = payload.summary.totalSteps + weighing.doneCount;
          if (dynamicTotal > weighing.initialTotal) {
            weighing.initialTotal = dynamicTotal;
          }
        }

        if (notifySummary) {
          IRMS.notify(
            `계량 큐 ${payload.summary.totalSteps}건 / 레시피 ${payload.summary.recipeCount}건`,
            "info"
          );
        }
      } catch (error) {
        IRMS.notify(`계량 큐 조회 실패: ${error.message}`, "error");
      } finally {
        weighing.loading = false;
        if (weighing.open) {
          ctx.weighingRender.render();
        }
        ctx.weighingRender.syncControls();
      }
    }

    function open(colorGroup, modeLabel) {
      if (!dom.weighingMode) return;
      weighing.colorGroup = colorGroup;

      if (dom.weighingModeLabel) {
        dom.weighingModeLabel.textContent = modeLabel || "";
      }

      weighing.open = true;
      dom.weighingMode.classList.add("active");
      dom.weighingMode.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
      if (dom.liquidColorPicker) dom.liquidColorPicker.hidden = true;
      loadQueue({ resetProgress: true });
    }

    function close() {
      if (!dom.weighingMode) {
        return;
      }
      weighing.open = false;
      dom.weighingMode.classList.remove("active");
      dom.weighingMode.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
    }

    async function advance() {
      if (!weighing.open || weighing.loading || weighing.advancing) {
        return;
      }

      weighing.advancing = true;
      ctx.weighingRender.syncControls();

      try {
        if (weighing.pendingRecipeCompletion) {
          const pendingRecipe = weighing.pendingRecipeCompletion;
          await IRMS.completeWeighingRecipe(pendingRecipe.recipeId);
          weighing.pendingRecipeCompletion = null;
          IRMS.notify(
            `${pendingRecipe.productName} (${pendingRecipe.inkName}) 완료 처리되었습니다.`,
            "success"
          );
          if (typeof ctx.onRefreshTable === "function") {
            await ctx.onRefreshTable();
          }
          await loadQueue();
          ctx.weighingRender.render();
          return;
        }

        const current = weighing.queue[0];
        if (!current) {
          IRMS.notify("남은 계량 항목이 없습니다.", "info");
          return;
        }

        const stepResult = await IRMS.completeWeighingStep(
          current.recipeId,
          current.materialId,
          current.recipeItemId
        );

        weighing.queue.shift();
        weighing.doneCount += 1;
        weighing.lastCompleted = {
          recipeId: current.recipeId,
          recipeItemId: current.recipeItemId,
          materialId: current.materialId,
          materialName: current.materialName,
          productName: current.productName,
          inkName: current.inkName,
        };

        if (stepResult.ready_for_recipe_completion) {
          weighing.pendingRecipeCompletion = current;
          IRMS.notify(
            `${current.productName} 계량 스텝 완료. Enter/Space로 레시피 완료를 확정하세요.`,
            "info"
          );
        } else {
          IRMS.notify(`${current.materialName} 계량 완료`, "success");
        }

        if (typeof ctx.onRefreshTable === "function") {
          await ctx.onRefreshTable();
        }
        ctx.weighingRender.render();
      } catch (error) {
        IRMS.notify(`계량 처리 실패: ${error.message}`, "error");
      } finally {
        weighing.advancing = false;
        ctx.weighingRender.syncControls();
      }
    }

    async function undo() {
      if (!weighing.open || weighing.loading || weighing.advancing || weighing.undoing) {
        return;
      }
      if (!weighing.lastCompleted) {
        IRMS.notify("되돌릴 수 있는 스텝이 없습니다.", "info");
        return;
      }

      weighing.undoing = true;
      ctx.weighingRender.syncControls();

      try {
        const target = weighing.lastCompleted;
        await IRMS.undoWeighingStep(target.recipeId, target.materialId, target.recipeItemId);
        weighing.lastCompleted = null;
        if (weighing.doneCount > 0) {
          weighing.doneCount -= 1;
        }
        weighing.pendingRecipeCompletion = null;
        IRMS.notify(`${target.materialName} 되돌림 완료`, "success");
        await loadQueue();
        if (typeof ctx.onRefreshTable === "function") {
          await ctx.onRefreshTable();
        }
        ctx.weighingRender.render();
      } catch (error) {
        IRMS.notify(`되돌리기 실패: ${error.message}`, "error");
      } finally {
        weighing.undoing = false;
        ctx.weighingRender.syncControls();
      }
    }

    function isOpen() {
      return weighing.open;
    }

    return { open, close, loadQueue, advance, undo, isOpen };
  };
})();
