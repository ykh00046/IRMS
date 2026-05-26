/**
 * weighing-render module — 계량 패널 시각 상태 렌더 + 컨트롤 동기화.
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.createWeighingRender(ctx)
 * Returns: { render, syncControls, resetProgress, getQueueColorCounts }
 * ctx deps:
 *   - ctx.dom.weighing* (모든 패널 요소 + 진행/되돌림/리프레시 버튼 + currentCard)
 *   - ctx.state.weighing
 *   - ctx.state.lowStockSet (Set<number>)
 *   - ctx.colorLabel (= IRMS.colorLabel)
 *
 * `getQueueColorCounts`는 순수 함수로 노출(테스트용).
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
        { black: 0, red: 0, blue: 0, yellow: 0, none: 0 }
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
        `남은 스텝 ${weighing.queue.length}`,
        `남은 레시피 ${queueRecipeCount}`,
        `Black ${queueCounts.black}`,
        `Red ${queueCounts.red}`,
        `Blue ${queueCounts.blue}`,
        `Yellow ${queueCounts.yellow}`,
        `기타 ${queueCounts.none}`,
      ];
      if (weighing.pendingRecipeCompletion) {
        chips.unshift("레시피 완료 대기 1");
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
        dom.weighingMaterialName.textContent = "모든 계량 완료";
        dom.weighingTargetValue.textContent = "완료 처리";
        dom.weighingActionHint.textContent =
          "Enter 또는 Space를 눌러 레시피 완료를 확정하고 다음 계량으로 이동하세요.";
        const nextStep = weighing.queue[0];
        dom.weighingNextValue.textContent = nextStep
          ? `${nextStep.materialName} · ${nextStep.targetValue} (${nextStep.productName})`
          : "다음 계량 없음";
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
        dom.weighingMaterialName.textContent = "대기중";
        dom.weighingTargetValue.textContent = "-";
        dom.weighingActionHint.textContent = "큐를 새로고침하거나 Esc로 계량 모드를 종료하세요.";
        dom.weighingNextValue.textContent = "-";
        syncControls();
        return;
      }

      const colorBadgeClass = current.colorGroup && current.colorGroup !== "none"
        ? `color-${current.colorGroup}` : "";
      dom.weighingStateBadge.className = `weighing-state-badge ${colorBadgeClass}`.trim();
      dom.weighingStateBadge.textContent = `${ctx.colorLabel(current.colorGroup)} STEP`;
      dom.weighingProductName.textContent = current.productName;
      dom.weighingInkLabel.textContent = current.inkName;
      dom.weighingPositionLabel.textContent = `위치: ${current.position || "-"}`;
      dom.weighingMaterialName.textContent = current.materialName;
      dom.weighingTargetValue.textContent = current.targetValue;
      dom.weighingActionHint.textContent = "Enter 또는 Space를 눌러 현재 계량을 완료 처리하세요.";

      if (dom.weighingCurrentCard) {
        dom.weighingCurrentCard.classList.toggle(
          "stock-warning-stripe",
          state.lowStockSet.has(current.materialId)
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
      if (nextStep) {
        dom.weighingNextValue.textContent = `${nextStep.materialName} · ${nextStep.targetValue} (${nextStep.productName})`;
      } else {
        dom.weighingNextValue.textContent = "현재 큐 기준 마지막 스텝입니다.";
      }
      syncControls();
    }

    return { render, syncControls, resetProgress, getQueueColorCounts };
  };
})();
