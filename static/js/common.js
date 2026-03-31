(function () {
  "use strict";

  async function request(path, options) {
    const method = options?.method || "GET";
    const query = options?.query || null;
    const body = options?.body || null;
    const responseType = options?.responseType || "json";

    const endpoint = new URL(`/api${path}`, window.location.origin);
    if (query) {
      Object.entries(query).forEach(([key, value]) => {
        if (value === undefined || value === null || value === "") {
          return;
        }
        endpoint.searchParams.set(key, String(value));
      });
    }

    const response = await fetch(endpoint, {
      method,
      credentials: "same-origin",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      let payload = null;
      try {
        payload = await response.json();
      } catch (_error) {
        payload = { detail: response.statusText };
      }
      const detail =
        payload?.detail?.message ||
        payload?.detail ||
        payload?.message ||
        `Request failed (${response.status})`;
      if (
        (response.status === 401 || response.status === 403) &&
        typeof window !== "undefined" &&
        !window.location.pathname.startsWith("/management/login") &&
        !window.location.pathname.startsWith("/weighing/select") &&
        !window.location.pathname.startsWith("/login")
      ) {
        const next = `${window.location.pathname}${window.location.search}`;
        const isManagementPath =
          window.location.pathname.startsWith("/management") ||
          window.location.pathname.startsWith("/insight") ||
          window.location.pathname.startsWith("/status") ||
          window.location.pathname.startsWith("/base") ||
          window.location.pathname.startsWith("/admin");
        const target = isManagementPath ? "/management/login" : "/weighing/select";
        window.location.assign(`${target}?next=${encodeURIComponent(next)}`);
      }
      throw new Error(String(detail));
    }

    if (responseType === "blob") {
      return response.blob();
    }
    if (responseType === "text") {
      return response.text();
    }
    return response.json();
  }

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

  async function login(username, password) {
    const payload = await request("/auth/login", {
      method: "POST",
      body: {
        username,
        password,
      },
    });
    return mapUser(payload.user);
  }

  async function loginManager(username, password) {
    const payload = await request("/auth/management-login", {
      method: "POST",
      body: {
        username,
        password,
      },
    });
    return mapUser(payload.user);
  }

  async function selectOperator(userId) {
    const payload = await request("/auth/operator-select", {
      method: "POST",
      body: {
        user_id: userId,
      },
    });
    return mapUser(payload.user);
  }

  async function logout() {
    return request("/auth/logout", {
      method: "POST",
    });
  }

  async function getCurrentUser() {
    const payload = await request("/auth/me");
    return mapUser(payload.user);
  }

  async function listUsers() {
    const payload = await request("/admin/users");
    return {
      items: (payload.items || []).map(mapAdminUser),
      summary: payload.summary || {},
      total: Number(payload.total || 0),
    };
  }

  async function createUser(user) {
    const payload = await request("/admin/users", {
      method: "POST",
      body: {
        username: user.username,
        display_name: user.displayName,
        access_level: user.accessLevel,
        password: user.password,
      },
    });
    return mapAdminUser(payload.user);
  }

  async function updateUser(userId, user) {
    const payload = await request(`/admin/users/${userId}`, {
      method: "PATCH",
      body: {
        display_name: user.displayName,
        access_level: user.accessLevel,
        is_active: user.isActive,
      },
    });
    return mapAdminUser(payload.user);
  }

  async function resetUserPassword(userId, password) {
    return request(`/admin/users/${userId}/password`, {
      method: "POST",
      body: { password },
    });
  }

  async function listAuditLogs(filters) {
    const payload = await request("/admin/audit-logs", {
      query: {
        limit: filters?.limit,
        action: filters?.action,
      },
    });
    return {
      items: (payload.items || []).map(mapAuditLog),
      total: Number(payload.total || 0),
    };
  }

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

  async function listChatRooms() {
    const payload = await request("/chat/rooms");
    return {
      items: (payload.items || []).map(mapChatRoom),
      total: Number(payload.total || 0),
    };
  }

  async function getChatMessages(filters) {
    const payload = await request("/chat/messages", {
      query: {
        room_key: filters?.roomKey,
        limit: filters?.limit,
        after_id: filters?.afterId,
      },
    });
    return {
      room: payload.room ? mapChatRoom(payload.room) : null,
      items: (payload.items || []).map(mapChatMessage),
      total: Number(payload.total || 0),
      latestId: Number(payload.latest_id || 0),
    };
  }

  async function postChatMessage(message) {
    const payload = await request("/chat/messages", {
      method: "POST",
      body: {
        room_key: message.roomKey,
        message_text: message.messageText,
        stage: message.stage || null,
      },
    });
    return {
      room: payload.room ? mapChatRoom(payload.room) : null,
      message: payload.message ? mapChatMessage(payload.message) : null,
    };
  }

  async function getMaterials() {
    const payload = await request("/materials");
    return (payload.items || []).map(mapMaterial);
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

  async function previewImport(rawText, createdBy) {
    const payload = await request("/recipes/import/preview", {
      method: "POST",
      body: {
        raw_text: rawText,
        created_by: createdBy || "관리자",
      },
    });
    return mapPreview(payload);
  }

  async function importRecipes(rawText, createdBy) {
    return request("/recipes/import", {
      method: "POST",
      body: {
        raw_text: rawText,
        created_by: createdBy || "관리자",
      },
    });
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
      materialId: row.material_id,
      materialName: row.material_name,
      unitType: row.unit_type,
      unit: row.unit,
      colorGroup: row.color_group || "none",
      targetValue: row.target_value,
    };
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

  async function completeWeighingStep(recipeId, materialId, measuredBy) {
    return request("/weighing/step/complete", {
      method: "POST",
      body: {
        recipe_id: recipeId,
        material_id: materialId,
        measured_by: measuredBy || "작업자",
      },
    });
  }

  async function completeWeighingRecipe(recipeId) {
    const payload = await request("/weighing/recipe/complete", {
      method: "POST",
      body: { recipe_id: recipeId },
    });
    return mapRecipe(payload);
  }

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

  function statusLabel(status) {
    const map = {
      pending: "대기",
      in_progress: "진행",
      completed: "완료",
      canceled: "취소",
      draft: "초안",
    };
    return map[status] || status;
  }

  function statusClass(status) {
    return `status-${status}`;
  }

  function formatDateTime(value) {
    if (!value) {
      return "-";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "-";
    }
    return date.toLocaleString("ko-KR", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function toDateOnly(value) {
    if (!value) {
      return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return "";
    }
    const year = date.getFullYear();
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${year}-${month}-${day}`;
  }

  function formatValue(value) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) {
      return numeric.toLocaleString("ko-KR", {
        minimumFractionDigits: numeric % 1 === 0 ? 0 : 2,
        maximumFractionDigits: 2,
      });
    }
    return String(value ?? "-");
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function debounce(fn, delay) {
    let timer = null;
    return function (...args) {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => { fn.apply(this, args); }, delay);
    };
  }

  function loadPreference(key, fallbackValue) {
    try {
      const value = window.localStorage.getItem(key);
      return value === null ? fallbackValue : value;
    } catch (_error) {
      return fallbackValue;
    }
  }

  function savePreference(key, value) {
    try {
      if (value === undefined || value === null || value === "") {
        window.localStorage.removeItem(key);
        return;
      }
      window.localStorage.setItem(key, String(value));
    } catch (_error) {
      // Ignore storage failures to keep workflows usable in restricted browsers.
    }
  }

  function clearPreference(key) {
    try {
      window.localStorage.removeItem(key);
    } catch (_error) {
      // Ignore storage failures to keep workflows usable in restricted browsers.
    }
  }

  function notify(message, type) {
    const root =
      document.getElementById("toast-root") ||
      document.querySelector(".toast-container");
    if (!root) {
      return;
    }
    const node = document.createElement("div");
    node.className = `toast ${type || "info"}`;
    node.textContent = message;
    root.appendChild(node);
    window.setTimeout(() => {
      node.remove();
    }, 2800);
  }

  function bindLogoutButton() {
    const logoutBtn = document.getElementById("logout-btn");
    if (!logoutBtn || logoutBtn.dataset.bound === "true") {
      return;
    }

    logoutBtn.dataset.bound = "true";
    logoutBtn.addEventListener("click", async () => {
      logoutBtn.disabled = true;
      try {
        await logout();
        window.location.assign("/");
      } catch (error) {
        notify(`로그아웃 실패: ${error.message}`, "error");
        logoutBtn.disabled = false;
      }
    });
  }

  window.IRMS = {
    login,
    loginManager,
    selectOperator,
    logout,
    getCurrentUser,
    listUsers,
    createUser,
    updateUser,
    resetUserPassword,
    listAuditLogs,
    listChatRooms,
    getChatMessages,
    postChatMessage,
    getRecipeImportNotifications,
    getRecipeProgress,
    getMaterials,
    getRecipes,
    updateRecipeStatus,
    previewImport,
    importRecipes,
    getWeighingQueue,
    completeWeighingStep,
    completeWeighingRecipe,
    getStats,
    exportStatsCsv,
    statusLabel,
    statusClass,
    formatDateTime,
    toDateOnly,
    formatValue,
    escapeHtml,
    debounce,
    loadPreference,
    savePreference,
    clearPreference,
    notify,
  };

  bindLogoutButton();
})();
