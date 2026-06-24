/**
 * Weighing panel renderer.
 *
 * Factory: IRMS.work.createWeighingRender(ctx)
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  NS.work.createWeighingRender = function (ctx) {
    const { dom, state } = ctx;
    const weighing = state.weighing;

    function resetProgress(totalSteps) {
      weighing.doneCount = 0;
      weighing.initialTotal = Number(totalSteps || 0);
      weighing.pendingRecipeCompletion = null;
      weighing.lastCompleted = null;
    }

    function getQueueColorCounts(queue) {
      return queue.reduce(
        (acc, item) => {
          const group = item.colorGroup || "none";
          const key = Object.prototype.hasOwnProperty.call(acc, group) ? group : "none";
          acc[key] += 1;
          return acc;
        },
        { black: 0, red: 0, blue: 0, yellow: 0, none: 0 },
      );
    }

    function syncControls() {
      const busy = weighing.loading || weighing.advancing || weighing.undoing;
      const hasAction = Boolean(weighing.pendingRecipeCompletion) || weighing.queue.length > 0;
      if (dom.weighingAdvanceBtn) {
        dom.weighingAdvanceBtn.disabled = busy || !hasAction;
      }
      if (dom.weighingUndoBtn) {
        dom.weighingUndoBtn.disabled = busy || !weighing.lastCompleted;
      }
      if (dom.weighingRefreshBtn) {
        dom.weighingRefreshBtn.disabled = busy;
      }
    }

    function setActualInputEnabled(enabled, targetWeight) {
      if (!dom.weighingActualWeight) return;
      dom.weighingActualWeight.disabled = !enabled;
      dom.weighingActualWeight.value = "";
      dom.weighingActualWeight.placeholder =
        enabled && targetWeight !== null && targetWeight !== undefined
          ? String(targetWeight)
          : "blank = target";
    }

    function render() {
      if (!dom.weighingStateBadge) {
        return;
      }

      const total = Math.max(weighing.initialTotal, weighing.doneCount + weighing.queue.length);
      const progressRatio = total > 0 ? weighing.doneCount / total : 0;
      if (dom.weighingProgressFill) {
        dom.weighingProgressFill.style.width = `${Math.max(0, Math.min(100, progressRatio * 100))}%`;
      }
      if (dom.weighingProgressText) {
        dom.weighingProgressText.textContent = `${weighing.doneCount} / ${total}`;
      }

      const queueCounts = getQueueColorCounts(weighing.queue);
      const queueRecipeCount = new Set(weighing.queue.map((item) => item.recipeId)).size;
      const chips = [
        `Steps ${weighing.queue.length}`,
        `Recipes ${queueRecipeCount}`,
        `Black ${queueCounts.black}`,
        `Red ${queueCounts.red}`,
        `Blue ${queueCounts.blue}`,
        `Yellow ${queueCounts.yellow}`,
        `Other ${queueCounts.none}`,
      ];
      if (weighing.pendingRecipeCompletion) {
        chips.unshift("Recipe completion pending");
      }
      if (dom.weighingSummary) {
        dom.weighingSummary.innerHTML = chips
          .map((value) => `<span class="weighing-summary-chip">${value}</span>`)
          .join("");
      }

      if (weighing.pendingRecipeCompletion) {
        weighing.lastSpokenStepKey = null;
        const pending = weighing.pendingRecipeCompletion;
        dom.weighingStateBadge.className = "weighing-state-badge state-recipe";
        dom.weighingStateBadge.textContent = "RECIPE COMPLETE";
        dom.weighingProductName.textContent = pending.productName;
        dom.weighingInkLabel.textContent = pending.inkName;
        dom.weighingPositionLabel.textContent = pending.position || "-";
        dom.weighingMaterialName.textContent = "All weighing steps complete";
        dom.weighingTargetValue.textContent = "Complete recipe";
        dom.weighingActionHint.textContent = "Press Enter or Space to complete this recipe.";
        setActualInputEnabled(false);
        const nextStep = weighing.queue[0];
        dom.weighingNextValue.textContent = nextStep
          ? `${nextStep.materialName} - ${nextStep.targetValue} (${nextStep.productName})`
          : "No next step";
        syncControls();
        return;
      }

      const current = weighing.queue[0];
      if (!current) {
        weighing.lastSpokenStepKey = null;
        dom.weighingStateBadge.className = "weighing-state-badge state-idle";
        dom.weighingStateBadge.textContent = "IDLE";
        dom.weighingProductName.textContent = "-";
        dom.weighingInkLabel.textContent = "-";
        dom.weighingPositionLabel.textContent = "-";
        dom.weighingMaterialName.textContent = "Waiting";
        dom.weighingTargetValue.textContent = "-";
        dom.weighingActionHint.textContent = "Refresh or press Esc to close weighing mode.";
        dom.weighingNextValue.textContent = "-";
        setActualInputEnabled(false);
        syncControls();
        return;
      }

      const colorBadgeClass = current.colorGroup && current.colorGroup !== "none"
        ? `color-${current.colorGroup}` : "";
      dom.weighingStateBadge.className = `weighing-state-badge ${colorBadgeClass}`.trim();
      dom.weighingStateBadge.textContent = `${ctx.colorLabel(current.colorGroup)} STEP`;
      dom.weighingProductName.textContent = current.productName;
      dom.weighingInkLabel.textContent = current.inkName;
      dom.weighingPositionLabel.textContent = `Position: ${current.position || "-"}`;
      dom.weighingMaterialName.textContent = current.materialName;
      dom.weighingTargetValue.textContent = current.targetValue;
      dom.weighingActionHint.textContent = "Press Enter or Space to complete the current step.";
      setActualInputEnabled(true, current.targetWeight);

      if (dom.weighingCurrentCard) {
        dom.weighingCurrentCard.classList.toggle(
          "stock-warning-stripe",
          state.lowStockSet.has(current.materialId),
        );
      }

      const stepKey = `${current.recipeId}:${current.recipeItemId || current.materialId}`;
      if (weighing.lastSpokenStepKey !== stepKey) {
        weighing.lastSpokenStepKey = stepKey;
        const spokenValue = String(current.targetValue || "").trim();
        if (spokenValue && spokenValue !== "-") {
          IRMS.speakText(`${current.materialName}, ${spokenValue}`);
        }
      }

      const nextStep = weighing.queue[1];
      dom.weighingNextValue.textContent = nextStep
        ? `${nextStep.materialName} - ${nextStep.targetValue} (${nextStep.productName})`
        : "This is the last step in the current queue.";
      syncControls();
    }

    return { render, syncControls, resetProgress, getQueueColorCounts };
  };
})();
