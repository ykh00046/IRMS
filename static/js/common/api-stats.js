/**
 * api-stats.js — Material consumption statistics + CSV export.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   getStats, exportStatsCsv
 *
 * Side effects: none.
 * Dependencies: core.js (uses IRMS._core.request).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;

  async function getStats(filters) {
    const payload = await request("/stats/consumption", {
      query: {
        date_from: filters?.dateFrom,
        date_to: filters?.dateTo,
        color_group: filters?.colorGroup,
        category: filters?.category,
      },
    });

    return {
      period: payload.period,
      summary: {
        completedRecipes: Number(payload?.summary?.completed_recipes || 0),
        activeMaterials: Number(payload?.summary?.active_materials || 0),
        totalWeight: Number(payload?.summary?.total_weight || 0),
        totalCount: Number(payload?.summary?.total_count || 0),
      },
      items: (payload.items || []).map((row) => ({
        materialId: row.material_id,
        materialName: row.material_name,
        unitType: row.unit_type,
        unit: row.unit,
        colorGroup: row.color_group,
        category: row.category,
        totalWeight: Number(row.total_weight || 0),
        totalCount: Number(row.total_count || 0),
        recipeCount: Number(row.recipe_count || 0),
      })),
    };
  }

  function exportStatsCsv(filters) {
    const params = new URLSearchParams();
    params.set("date_from", filters.dateFrom);
    params.set("date_to", filters.dateTo);
    if (filters.colorGroup) {
      params.set("color_group", filters.colorGroup);
    }
    if (filters.category) {
      params.set("category", filters.category);
    }
    window.location.href = `/api/stats/export?${params.toString()}`;
  }

  Object.assign(IRMS, { getStats, exportStatsCsv });
})();
