/**
 * mappers.js — API response row → normalized object mappers (internal).
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). See docs/01-plan/features/split-common-js.plan.md.
 *
 * Internal namespace (IRMS._mappers):
 *   mapUser, mapAuditLog, mapMaterial, mapRecipe, mapPreview
 *
 * Side effects: none.
 * Dependencies: core.js (for window.IRMS namespace).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

  function mapUser(row) {
    if (!row) {
      return null;
    }
    return {
      id: row.id,
      username: row.username,
      displayName: row.display_name,
      role: row.role,
      roleLabel: row.role_label,
      accessLevel: row.access_level,
    };
  }

  function mapAuditLog(row) {
    return {
      id: row.id,
      action: row.action,
      actorUserId: row.actor_user_id,
      actorUsername: row.actor_username,
      actorDisplayName: row.actor_display_name,
      actorAccessLevel: row.actor_access_level,
      targetType: row.target_type,
      targetId: row.target_id,
      targetLabel: row.target_label,
      details: row.details || {},
      createdAt: row.created_at,
    };
  }

  function mapMaterial(row) {
    return {
      id: row.id,
      name: row.name,
      unitType: row.unit_type,
      unit: row.unit,
      colorGroup: row.color_group,
      category: row.category,
      aliases: row.aliases || [],
    };
  }

  function mapRecipe(row) {
    return {
      id: row.id,
      productName: row.product_name,
      position: row.position,
      inkName: row.ink_name,
      status: row.status,
      isDhr: !!row.is_dhr,
      createdBy: row.created_by,
      createdAt: row.created_at,
      completedAt: row.completed_at,
      items: (row.items || []).map((item) => ({
        materialId: item.material_id,
        materialName: item.material_name,
        unitType: item.unit_type,
        unit: item.unit,
        colorGroup: item.color_group,
        value: item.value,
      })),
    };
  }

  function mapPreview(result) {
    const rows = (result?.preview?.rows || []).map((row) => ({
      productName: row.product_name,
      position: row.position,
      inkName: row.ink_name,
      items: (row.items || []).map((item) => ({
        materialId: item.material_id,
        materialName: item.material_name,
        value: item.value,
      })),
    }));

    return {
      status: result.status,
      errors: result.errors || [],
      warnings: result.warnings || [],
      rows,
    };
  }

  IRMS._mappers = {
    mapUser,
    mapAuditLog,
    mapMaterial,
    mapRecipe,
    mapPreview,
  };
})();
