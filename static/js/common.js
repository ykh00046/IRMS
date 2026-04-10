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

  async function loginOperator(username, password) {
    const payload = await request("/auth/operator-login", {
      method: "POST",
      body: {
        username,
        password,
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

  async function completeWeighingStep(recipeId, materialId) {
    return request("/weighing/step/complete", {
      method: "POST",
      body: {
        recipe_id: recipeId,
        material_id: materialId,
      },
    });
  }

  async function undoWeighingStep(recipeId, materialId) {
    return request("/weighing/step/undo", {
      method: "POST",
      body: {
        recipe_id: recipeId,
        material_id: materialId,
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

  function showLoading(el) {
    if (!el) return null;
    let overlay = el.querySelector('.loading-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'loading-overlay';
      overlay.innerHTML = '<div class="spinner"></div>';
      el.style.position = el.style.position || 'relative';
      el.appendChild(overlay);
    }
    requestAnimationFrame(() => overlay.classList.add('active'));
    return overlay;
  }

  function hideLoading(el) {
    if (!el) return;
    const overlay = el.querySelector('.loading-overlay');
    if (overlay) overlay.classList.remove('active');
  }

  function btnLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
      btn._origHTML = btn.innerHTML;
      btn.innerHTML = '<div class="spinner"></div>';
      btn.classList.add('loading');
      btn.disabled = true;
    } else {
      btn.innerHTML = btn._origHTML || btn.innerHTML;
      btn.classList.remove('loading');
      btn.disabled = false;
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

  // ── Spreadsheet editor API ──

  async function ssListProducts() {
    const data = await request("/spreadsheet/products");
    return {
      items: (data.items || []).map((p) => ({
        id: p.id,
        name: p.name,
        description: p.description,
        columnCount: p.columnCount,
        rowCount: p.rowCount,
        updatedAt: p.updatedAt,
      })),
    };
  }

  async function ssCreateProduct(body) {
    return request("/spreadsheet/products", { method: "POST", body });
  }

  async function ssUpdateProduct(productId, body) {
    return request(`/spreadsheet/products/${productId}`, { method: "PATCH", body });
  }

  async function ssDeleteProduct(productId) {
    return request(`/spreadsheet/products/${productId}`, { method: "DELETE" });
  }

  async function ssLoadSheet(productId) {
    return request(`/spreadsheet/products/${productId}/sheet`);
  }

  async function ssSaveSheet(productId, rows) {
    return request(`/spreadsheet/products/${productId}/save`, { method: "POST", body: { rows } });
  }

  async function ssAddColumn(productId, body) {
    return request(`/spreadsheet/products/${productId}/columns`, { method: "POST", body });
  }

  async function ssDeleteColumn(columnId) {
    return request(`/spreadsheet/columns/${columnId}`, { method: "DELETE" });
  }

  async function ssAddRow(productId) {
    return request(`/spreadsheet/products/${productId}/rows`, { method: "POST" });
  }

  async function ssDeleteRow(rowId) {
    return request(`/spreadsheet/rows/${rowId}`, { method: "DELETE" });
  }

  window.IRMS = {
    login,
    loginManager,
    loginOperator,
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
    getOperatorProgress,
    getMaterials,
    getRecipes,
    updateRecipeStatus,
    previewImport,
    importRecipes,
    getProducts,
    getRecipesByProduct,
    getRecipeDetail,
    getWeighingQueue,
    completeWeighingStep,
    undoWeighingStep,
    completeWeighingRecipe,
    getStats,
    exportStatsCsv,
    ssListProducts,
    ssCreateProduct,
    ssUpdateProduct,
    ssDeleteProduct,
    ssLoadSheet,
    ssSaveSheet,
    ssAddColumn,
    ssDeleteColumn,
    ssAddRow,
    ssDeleteRow,
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
    showLoading,
    hideLoading,
    btnLoading,
  };

  /**
   * Shared login form handler.
   * @param {object} opts
   * @param {string} opts.formId - form element ID
   * @param {string} opts.usernameId - username input ID
   * @param {string} opts.passwordId - password input ID
   * @param {string} opts.submitId - submit button ID
   * @param {string} opts.errorId - error display element ID
   * @param {string} opts.nextId - hidden next-url input ID
   * @param {Function} opts.loginFn - IRMS.login or IRMS.loginManager
   * @param {string} opts.defaultNext - fallback redirect URL
   * @param {string} opts.emptyMsg - message for empty fields
   * @param {string} opts.failMsg - message for invalid credentials
   */
  function bindLoginForm(opts) {
    const form = document.getElementById(opts.formId);
    const usernameInput = document.getElementById(opts.usernameId);
    const passwordInput = document.getElementById(opts.passwordId);
    const submitBtn = document.getElementById(opts.submitId);
    const errorNode = document.getElementById(opts.errorId);
    const nextInput = document.getElementById(opts.nextId || "next-url");

    function setError(message) {
      if (!errorNode) return;
      if (!message) { errorNode.hidden = true; errorNode.textContent = ""; return; }
      errorNode.hidden = false;
      errorNode.textContent = message;
    }

    form?.addEventListener("submit", async (event) => {
      event.preventDefault();
      const username = String(usernameInput?.value || "").trim();
      const password = String(passwordInput?.value || "");
      if (!username || !password) { setError(opts.emptyMsg); return; }
      setError("");
      if (submitBtn) submitBtn.disabled = true;
      try {
        await opts.loginFn(username, password);
        const nextUrl = String(nextInput?.value || opts.defaultNext);
        window.location.assign(nextUrl.startsWith("/") ? nextUrl : opts.defaultNext);
      } catch (error) {
        setError(error.message === "INVALID_CREDENTIALS" ? opts.failMsg : error.message);
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

  window.IRMS.bindLoginForm = bindLoginForm;

  function colorLabel(color) {
    var map = {
      black: "BLACK", red: "RED", blue: "BLUE", yellow: "YELLOW",
      none: "기타", all: "전체",
    };
    return map[color] || "기타";
  }
  window.IRMS.colorLabel = colorLabel;

  function initTableScrollHints() {
    document.querySelectorAll(".table-wrap").forEach(function (wrap) {
      function update() {
        var hasScroll = wrap.scrollWidth > wrap.clientWidth + 1;
        wrap.classList.toggle("has-scroll", hasScroll);
        wrap.classList.toggle("scrolled-end", hasScroll && wrap.scrollLeft + wrap.clientWidth >= wrap.scrollWidth - 2);
      }
      wrap.addEventListener("scroll", update, { passive: true });
      update();
      new ResizeObserver(update).observe(wrap);
    });
  }

  window.IRMS.initTableScrollHints = initTableScrollHints;

  /* ── Chat notification: sound + TTS ── */
  var notifSoundCtx = null;

  function playChatSound() {
    try {
      if (!notifSoundCtx) notifSoundCtx = new (window.AudioContext || window.webkitAudioContext)();
      var ctx = notifSoundCtx;
      var osc = ctx.createOscillator();
      var gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.setValueAtTime(880, ctx.currentTime);
      osc.frequency.setValueAtTime(1047, ctx.currentTime + 0.08);
      gain.gain.setValueAtTime(0.18, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.3);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.3);
    } catch (_) { /* AudioContext unavailable */ }
  }

  function speakText(text) {
    if (!window.speechSynthesis) return;
    var utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "ko-KR";
    utterance.rate = 1.1;
    utterance.volume = 0.9;
    window.speechSynthesis.speak(utterance);
  }

  window.IRMS.playChatSound = playChatSound;
  window.IRMS.speakText = speakText;

  bindLogoutButton();

  const navToggle = document.getElementById("nav-toggle");
  const topNav = document.querySelector(".top-nav");
  if (navToggle && topNav) {
    navToggle.addEventListener("click", () => {
      const isOpen = topNav.classList.toggle("open");
      navToggle.classList.toggle("active", isOpen);
      navToggle.setAttribute("aria-expanded", String(isOpen));
    });
  }

  // Floating chat sidebar
  const chatFloat = document.querySelector(".chat-float");
  if (chatFloat) {
    const toggle = document.createElement("button");
    toggle.className = "chat-float-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-label", "메시지");
    toggle.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
    document.body.appendChild(toggle);

    const closeBtn = document.createElement("button");
    closeBtn.className = "chat-float-close";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "닫기");
    closeBtn.innerHTML = "&times;";
    const chatHead = chatFloat.querySelector(".chat-head");
    if (chatHead) {
      chatHead.appendChild(closeBtn);
    } else {
      chatFloat.prepend(closeBtn);
    }

    function setChatOpen(open) {
      chatFloat.classList.toggle("open", open);
      toggle.classList.toggle("active", open);
    }

    toggle.addEventListener("click", () => setChatOpen(!chatFloat.classList.contains("open")));
    closeBtn.addEventListener("click", () => setChatOpen(false));

    // Close on overlay (backdrop) click
    const overlay = document.createElement("div");
    overlay.className = "chat-float-overlay";
    document.body.appendChild(overlay);
    overlay.addEventListener("click", () => setChatOpen(false));

    const origSetChatOpen = setChatOpen;
    setChatOpen = function (open) {
      origSetChatOpen(open);
      overlay.classList.toggle("active", open);
    };
  }

  // Enter to send in chat textareas (Shift+Enter for newline)
  document.addEventListener("keydown", (e) => {
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !e.isComposing &&
      e.target.classList.contains("chat-textarea")
    ) {
      e.preventDefault();
      const form = e.target.closest("form");
      if (form) form.requestSubmit();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initTableScrollHints);
  } else {
    initTableScrollHints();
  }
})();
