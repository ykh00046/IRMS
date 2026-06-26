document.addEventListener("DOMContentLoaded", () => {
  const currentUserIdInput = document.getElementById("current-user-id");
  const createForm = document.getElementById("create-user-form");
  const createDisplayName = document.getElementById("create-display-name");
  const createUsername = document.getElementById("create-username");
  const createAccessLevel = document.getElementById("create-access-level");
  const createPassword = document.getElementById("create-password");
  const createSubmit = document.getElementById("create-user-submit");
  const refreshBtn = document.getElementById("users-refresh");
  const usersBody = document.getElementById("users-body");

  const summaryTotal = document.getElementById("summary-total");
  const summaryActive = document.getElementById("summary-active");
  const summaryManagers = document.getElementById("summary-managers");
  const summaryOperators = document.getElementById("summary-operators");

  const auditRefreshBtn = document.getElementById("audit-refresh");
  const auditActionFilter = document.getElementById("audit-action-filter");
  const auditLimitFilter = document.getElementById("audit-limit-filter");
  const auditLogList = document.getElementById("audit-log-list");

  const currentUserId = Number(currentUserIdInput?.value || 0);

  function accessLabel(accessLevel) {
    const map = {
      admin: "관리자",
      manager: "책임자",
      operator: "담당자",
    };
    return map[accessLevel] || accessLevel;
  }

  function actionLabel(action) {
    const map = {
      auth_login: "Legacy Login",
      management_login: "Management Login",
      operator_select: "Operator Select",
      logout: "Logout",
      user_created: "User Created",
      user_updated: "User Updated",
      user_password_reset: "Password Reset",
      user_deleted: "User Deleted",
      attendance_viewed_by_admin: "Attendance Viewed",
      attendance_password_reset: "Attendance Password Reset",
      recipe_status_updated: "Recipe Status",
      weighing_step_completed: "Weighing Step",
      recipe_weighing_completed: "Recipe Complete",
      recipes_imported: "Recipe Import",
    };
    return map[action] || action;
  }

  function formatCreatedAt(value) {
    return IRMS.formatDateTime(value);
  }

  const summaryAdmins = document.getElementById("summary-admins");

  function renderSummary(summary) {
    summaryTotal.textContent = String(summary.total || 0);
    summaryActive.textContent = String(summary.active || 0);
    if (summaryAdmins) summaryAdmins.textContent = String(summary.admins || 0);
    summaryManagers.textContent = String(summary.managers || 0);
    summaryOperators.textContent = String(summary.operators || 0);
  }

  function accessOptions(selected) {
    return ["operator", "manager", "admin"]
      .map(
        (accessLevel) =>
          `<option value="${accessLevel}"${selected === accessLevel ? " selected" : ""}>${accessLabel(accessLevel)}</option>`,
      )
      .join("");
  }

  function activeOptions(isActive) {
    return `
      <option value="true"${isActive ? " selected" : ""}>활성</option>
      <option value="false"${!isActive ? " selected" : ""}>비활성</option>
    `;
  }

  function renderUsersTable(items) {
    if (!items.length) {
      usersBody.innerHTML =
        '<tr><td colspan="8"><div class="empty-state-block">사용자 데이터가 없습니다.</div></td></tr>';
      return;
    }

    usersBody.innerHTML = items
      .map((user) => {
        const isCurrentUser = user.id === currentUserId;
        return `
          <tr data-user-id="${user.id}">
            <td>
              <div class="user-meta">
                <span class="user-name">
                  ${IRMS.escapeHtml(user.displayName)}
                  ${isCurrentUser ? '<span class="inline-chip current">본인</span>' : ""}
                  ${!user.isActive ? '<span class="inline-chip inactive">비활성</span>' : ""}
                </span>
                <span class="user-id">@${IRMS.escapeHtml(user.username)}</span>
              </div>
            </td>
            <td>
              <input
                class="input row-input"
                data-field="display-name"
                value="${IRMS.escapeHtml(user.displayName)}"
                maxlength="50"
              />
            </td>
            <td>
              <select class="select row-select" data-field="access-level">
                ${accessOptions(user.accessLevel)}
              </select>
            </td>
            <td>
              <select class="select row-select" data-field="active">
                ${activeOptions(user.isActive)}
              </select>
            </td>
            <td>${formatCreatedAt(user.createdAt)}</td>
            <td>
              <div class="action-stack">
                <button type="button" class="btn" data-action="save">저장</button>
                <span class="helper-text">${IRMS.escapeHtml(user.roleLabel)} 계정</span>
              </div>
            </td>
            <td>
              <div class="password-stack">
                <input
                  type="password"
                  class="input row-password mono"
                  data-field="password"
                  maxlength="100"
                  placeholder="새 비밀번호 (8자 이상, 숫자 반복/연속 불가)"
                />
                <button type="button" class="btn accent" data-action="reset-password">비밀번호 재설정</button>
              </div>
            </td>
            <td>
              ${isCurrentUser
                ? '<span class="helper-text">본인 계정</span>'
                : '<button type="button" class="btn danger" data-action="delete">삭제</button>'}
            </td>
          </tr>
        `;
      })
      .join("");
  }

  function detailsSummary(log) {
    const details = log.details || {};

    if (log.action === "recipe_status_updated") {
      return `${details.from_status || "-"} -> ${details.to_status || "-"}`;
    }
    if (log.action === "recipes_imported") {
      return `${details.created_count || 0}건 등록`;
    }
    if (log.action === "weighing_step_completed") {
      return `${details.material_name || "-"} · 잔여 ${details.remaining_in_recipe ?? "-"}`;
    }
    if (log.action === "recipe_weighing_completed") {
      return `completed_at ${details.completed_at || "-"}`;
    }
    if (log.action === "user_updated") {
      const before = details.before || {};
      const after = details.after || {};
      return `${before.access_level || "-"} -> ${after.access_level || "-"}`;
    }
    if (log.action === "user_created") {
      return `${details.access_level || "-"} 생성`;
    }
    if (log.action === "user_password_reset") {
      return `${details.target_access_level || "-"} 계정 비밀번호 재설정`;
    }
    if (log.action === "attendance_viewed_by_admin") {
      return `${details.month || "-"} 근태 조회`;
    }
    if (log.action === "attendance_password_reset") {
      return "사번 비밀번호 초기화";
    }
    if (log.action === "management_login" || log.action === "operator_select" || log.action === "auth_login") {
      return details.entry_point || "session";
    }

    const entries = Object.entries(details);
    if (!entries.length) {
      return "세부정보 없음";
    }
    return entries
      .slice(0, 2)
      .map(([key, value]) => `${key}: ${String(value)}`)
      .join(" / ");
  }

  function actorSummary(log) {
    const display = log.actorDisplayName || log.actorUsername || "system";
    const username = log.actorUsername ? `@${log.actorUsername}` : "system";
    const accessLevel = log.actorAccessLevel || "system";
    return { display, username, accessLevel };
  }

  function renderAuditLogs(items) {
    if (!items.length) {
      auditLogList.innerHTML = '<div class="empty-state-block">표시할 감사 로그가 없습니다.</div>';
      return;
    }

    auditLogList.innerHTML = items
      .map((log) => {
        const actor = actorSummary(log);
        const targetLabel = log.targetLabel || `${log.targetType || "target"} ${log.targetId || "-"}`;
        return `
          <article class="audit-item">
            <div class="audit-item-top">
              <span class="audit-action-chip">${IRMS.escapeHtml(actionLabel(log.action))}</span>
              <time class="audit-time">${IRMS.escapeHtml(formatCreatedAt(log.createdAt))}</time>
            </div>
            <div class="audit-item-main">
              <div class="audit-actor-block">
                <strong>${IRMS.escapeHtml(actor.display)}</strong>
                <span class="audit-meta">${IRMS.escapeHtml(usernameWithRole(actor))}</span>
              </div>
              <div class="audit-target-block">
                <span class="audit-target-label">${IRMS.escapeHtml(targetLabel)}</span>
                <span class="audit-detail-text">${IRMS.escapeHtml(detailsSummary(log))}</span>
              </div>
            </div>
          </article>
        `;
      })
      .join("");
  }

  function usernameWithRole(actor) {
    return `${actor.username} · ${actor.accessLevel}`;
  }

  async function loadUsers() {
    try {
      if (refreshBtn) {
        refreshBtn.disabled = true;
      }
      const result = await IRMS.listUsers();
      renderSummary(result.summary || {});
      renderUsersTable(result.items || []);
    } catch (error) {
      IRMS.notify(`사용자 목록 조회 실패: ${error.message}`, "error");
    } finally {
      if (refreshBtn) {
        refreshBtn.disabled = false;
      }
    }
  }

  async function loadAuditLogs() {
    IRMS.btnLoading(auditRefreshBtn, true);
    try {
      const result = await IRMS.listAuditLogs({
        action: String(auditActionFilter?.value || "") || undefined,
        limit: Number(auditLimitFilter?.value || 50),
      });
      renderAuditLogs(result.items || []);
    } catch (error) {
      IRMS.notify(`감사 로그 조회 실패: ${error.message}`, "error");
    } finally {
      IRMS.btnLoading(auditRefreshBtn, false);
    }
  }

  async function refreshDashboard() {
    await Promise.all([loadUsers(), loadAuditLogs()]);
  }

  async function handleCreate(event) {
    event.preventDefault();

    const displayName = String(createDisplayName?.value || "").trim();
    const username = String(createUsername?.value || "").trim();
    const accessLevel = String(createAccessLevel?.value || "operator");
    const password = String(createPassword?.value || "");

    if (!displayName || !username || !password) {
      IRMS.notify("이름, 아이디, 초기 비밀번호를 모두 입력하세요.", "error");
      return;
    }

    IRMS.btnLoading(createSubmit, true);
    try {
      await IRMS.createUser({
        displayName,
        username,
        accessLevel,
        password,
      });
      createForm.reset();
      createAccessLevel.value = "operator";
      IRMS.notify("사용자 계정을 생성했습니다.", "success");
      await refreshDashboard();
    } catch (error) {
      const message = error.message === "USERNAME_ALREADY_EXISTS"
        ? "이미 사용 중인 아이디입니다."
        : error.message;
      IRMS.notify(`계정 생성 실패: ${message}`, "error");
    } finally {
      IRMS.btnLoading(createSubmit, false);
    }
  }

  async function handleSave(row, button) {
    const userId = Number(row.dataset.userId);
    const displayName = String(row.querySelector('[data-field="display-name"]')?.value || "").trim();
    const accessLevel = String(row.querySelector('[data-field="access-level"]')?.value || "operator");
    const isActive = String(row.querySelector('[data-field="active"]')?.value || "true") === "true";

    if (!displayName) {
      IRMS.notify("표시 이름을 입력하세요.", "error");
      return;
    }

    button.disabled = true;
    try {
      await IRMS.updateUser(userId, {
        displayName,
        accessLevel,
        isActive,
      });
      IRMS.notify("사용자 정보를 저장했습니다.", "success");
      await refreshDashboard();
    } catch (error) {
      const messageMap = {
        USER_NOT_FOUND: "대상 사용자를 찾을 수 없습니다.",
        CANNOT_CHANGE_SELF_ACCESS: "본인 계정의 권한 또는 활성 상태는 직접 변경할 수 없습니다.",
        LAST_ADMIN: "마지막 관리자 계정은 변경할 수 없습니다.",
        LAST_MANAGER: "마지막 책임자 계정은 변경할 수 없습니다.",
      };
      IRMS.notify(`저장 실패: ${messageMap[error.message] || error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function handlePasswordReset(row, button) {
    const userId = Number(row.dataset.userId);
    const passwordInput = row.querySelector('[data-field="password"]');
    const password = String(passwordInput?.value || "");

    if (password.length < 8) {
      IRMS.notify("새 비밀번호는 8자 이상이어야 합니다.", "error");
      return;
    }

    button.disabled = true;
    try {
      const result = await IRMS.resetUserPassword(userId, password);
      if (passwordInput) {
        passwordInput.value = "";
      }
      IRMS.notify(result.password_expiration_notice || "비밀번호를 재설정했습니다.", "success");
      await loadAuditLogs();
    } catch (error) {
      IRMS.notify(`비밀번호 재설정 실패: ${error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  usersBody?.addEventListener("click", async (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) {
      return;
    }

    const row = button.closest("tr[data-user-id]");
    if (!row) {
      return;
    }

    if (button.dataset.action === "save") {
      await handleSave(row, button);
      return;
    }

    if (button.dataset.action === "reset-password") {
      await handlePasswordReset(row, button);
      return;
    }

    if (button.dataset.action === "delete") {
      await handleDelete(row, button);
    }
  });

  async function handleDelete(row, button) {
    const userId = Number(row.dataset.userId);
    const displayName = row.querySelector('[data-field="display-name"]')?.value || "";
    if (!window.confirm(`'${displayName}' 계정을 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.`)) {
      return;
    }
    button.disabled = true;
    try {
      await IRMS.deleteUser(userId);
      IRMS.notify("사용자를 삭제했습니다.", "success");
      await refreshDashboard();
    } catch (error) {
      const messageMap = {
        USER_NOT_FOUND: "대상 사용자를 찾을 수 없습니다.",
        CANNOT_DELETE_SELF: "본인 계정은 삭제할 수 없습니다.",
        LAST_ADMIN: "마지막 관리자 계정은 삭제할 수 없습니다.",
        LAST_MANAGER: "마지막 책임자 계정은 삭제할 수 없습니다.",
      };
      IRMS.notify(`삭제 실패: ${messageMap[error.message] || error.message}`, "error");
      button.disabled = false;
    }
  }

  createForm?.addEventListener("submit", handleCreate);
  refreshBtn?.addEventListener("click", loadUsers);

  const deactivateOthersBtn = document.getElementById("deactivate-others-btn");
  deactivateOthersBtn?.addEventListener("click", async () => {
    if (!window.confirm(
      "admin 을 제외한 모든 로그인 계정을 비활성화할까요?\n" +
      "(작업자는 이름 입력으로 사용합니다. 필요 시 사용자 목록에서 되돌릴 수 있습니다.)"
    )) return;
    try {
      const result = await IRMS._core.request("/admin/deactivate-others", { method: "POST", body: {} });
      IRMS.notify(`${result.deactivated}개 계정을 비활성화했습니다.`, "success");
      loadUsers();
    } catch (error) {
      IRMS.notify(`비활성화 실패: ${error.message}`, "error");
    }
  });
  auditRefreshBtn?.addEventListener("click", loadAuditLogs);
  auditActionFilter?.addEventListener("change", loadAuditLogs);
  auditLimitFilter?.addEventListener("change", loadAuditLogs);

  // Attendance users
  const attUsersBody = document.getElementById("att-users-body");
  const attUsersRefresh = document.getElementById("att-users-refresh");

  async function attendanceFetch(path, options) {
    const opts = options || {};
    const method = opts.method || "GET";
    const csrfMatch = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    const csrfToken = csrfMatch ? decodeURIComponent(csrfMatch[1]) : "";
    const headers = opts.body
      ? { "Content-Type": "application/json", "x-csrftoken": csrfToken }
      : undefined;
    const response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers,
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!response.ok) {
      let detail = "";
      try {
        const payload = await response.json();
        detail = payload?.detail?.detail || payload?.detail || "";
      } catch (_) {
        detail = response.statusText;
      }
      throw new Error(String(detail));
    }
    return response.json();
  }

  function formatAttDate(text) {
    if (!text) return "—";
    return String(text).replace("T", " ").replace("Z", "");
  }

  async function loadAttendanceUsers() {
    if (!attUsersBody) return;
    attUsersBody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#64748b">로딩 중...</td></tr>';
    try {
      const data = await attendanceFetch("/api/attendance/admin/users");
      const items = data.items || [];
      if (!items.length) {
        attUsersBody.innerHTML = '<tr><td colspan="7" style="text-align:center;color:#64748b">등록된 근태 계정이 없습니다.</td></tr>';
        return;
      }
      attUsersBody.innerHTML = "";
      items.forEach((item) => {
        const tr = document.createElement("tr");
        const resetTag = item.password_reset_required
          ? '<span style="color:#b45309;font-weight:700">초기 상태</span>'
          : '<span style="color:#166534">사용자 설정됨</span>';
        tr.innerHTML = `
          <td class="mono">${item.emp_id}</td>
          <td>${resetTag}</td>
          <td>${item.failed_attempts}</td>
          <td>${formatAttDate(item.locked_until)}</td>
          <td>${formatAttDate(item.last_login_at)}</td>
          <td>${formatAttDate(item.created_at)}</td>
          <td><button type="button" class="btn compact danger" data-emp-id="${item.emp_id}">임시 비밀번호 발급</button></td>`;
        attUsersBody.appendChild(tr);
      });
      attUsersBody.querySelectorAll("button[data-emp-id]").forEach((btn) => {
        btn.addEventListener("click", async (event) => {
          const empId = event.currentTarget.dataset.empId;
          if (!empId) return;
          if (!window.confirm(`사번 ${empId}의 임시 비밀번호를 발급할까요?`)) return;
          try {
            event.currentTarget.disabled = true;
            const result = await attendanceFetch("/api/attendance/admin/reset-password", {
              method: "POST",
              body: { emp_id: empId },
            });
            window.prompt(`사번 ${empId} 임시 비밀번호`, result.temporary_password || "");
            IRMS.notify(`사번 ${empId} 임시 비밀번호를 발급했습니다.`, "success");
            loadAttendanceUsers();
          } catch (err) {
            IRMS.notify(`초기화 실패: ${err.message}`, "error");
            event.currentTarget.disabled = false;
          }
        });
      });
    } catch (err) {
      attUsersBody.innerHTML = `<tr><td colspan="7" style="color:#dc2626;text-align:center">${err.message}</td></tr>`;
    }
  }

  attUsersRefresh?.addEventListener("click", loadAttendanceUsers);
  loadAttendanceUsers();

  // ── 배합일지 서명 합성 설정 (signature_qa_tool 이식) ──
  (function initSignatureConfig() {
    const grid = document.getElementById("sig-config-grid");
    const preview = document.getElementById("sig-preview");
    const req = IRMS._core && IRMS._core.request;
    if (!grid || !req) return;
    const LABELS = {
      gaussian_blur_sigma: "가우시안 블러", pressure_noise_strength: "펜압 노이즈",
      ink_alpha_factor: "서명 진하기", signature_brightness_factor: "서명 밝기",
      final_contrast_factor: "최종 대비", rotation_angle: "회전 각도(°)",
      scale_min: "최소 크기", scale_max: "최대 크기",
      scan_noise_range: "스캔 노이즈", scan_blur_radius: "스캔 블러",
      scan_contrast: "스캔 대비", scan_brightness: "스캔 밝기",
      scan_paper_tone: "종이톤(여백)",
    };
    let defaults = {};

    function refreshPreview() {
      if (preview) preview.src = `/api/admin/signature-preview?t=${Date.now()}`;
    }
    function render(cfg, ranges) {
      grid.innerHTML = Object.keys(LABELS).map((k) => {
        const r = ranges[k] || [0, 10];
        return `<label style="display:flex;flex-direction:column;gap:3px;font-size:12px;color:var(--text-secondary)">
          <span>${LABELS[k]}</span>
          <input type="number" data-key="${k}" value="${cfg[k]}" step="0.05" min="${r[0]}" max="${r[1]}" class="input" />
        </label>`;
      }).join("");
    }
    function collect() {
      const out = {};
      grid.querySelectorAll("input[data-key]").forEach((el) => { out[el.dataset.key] = Number(el.value); });
      return out;
    }
    async function load() {
      try {
        const data = await req("/admin/signature-config");
        defaults = data.defaults || {};
        render(data.config, data.ranges || {});
      } catch (e) { IRMS.notify(`서명 설정 로드 실패: ${e.message}`, "error"); }
    }
    document.getElementById("sig-config-save")?.addEventListener("click", async () => {
      try {
        await req("/admin/signature-config", { method: "PUT", body: collect() });
        IRMS.notify("서명 설정을 저장했습니다.", "success");
        refreshPreview();
      } catch (e) { IRMS.notify(`저장 실패: ${e.message}`, "error"); }
    });
    document.getElementById("sig-config-reset")?.addEventListener("click", async () => {
      try {
        await req("/admin/signature-config", { method: "PUT", body: defaults });
        await load();
        IRMS.notify("기본값으로 되돌렸습니다.", "success");
        refreshPreview();
      } catch (e) { IRMS.notify(`초기화 실패: ${e.message}`, "error"); }
    });
    document.getElementById("sig-preview-refresh")?.addEventListener("click", refreshPreview);
    load().then(refreshPreview);
  })();

  // ── 작업자 서명 샘플 관리 ──
  (function initSignatureSamples() {
    const req = IRMS._core && IRMS._core.request;
    const listEl = document.getElementById("sig-sample-list");
    const roleEl = document.getElementById("sig-sample-role");
    const workerEl = document.getElementById("sig-sample-worker");
    const workerWrap = document.getElementById("sig-sample-worker-wrap");
    const fileEl = document.getElementById("sig-sample-file");
    if (!req || !listEl) return;
    const ROLE_KO = { charge: "담당", review: "검토", approve: "승인" };

    function toggleWorker() { workerWrap.style.display = roleEl.value === "charge" ? "" : "none"; }
    function render(items) {
      if (!items || !items.length) { listEl.innerHTML = '<p class="panel-subtitle">샘플이 없습니다.</p>'; return; }
      listEl.innerHTML = items.map((g) => {
        const title = g.role === "charge" ? `담당 · ${g.worker}` : (ROLE_KO[g.role] || g.base);
        const files = g.files.map((f) =>
          `<span style="display:inline-flex;align-items:center;gap:3px;border:1px solid var(--line);border-radius:6px;padding:2px 6px;margin:2px;font-size:12px">${f}<button data-del="${f}" title="삭제" style="border:0;background:none;cursor:pointer;color:#dc2626;font-weight:700">×</button></span>`
        ).join("");
        return `<div style="margin-bottom:8px"><b>${title}</b> <span style="color:var(--text-secondary)">(${g.count})</span><div>${files}</div></div>`;
      }).join("");
      listEl.querySelectorAll("button[data-del]").forEach((b) => b.addEventListener("click", async () => {
        if (!confirm(`${b.dataset.del} 샘플을 삭제할까요?`)) return;
        try {
          const r = await req(`/admin/signature-samples/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" });
          render(r.items); IRMS.notify("샘플을 삭제했습니다.", "success");
        } catch (e) { IRMS.notify(`삭제 실패: ${e.message}`, "error"); }
      }));
    }
    async function load() {
      try { const r = await req("/admin/signature-samples"); render(r.items); }
      catch (e) { IRMS.notify(`샘플 로드 실패: ${e.message}`, "error"); }
    }
    roleEl.addEventListener("change", toggleWorker);
    toggleWorker();
    document.getElementById("sig-sample-upload").addEventListener("click", () => {
      const file = fileEl.files && fileEl.files[0];
      if (!file) { IRMS.notify("서명 이미지를 선택하세요.", "warn"); return; }
      if (roleEl.value === "charge" && !workerEl.value.trim()) { IRMS.notify("작업자 이름을 입력하세요.", "warn"); return; }
      const reader = new FileReader();
      reader.onload = async () => {
        try {
          const r = await req("/admin/signature-samples", {
            method: "POST",
            body: { role: roleEl.value, worker: workerEl.value.trim(), image_data: reader.result },
          });
          render(r.items); fileEl.value = "";
          IRMS.notify(`업로드: ${r.filename}`, "success");
        } catch (e) { IRMS.notify(`업로드 실패: ${e.message}`, "error"); }
      };
      reader.readAsDataURL(file);
    });
    load();
  })();

  // ── Google Sheets 백업 (선택, google_sheets_backup 이식) ──
  (function initSheetsBackup() {
    const req = IRMS._core && IRMS._core.request;
    const urlEl = document.getElementById("sheets-url");
    const credsEl = document.getElementById("sheets-creds");
    const enabledEl = document.getElementById("sheets-enabled");
    const statusEl = document.getElementById("sheets-status");
    if (!req || !urlEl) return;

    function renderStatus(s) {
      const parts = [
        s.gspread_available ? "gspread 설치됨" : "⚠ gspread 미설치 (pip install gspread google-auth)",
        s.configured ? "설정 완료" : "설정 미완료",
        s.enabled ? "활성화" : "비활성화",
      ];
      statusEl.textContent = parts.join("  ·  ");
      statusEl.style.color = s.gspread_available && s.configured && s.enabled
        ? "var(--success, #166534)" : "var(--text-secondary)";
    }
    async function load() {
      try {
        const s = await req("/admin/sheets-config");
        urlEl.value = s.spreadsheet_url || "";
        credsEl.value = s.credentials_file || "";
        enabledEl.checked = !!s.enabled;
        renderStatus(s);
      } catch (e) { IRMS.notify(`Sheets 설정 로드 실패: ${e.message}`, "error"); }
    }
    document.getElementById("sheets-save")?.addEventListener("click", async () => {
      try {
        const s = await req("/admin/sheets-config", {
          method: "PUT",
          body: { spreadsheet_url: urlEl.value, credentials_file: credsEl.value, enabled: enabledEl.checked },
        });
        renderStatus(s);
        IRMS.notify("Google Sheets 설정을 저장했습니다.", "success");
      } catch (e) { IRMS.notify(`저장 실패: ${e.message}`, "error"); }
    });
    document.getElementById("sheets-backup-now")?.addEventListener("click", async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true;
      try {
        const r = await req("/admin/sheets-backup", { method: "POST", body: {} });
        IRMS.notify(r.message || (r.ok ? "백업 완료" : "백업 실패"), r.ok ? "success" : "error");
      } catch (e) { IRMS.notify(`백업 실패: ${e.message}`, "error"); }
      finally { btn.disabled = false; }
    });
    load();
  })();

  refreshDashboard();
});
