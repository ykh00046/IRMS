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

  function renderSummary(summary) {
    summaryTotal.textContent = String(summary.total || 0);
    summaryActive.textContent = String(summary.active || 0);
    summaryManagers.textContent = String(summary.managers || 0);
    summaryOperators.textContent = String(summary.operators || 0);
  }

  function accessOptions(selected) {
    return ["operator", "manager"]
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
                  placeholder="새 비밀번호 (6자 이상)"
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

    if (password.length < 6) {
      IRMS.notify("새 비밀번호는 6자 이상이어야 합니다.", "error");
      return;
    }

    button.disabled = true;
    try {
      await IRMS.resetUserPassword(userId, password);
      if (passwordInput) {
        passwordInput.value = "";
      }
      IRMS.notify("비밀번호를 재설정했습니다.", "success");
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
        LAST_MANAGER: "마지막 책임자 계정은 삭제할 수 없습니다.",
      };
      IRMS.notify(`삭제 실패: ${messageMap[error.message] || error.message}`, "error");
      button.disabled = false;
    }
  }

  createForm?.addEventListener("submit", handleCreate);
  refreshBtn?.addEventListener("click", loadUsers);
  auditRefreshBtn?.addEventListener("click", loadAuditLogs);
  auditActionFilter?.addEventListener("change", loadAuditLogs);
  auditLimitFilter?.addEventListener("change", loadAuditLogs);

  refreshDashboard();
});
