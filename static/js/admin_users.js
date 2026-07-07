document.addEventListener("DOMContentLoaded", () => {
  const request = IRMS._core && IRMS._core.request;
  const esc = IRMS.escapeHtml;
  const currentUserIdInput = document.getElementById("current-user-id");
  const currentUserId = Number(currentUserIdInput?.value || 0);

  // 이용자(사람) 명단 refs
  const addWorkerForm = document.getElementById("add-worker-form");
  const addWorkerName = document.getElementById("add-worker-name");
  const addWorkerSubmit = document.getElementById("add-worker-submit");
  const workersBody = document.getElementById("workers-body");
  const workersRefresh = document.getElementById("workers-refresh");
  const summaryTotal = document.getElementById("summary-total");
  const summaryManagers = document.getElementById("summary-managers");

  // 감사 로그 refs
  const auditRefreshBtn = document.getElementById("audit-refresh");
  const auditActionFilter = document.getElementById("audit-action-filter");
  const auditLimitFilter = document.getElementById("audit-limit-filter");
  const auditLogList = document.getElementById("audit-log-list");

  function actionLabel(action) {
    const map = {
      auth_login: "Legacy Login",
      management_login: "책임자 로그인",
      operator_select: "Operator Select",
      logout: "Logout",
      login_failed: "로그인 실패",
      worker_register: "이용자 등록",
      worker_update: "이용자 수정",
      worker_manager_granted: "책임자 지정",
      worker_manager_revoked: "책임자 해제",
      worker_manager_password_reset: "책임자 비번 초기화",
      attendance_viewed_by_admin: "근태 조회",
      attendance_password_reset: "근태 비번 초기화",
      recipe_status_updated: "레시피 상태",
      recipes_imported: "레시피 등록",
    };
    return map[action] || action;
  }

  function formatCreatedAt(value) {
    return IRMS.formatDateTime(value);
  }

  // ── 이용자 명단 ──────────────────────────────────────────────
  function renderSummary(items) {
    if (summaryTotal) summaryTotal.textContent = String(items.length);
    if (summaryManagers) summaryManagers.textContent = String(items.filter((w) => w.is_manager).length);
  }

  function renderWorkerRow(worker) {
    const isSelf = worker.id === currentUserId;
    const roleChip = worker.is_manager
      ? '<span class="status-chip status-completed">책임자</span>'
      : '<span class="status-chip">이용자</span>';
    const managerCell = worker.is_manager
      ? `
        <div class="password-stack">
          <input type="password" class="input row-password mono" data-field="new-password" maxlength="100"
            placeholder="새 비밀번호(6자 이상)" />
          <div class="button-row">
            <button type="button" class="btn accent" data-action="reset-password">비밀번호 초기화</button>
            ${isSelf ? '<span class="helper-text">본인 계정</span>'
              : '<button type="button" class="btn danger" data-action="revoke">책임자 해제</button>'}
          </div>
        </div>`
      : `
        <div class="password-stack">
          <input type="password" class="input row-password mono" data-field="new-password" maxlength="100"
            placeholder="비밀번호 설정(6자 이상)" />
          <button type="button" class="btn accent" data-action="grant">책임자 지정</button>
        </div>`;
    const statusCell = worker.is_active
      ? '<button type="button" class="btn" data-action="deactivate">비활성화</button>'
      : '<button type="button" class="btn" data-action="activate">활성화</button>';
    return `
      <tr data-worker-id="${worker.id}" data-name="${esc(worker.name)}">
        <td>
          <span class="user-name">
            ${esc(worker.name)}
            ${isSelf ? '<span class="inline-chip current">본인</span>' : ""}
            ${!worker.is_active ? '<span class="inline-chip inactive">비활성</span>' : ""}
          </span>
        </td>
        <td>${roleChip}</td>
        <td>${managerCell}</td>
        <td>${statusCell}</td>
      </tr>`;
  }

  function renderWorkers(items) {
    if (!items.length) {
      workersBody.innerHTML =
        '<tr><td colspan="4"><div class="empty-state-block">등록된 이용자가 없습니다.</div></td></tr>';
      return;
    }
    workersBody.innerHTML = items.map(renderWorkerRow).join("");
  }

  async function loadWorkers() {
    try {
      if (workersRefresh) workersRefresh.disabled = true;
      const data = await request("/workers/all");
      const items = data.items || [];
      renderSummary(items);
      renderWorkers(items);
    } catch (error) {
      IRMS.notify(`이용자 목록 조회 실패: ${error.message}`, "error");
    } finally {
      if (workersRefresh) workersRefresh.disabled = false;
    }
  }

  async function handleAddWorker(event) {
    event.preventDefault();
    const name = String(addWorkerName?.value || "").trim();
    if (!name) {
      IRMS.notify("이름을 입력하세요.", "error");
      return;
    }
    IRMS.btnLoading(addWorkerSubmit, true);
    try {
      const result = await request("/workers", { method: "POST", body: { name } });
      addWorkerForm.reset();
      IRMS.notify(result.created ? `${name} 이용자를 추가했습니다.` : `${name}은(는) 이미 명단에 있습니다.`, "success");
      await refreshDashboard();
    } catch (error) {
      IRMS.notify(`추가 실패: ${error.message}`, "error");
    } finally {
      IRMS.btnLoading(addWorkerSubmit, false);
    }
  }

  const workerErrorMap = {
    WORKER_NOT_FOUND: "대상 이용자를 찾을 수 없습니다.",
    NOT_A_MANAGER: "책임자가 아닙니다.",
    CANNOT_REVOKE_SELF: "본인의 책임자 권한은 해제할 수 없습니다.",
  };

  async function handleWorkerAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const row = button.closest("tr[data-worker-id]");
    if (!row) return;
    const workerId = Number(row.dataset.workerId);
    const name = row.dataset.name || "";
    const action = button.dataset.action;
    const passwordInput = row.querySelector('[data-field="new-password"]');
    const password = String(passwordInput?.value || "");

    button.disabled = true;
    try {
      if (action === "grant" || action === "reset-password") {
        if (password.length < 6) {
          IRMS.notify("비밀번호는 6자 이상이어야 합니다.", "error");
          return;
        }
        const path = action === "grant"
          ? `/workers/${workerId}/manager`
          : `/workers/${workerId}/manager/password`;
        await request(path, { method: "POST", body: { password } });
        if (passwordInput) passwordInput.value = "";
        IRMS.notify(action === "grant" ? `${name}을(를) 책임자로 지정했습니다.` : `${name}의 비밀번호를 초기화했습니다.`, "success");
      } else if (action === "revoke") {
        if (!window.confirm(`'${name}'의 책임자 권한을 해제하시겠습니까? (이름만 쓰는 이용자로 돌아갑니다)`)) return;
        await request(`/workers/${workerId}/manager`, { method: "DELETE" });
        IRMS.notify(`${name}의 책임자 권한을 해제했습니다.`, "success");
      } else if (action === "deactivate") {
        if (!window.confirm(`'${name}'을(를) 비활성화하시겠습니까?`)) return;
        await request(`/workers/${workerId}`, { method: "PATCH", body: { is_active: false } });
        IRMS.notify(`${name}을(를) 비활성화했습니다.`, "success");
      } else if (action === "activate") {
        await request(`/workers/${workerId}`, { method: "PATCH", body: { is_active: true } });
        IRMS.notify(`${name}을(를) 활성화했습니다.`, "success");
      }
      await refreshDashboard();
    } catch (error) {
      IRMS.notify(`실패: ${workerErrorMap[error.message] || error.message}`, "error");
    } finally {
      button.disabled = false;
    }
  }

  // ── 감사 로그 ────────────────────────────────────────────────
  function detailsSummary(log) {
    const details = log.details || {};
    if (log.action === "recipe_status_updated") {
      return `${details.from_status || "-"} -> ${details.to_status || "-"}`;
    }
    if (log.action === "recipes_imported") {
      return `${details.created_count || 0}건 등록`;
    }
    if (log.action === "attendance_viewed_by_admin") {
      return `${details.month || "-"} 근태 조회`;
    }
    if (log.action === "management_login" || log.action === "operator_select" || log.action === "auth_login") {
      return details.entry_point || "session";
    }
    const entries = Object.entries(details);
    if (!entries.length) return "세부정보 없음";
    return entries.slice(0, 2).map(([key, value]) => `${key}: ${String(value)}`).join(" / ");
  }

  function actorSummary(log) {
    return {
      display: log.actorDisplayName || log.actorUsername || "system",
      username: log.actorUsername ? `@${log.actorUsername}` : "system",
      accessLevel: log.actorAccessLevel || "system",
    };
  }

  function usernameWithRole(actor) {
    return `${actor.username} · ${actor.accessLevel}`;
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
              <span class="audit-action-chip">${esc(actionLabel(log.action))}</span>
              <time class="audit-time">${esc(formatCreatedAt(log.createdAt))}</time>
            </div>
            <div class="audit-item-main">
              <div class="audit-actor-block">
                <strong>${esc(actor.display)}</strong>
                <span class="audit-meta">${esc(usernameWithRole(actor))}</span>
              </div>
              <div class="audit-target-block">
                <span class="audit-target-label">${esc(targetLabel)}</span>
                <span class="audit-detail-text">${esc(detailsSummary(log))}</span>
              </div>
            </div>
          </article>`;
      })
      .join("");
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
    await Promise.all([loadWorkers(), loadAuditLogs()]);
  }

  addWorkerForm?.addEventListener("submit", handleAddWorker);
  workersRefresh?.addEventListener("click", loadWorkers);
  workersBody?.addEventListener("click", handleWorkerAction);
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
