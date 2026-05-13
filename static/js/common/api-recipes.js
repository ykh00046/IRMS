/**
 * api-recipes.js — Recipe queries, status updates, import, products, history.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   getRecipeImportNotifications, getRecipeProgress, getOperatorProgress,
 *   getRecipes, updateRecipeStatus, deleteRecipe, previewImport,
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

  async function getRecipeProgress(filters) {
    const payload = await request("/recipes/progress", {
      query: {
        status_filter: filters?.statusFilter || "active",
      },
    });

    return {
      statusFilter: payload.status_filter || "active",
      summary: payload.summary || {},
      items: (payload.items || []).map((row) => ({
        id: row.id,
        productName: row.product_name,
        position: row.position,
        inkName: row.ink_name,
        status: row.status,
        createdBy: row.created_by,
        createdAt: row.created_at,
        completedAt: row.completed_at,
        startedBy: row.started_by,
        startedAt: row.started_at,
        totalSteps: Number(row.total_steps || 0),
        completedSteps: Number(row.completed_steps || 0),
        remainingSteps: Number(row.remaining_steps || 0),
        progressPct: Number(row.progress_pct || 0),
        nextItem: row.next_item
          ? {
              materialName: row.next_item.material_name,
              unit: row.next_item.unit,
              colorGroup: row.next_item.color_group,
              targetValue: row.next_item.target_value,
            }
          : null,
        remainingMaterials: row.remaining_materials || [],
        lastCompletedItem: row.last_completed_item
          ? {
              materialName: row.last_completed_item.material_name,
              measuredAt: row.last_completed_item.measured_at,
              measuredBy: row.last_completed_item.measured_by,
            }
          : null,
      })),
    };
  }

  async function getOperatorProgress() {
    const payload = await request("/recipes/operator-progress");
    return {
      date: payload.date,
      totalOperators: Number(payload.total_operators || 0),
      operators: (payload.operators || []).map((op) => ({
        name: op.name,
        completedSteps: Number(op.completed_steps || 0),
        totalSteps: Number(op.total_steps || 0),
        progressPct: Number(op.progress_pct || 0),
        lastMeasuredAt: op.last_measured_at,
        currentRecipe: op.current_recipe
          ? {
              recipeId: op.current_recipe.recipe_id,
              productName: op.current_recipe.product_name,
              inkName: op.current_recipe.ink_name,
              position: op.current_recipe.position,
            }
          : null,
        categorySummary: (op.category_summary || []).map((c) => ({
          category: c.category,
          completed: Number(c.completed || 0),
          total: Number(c.total || 0),
        })),
        workedRecipes: (op.worked_recipes || []).map((w) => ({
          productName: w.product_name,
          count: Number(w.count || 0),
        })),
      })),
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

  async function deleteRecipe(recipeId) {
    return request(`/recipes/${recipeId}`, { method: "DELETE" });
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

  async function importRecipes(rawText, createdBy, revisionOf) {
    const body = {
      raw_text: rawText,
      created_by: createdBy || "책임자",
    };
    if (revisionOf != null) {
      body.revision_of = revisionOf;
    }
    return request("/recipes/import", {
      method: "POST",
      body,
    });
  }

  async function getProducts() {
    const payload = await request("/recipes/products");
    return payload.items || [];
  }

  async function getRecipesByProduct(productName, limit) {
    const query = { product_name: productName };
    if (limit) query.limit = limit;
    const payload = await request("/recipes/by-product", { query });
    return payload;
  }

  async function getRecipeDetail(recipeId) {
    return request(`/recipes/${recipeId}/detail`);
  }

  Object.assign(IRMS, {
    getRecipeImportNotifications,
    getRecipeProgress,
    getOperatorProgress,
    getRecipes,
    updateRecipeStatus,
    deleteRecipe,
    previewImport,
    importRecipes,
    getProducts,
    getRecipesByProduct,
    getRecipeDetail,
  });
})();
