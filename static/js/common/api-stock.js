/**
 * api-stock.js — Materials + weighing endpoints (operator scope).
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). getMaterials lives here (not api-recipes) because materials
 * are stock-master data; see design §4.1 for rationale.
 *
 * Exports (window.IRMS.*):
 *   getMaterials, getWeighingQueue, completeWeighingStep,
 *   undoWeighingStep, completeWeighingRecipe, resetWeighingRecipe
 *
 * Side effects: none.
 * Dependencies: core.js, mappers.js.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;
  const { mapMaterial, mapRecipe, mapWeighingStep } = IRMS._mappers;

  async function getMaterials() {
    const payload = await request("/materials");
    return (payload.items || []).map(mapMaterial);
  }

  async function getWeighingQueue(colorGroup) {
    const payload = await request("/weighing/queue", {
      query: { color_group: colorGroup || "all" },
    });
    return {
      colorGroup: payload.color_group || "all",
      summary: {
        totalSteps: Number(payload?.summary?.total_steps || 0),
        recipeCount: Number(payload?.summary?.recipe_count || 0),
        byColor: payload?.summary?.by_color || {},
      },
      items: (payload.items || []).map(mapWeighingStep),
    };
  }

  async function completeWeighingStep(recipeId, materialId, recipeItemId, actualWeight) {
    const body = { recipe_id: recipeId };
    if (recipeItemId) {
      body.recipe_item_id = recipeItemId;
    } else {
      body.material_id = materialId;
    }
    if (actualWeight !== null && actualWeight !== undefined && actualWeight !== "") {
      body.actual_weight = Number(actualWeight);
    }
    return request("/weighing/step/complete", {
      method: "POST",
      body,
    });
  }

  async function undoWeighingStep(recipeId, materialId, recipeItemId) {
    const body = { recipe_id: recipeId };
    if (recipeItemId) {
      body.recipe_item_id = recipeItemId;
    } else {
      body.material_id = materialId;
    }
    return request("/weighing/step/undo", {
      method: "POST",
      body,
    });
  }

  async function completeWeighingRecipe(recipeId) {
    const payload = await request("/weighing/recipe/complete", {
      method: "POST",
      body: { recipe_id: recipeId },
    });
    return mapRecipe(payload);
  }

  async function resetWeighingRecipe(recipeId) {
    return request("/weighing/recipe/reset", {
      method: "POST",
      body: { recipe_id: recipeId },
    });
  }

  Object.assign(IRMS, {
    getMaterials,
    getWeighingQueue,
    completeWeighingStep,
    undoWeighingStep,
    completeWeighingRecipe,
    resetWeighingRecipe,
  });
})();
