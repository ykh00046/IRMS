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
 *   deleteRecipe(recipeId, deleteBlendRecords, { moveRecordsTo }?) — 3번째 인자는 선택.
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

  // reason: 취소 사유(선택) — 서버가 recipes.cancel_reason 과 감사로그에 남긴다.
  // 예전에는 UI 가 보내지 않아 사유가 항상 비어 있었다.
  async function updateRecipeStatus(recipeId, action, reason) {
    const payload = await request(`/recipes/${recipeId}/status`, {
      method: "PATCH",
      body: reason ? { action, reason } : { action },
    });
    return mapRecipe(payload);
  }

  // options.moveRecordsTo: 연결 기록을 같은 체인의 이 판 id 로 옮기고 삭제(삭제 플래그와 배타).
  async function deleteRecipe(recipeId, deleteBlendRecords, options) {
    const moveTo = options && options.moveRecordsTo;
    return request(`/recipes/${recipeId}`, {
      method: "DELETE",
      query: {
        delete_blend_records: deleteBlendRecords ? 1 : undefined,
        move_records_to: moveTo ? moveTo : undefined,
      },
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

  // 사용 시작일(effective_from)은 UI 에서 제거 — 서버가 등록일로 자동 기록.
  async function importRecipes(rawText, createdBy, revisionOf, baseTotals) {
    const body = {
      raw_text: rawText,
      created_by: createdBy || "책임자",
    };
    if (revisionOf != null) {
      body.revision_of = revisionOf;
    }
    if (Array.isArray(baseTotals) && baseTotals.length) {
      body.base_totals = baseTotals.slice(0, 3);
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
