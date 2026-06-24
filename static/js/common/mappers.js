/**
 * mappers.js — API response row → normalized object mappers (internal).
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). See docs/01-plan/features/split-common-js.plan.md.
 *
 * Internal namespace (IRMS._mappers):
 *   mapUser, mapAdminUser, mapAuditLog, mapChatRoom, mapChatMessage,
 *   mapMaterial, mapRecipe, mapPreview, mapWeighingStep
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

  function mapAdminUser(row) {
    return {
      id: row.id,
      username: row.username,
      displayName: row.display_name,
      role: row.role,
      roleLabel: row.role_label,
      accessLevel: row.access_level,
      isActive: Boolean(row.is_active),
      createdAt: row.created_at,
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

  function mapChatRoom(row) {
    return {
      key: row.key,
      name: row.name,
      scope: row.scope,
      sortOrder: Number(row.sort_order || 0),
      isActive: Boolean(row.is_active),
      messageCount: Number(row.message_count || 0),
      latestMessageAt: row.latest_message_at,
      stageRequired: Boolean(row.stage_required),
      stageOptions: row.stage_options || [],
    };
  }

  function mapChatMessage(row) {
    return {
      id: Number(row.id || 0),
      roomKey: row.room_key,
      messageText: row.message_text,
      stage: row.stage,
      createdByUserId: row.created_by_user_id,
      createdByUsername: row.created_by_username,
      createdByDisplayName: row.created_by_display_name,
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

  function mapWeighingStep(row) {
    return {
      sequence: Number(row.sequence || 0),
      recipeId: row.recipe_id,
      productName: row.product_name,
      position: row.position,
      inkName: row.ink_name,
      recipeStatus: row.recipe_status,
      createdAt: row.created_at,
      recipeItemId: row.recipe_item_id,
      materialId: row.material_id,
      materialName: row.material_name,
      unitType: row.unit_type,
      unit: row.unit,
      colorGroup: row.color_group || "none",
      targetValue: row.target_value,
      targetWeight: row.value_weight === null || row.value_weight === undefined ? null : Number(row.value_weight),
      actualWeight: row.actual_weight === null || row.actual_weight === undefined ? null : Number(row.actual_weight),
    };
  }

  IRMS._mappers = {
    mapUser,
    mapAdminUser,
    mapAuditLog,
    mapChatRoom,
    mapChatMessage,
    mapMaterial,
    mapRecipe,
    mapPreview,
    mapWeighingStep,
  };
})();
