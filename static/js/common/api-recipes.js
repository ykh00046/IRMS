/**
 * api-recipes.js — Recipe queries, status updates, import, products, history.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   getRecipeImportNotifications,  getRecipes, updateRecipeStatus, deleteRecipe, previewImport,
 *   importRecipes, getProducts, getRecipesByProduct, getRecipeDetail
 *
 * Side effects: none.
 * Dependencies: core.js, mappers.js.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;
  const { mapAuditLog, mapRecipe, mapPreview } = IRMS._mappers;

  async function getRecipeImportNotifications(filters) {
    const payload = await request("/notifications/recipe-imports", {
      query: {
        after_id: filters?.afterId,
        limit: filters?.limit,
        latest: filters?.latest,
      },
    });
    return {
      items: (payload.items || []).map(mapAuditLog),
      total: Number(payload.total || 0),
      latestId: Number(payload.latest_id || 0),
    };
  }

  async function getRecipes(filters) {
    const query = {
      status: filters?.status,
      search: filters?.search,
      date_from: filters?.dateFrom,
      date_to: filters?.dateTo,
    };
    const payload = await request("/recipes", { query });
    return (payload.items || []).map(mapRecipe);
  }

  async function updateRecipeStatus(recipeId, action) {
    const payload = await request(`/recipes/${recipeId}/status`, {
      method: "PATCH",
      body: { action },
    });
    return mapRecipe(payload);
  }

  async function deleteRecipe(recipeId, deleteBlendRecords) {
    return request(`/recipes/${recipeId}`, {
      method: "DELETE",
      query: { delete_blend_records: deleteBlendRecords ? 1 : undefined },
    });
  }

  async function previewImport(rawText, createdBy) {
    const payload = await request("/recipes/import/preview", {
      method: "POST",
      body: {
        raw_text: rawText,
        created_by: createdBy || "책임자",
      },
    });
    return mapPreview(payload);
  }

  async function importRecipes(rawText, createdBy, revisionOf, effectiveFrom, baseTotal) {
    const body = {
      raw_text: rawText,
      created_by: createdBy || "책임자",
    };
    if (revisionOf != null) {
      body.revision_of = revisionOf;
    }
    if (effectiveFrom) {
      body.effective_from = effectiveFrom;
    }
    if (baseTotal != null && Number(baseTotal) > 0) {
      body.base_total = Number(baseTotal);
    }
    return request("/recipes/import", {
      method: "POST",
      body,
    });
  }

  async function getProducts(dhr) {
    const query = dhr ? { dhr: 1 } : {};
    const payload = await request("/recipes/products", { query });
    return payload.items || [];
  }

  async function getRecipesByProduct(productName, limit, dhr) {
    const query = { product_name: productName };
    if (limit) query.limit = limit;
    if (dhr) query.dhr = 1;
    const payload = await request("/recipes/by-product", { query });
    return payload;
  }

  async function setRecipeDhr(recipeId, isDhr) {
    return request(`/recipes/${recipeId}/dhr`, { method: "PATCH", body: { is_dhr: !!isDhr } });
  }

  async function getRecipeDetail(recipeId) {
    return request(`/recipes/${recipeId}/detail`);
  }

  Object.assign(IRMS, {
    getRecipeImportNotifications,
    getRecipes,
    updateRecipeStatus,
    deleteRecipe,
    previewImport,
    importRecipes,
    getProducts,
    getRecipesByProduct,
    setRecipeDhr,
    getRecipeDetail,
  });
})();
