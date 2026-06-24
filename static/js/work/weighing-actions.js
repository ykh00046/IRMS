/**
 * Weighing panel actions.
 *
 * Factory: IRMS.work.createWeighingActions(ctx)
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
            `Weighing queue ${payload.summary.totalSteps} steps / ${payload.summary.recipeCount} recipes`,
            "info",
          );
        }
      } catch (error) {
        IRMS.notify(`Failed to load weighing queue: ${error.message}`, "error");
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

    function readActualWeight() {
      const raw = dom.weighingActualWeight ? dom.weighingActualWeight.value.trim() : "";
      if (raw === "") return null;
      const value = Number(raw);
      if (!Number.isFinite(value) || value < 0) {
        throw new Error("Actual weight must be zero or greater.");
      }
      return value;
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
            `${pendingRecipe.productName} (${pendingRecipe.inkName}) completed.`,
            "success",
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
          IRMS.notify("No weighing steps remain.", "info");
          return;
        }

        let actualWeight = null;
        try {
          actualWeight = readActualWeight();
        } catch (error) {
          IRMS.notify(error.message, "warn");
          return;
        }

        const stepResult = await IRMS.completeWeighingStep(
          current.recipeId,
          current.materialId,
          current.recipeItemId,
          actualWeight,
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

        if (dom.weighingActualWeight) {
          dom.weighingActualWeight.value = "";
        }

        if (stepResult.ready_for_recipe_completion) {
          weighing.pendingRecipeCompletion = current;
          IRMS.notify(
            `${current.productName} weighing steps complete. Press Enter/Space to complete recipe.`,
            "info",
          );
        } else {
          IRMS.notify(`${current.materialName} weighing completed`, "success");
        }

        if (typeof ctx.onRefreshTable === "function") {
          await ctx.onRefreshTable();
        }
        ctx.weighingRender.render();
      } catch (error) {
        IRMS.notify(`Failed to complete weighing step: ${error.message}`, "error");
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
        IRMS.notify("No completed step to undo.", "info");
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
        IRMS.notify(`${target.materialName} undo completed`, "success");
        await loadQueue();
        if (typeof ctx.onRefreshTable === "function") {
          await ctx.onRefreshTable();
        }
        ctx.weighingRender.render();
      } catch (error) {
        IRMS.notify(`Failed to undo weighing step: ${error.message}`, "error");
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
