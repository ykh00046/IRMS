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

  // 내 비밀번호 변경 refs
  const changePwForm = document.getElementById("change-pw-form");
  const cpwCurrent = document.getElementById("cpw-current");
  const cpwNew = document.getElementById("cpw-new");
  const cpwSubmit = document.getElementById("cpw-submit");
  const summaryTotal = document.getElementById("summary-total");
  const summaryManagers = document.getElementById("summary-managers");

  // 감사 로그 refs
  const auditRefreshBtn = document.getElementById("audit-refresh");
  const auditActionFilter = document.getElementById("audit-action-filter");
  const auditLimitFilter = document.getElementById("audit-limit-filter");
  const auditLogList = document.getElementById("audit-log-list");

  function actionLabel(action) {
    const map = {
      management_login: "책임자 로그인",
      logout: "로그아웃",
      login_failed: "로그인 실패",
      worker_register: "이용자 등록",
      worker_reactivated: "이용자 재활성화",
      worker_update: "이용자 수정",
      worker_deleted: "이용자 삭제",
      worker_manager_granted: "책임자 지정",
      worker_manager_revoked: "책임자 해제",
      worker_manager_password_reset: "책임자 비번 초기화",
      worker_manager_password_changed: "책임자 비번 변경(본인)",
      password_changed: "비밀번호 변경(본인)",
      blend_worker_login: "작업자 로그인",
      blend_record_create: "배합 기록 생성",
      blend_record_update: "배합 기록 수정",
      blend_record_deleted: "배합 기록 삭제",
      blend_record_cancel: "배합 기록 취소",
      blend_record_bulk_create: "배합 일괄 생성",
      blend_record_review: "검토 기록",
      blend_record_approve: "승인 기록",
      blend_viscosity_link: "점도 등록(배합 연계)",
      viscosity_reading_add: "점도 등록",
      viscosity_reading_delete: "점도 삭제",
      viscosity_product_create: "점도 품목 추가",
      viscosity_product_update: "점도 품목 수정",
      attendance_viewed_by_admin: "근태 조회",
      attendance_password_reset: "근태 비번 초기화",
      recipe_status_updated: "레시피 상태",
      recipes_imported: "레시피 등록",
      recipe_deleted: "레시피 삭제",
      recipe_dhr_set: "DHR 전용 지정",
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
            placeholder="새 비밀번호(8자 이상)" />
          <div class="button-row">
            <button type="button" class="btn accent" data-action="reset-password">비밀번호 초기화</button>
            ${isSelf ? '<span class="helper-text">본인 계정</span>'
              : '<button type="button" class="btn danger" data-action="revoke">책임자 해제</button>'}
          </div>
        </div>`
      : `
        <div class="password-stack">
          <input type="password" class="input row-password mono" data-field="new-password" maxlength="100"
            placeholder="비밀번호 설정(8자 이상)" />
          <button type="button" class="btn accent" data-action="grant">책임자 지정</button>
        </div>`;
    const statusCell = `
      <div class="button-row">
        ${worker.is_active
          ? '<button type="button" class="btn" data-action="deactivate">비활성화</button>'
          : '<button type="button" class="btn" data-action="activate">활성화</button>'}
        <button type="button" class="btn danger" data-action="delete">삭제</button>
      </div>`;
    // 파트(약품/합성/잉크/용수) — 변경 즉시 저장. 빈 값 = 미지정(해제).
    // 작업자 로그인 화면의 파트 필터가 이 값으로 명단을 거른다.
    const PARTS = ["약품", "합성", "잉크", "용수"];
    const partCell = `
      <td><select class="input worker-part-select">
        <option value=""${!worker.category ? " selected" : ""}>미지정</option>
        ${PARTS.map((p) => `<option value="${p}"${worker.category === p ? " selected" : ""}>${p}</option>`).join("")}
      </select></td>`;
    return `
      <tr data-worker-id="${worker.id}" data-name="${esc(worker.name)}">
        <td>
          <span class="user-name">
            ${esc(worker.name)}
            ${isSelf ? '<span class="inline-chip current">본인</span>' : ""}
            ${!worker.is_active ? '<span class="inline-chip inactive">비활성</span>' : ""}
          </span>
        </td>
        ${partCell}
        <td>${roleChip}</td>
        <td>${managerCell}</td>
        <td>${statusCell}</td>
      </tr>`;
  }

  function renderWorkers(items) {
    if (!items.length) {
      workersBody.innerHTML =
        '<tr><td colspan="5"><div class="empty-state-block">등록된 이용자가 없습니다.</div></td></tr>';
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

  async function handleChangePassword(event) {
    event.preventDefault();
    const current = String(cpwCurrent?.value || "");
    const next = String(cpwNew?.value || "");
    if (!current) {
      IRMS.notify("현재 비밀번호를 입력하세요.", "error");
      return;
    }
    if (next.length < 8) {
      IRMS.notify("새 비밀번호는 8자 이상이어야 합니다.", "error");
      return;
    }
    IRMS.btnLoading(cpwSubmit, true);
    try {
      await request("/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      changePwForm.reset();
      IRMS.notify("비밀번호를 변경했습니다.", "success");
    } catch (error) {
      const msg = error.message === "INVALID_CURRENT_PASSWORD"
        ? "현재 비밀번호가 올바르지 않습니다."
        : error.message;
      IRMS.notify(`변경 실패: ${msg}`, "error");
    } finally {
      IRMS.btnLoading(cpwSubmit, false);
    }
  }

  const workerErrorMap = {
    WORKER_NOT_FOUND: "대상 이용자를 찾을 수 없습니다.",
    NOT_A_MANAGER: "책임자가 아닙니다.",
    CANNOT_REVOKE_SELF: "본인의 책임자 권한은 해제할 수 없습니다.",
    CANNOT_DELETE_MANAGER: "책임자는 삭제할 수 없습니다. 먼저 책임자를 해제한 뒤 삭제하세요.",
    HAS_RECORDS: "배합 기록이 있는 이름은 삭제할 수 없습니다. 비활성화해 주세요.",
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
        if (password.length < 8) {
          IRMS.notify("비밀번호는 8자 이상이어야 합니다.", "error");
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
      } else if (action === "delete") {
        if (!window.confirm(`'${name}'을(를) 명단에서 완전히 삭제하시겠습니까? 되돌릴 수 없습니다.\n(사람이 그만둔 경우는 삭제 대신 "비활성화"를 권장합니다.)`)) return;
        await request(`/workers/${workerId}`, { method: "DELETE" });
        IRMS.notify(`${name}을(를) 삭제했습니다.`, "success");
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
  changePwForm?.addEventListener("submit", handleChangePassword);
  workersRefresh?.addEventListener("click", loadWorkers);
  workersBody?.addEventListener("click", handleWorkerAction);
  // 파트 변경 즉시 저장 — PATCH /workers/{id} {category}. 빈 값("")은 미지정으로 해제.
  workersBody?.addEventListener("change", async (event) => {
    const sel = event.target.closest(".worker-part-select");
    if (!sel) return;
    const row = sel.closest("tr[data-worker-id]");
    if (!row) return;
    try {
      await request(`/workers/${row.dataset.workerId}`, {
        method: "PATCH",
        body: { category: sel.value },
      });
      IRMS.notify(
        sel.value ? `파트를 '${sel.value}'(으)로 지정했습니다.` : "파트를 미지정으로 되돌렸습니다.",
        "success",
      );
    } catch (error) {
      IRMS.notify(`파트 저장 실패: ${error.message}`, "error");
    }
  });
  auditRefreshBtn?.addEventListener("click", loadAuditLogs);
  auditActionFilter?.addEventListener("change", loadAuditLogs);
  auditLimitFilter?.addEventListener("change", loadAuditLogs);

  // Attendance users
  const attUsersBody = document.getElementById("att-users-body");
  const attUsersRefresh = document.getElementById("att-users-refresh");

  // 근태 관리 오류 코드 → 사용자용 한국어 메시지. 서버 코드를 날것으로 노출하지 않는다
  // (예: 근태 엑셀에 없는 사번을 최초 발급 시도 → 404 EMP_NOT_IN_EXCEL).
  const attErrorMap = {
    EMP_NOT_IN_EXCEL: "근태 자료(엑셀)에 없는 사번입니다. 사번을 확인하세요.",
  };
  const attErrorMessage = (msg) => attErrorMap[msg] || msg;

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
            IRMS.notify(`초기화 실패: ${attErrorMessage(err.message)}`, "error");
            event.currentTarget.disabled = false;
          }
        });
      });
    } catch (err) {
      attUsersBody.innerHTML = `<tr><td colspan="7" style="color:#dc2626;text-align:center">${err.message}</td></tr>`;
    }
  }

  attUsersRefresh?.addEventListener("click", loadAttendanceUsers);

  // 계정 미발급 직원 최초 발급 — 목록(발급된 계정만)에 없는 사번을 직접 입력해 발급.
  // 백엔드 POST /admin/reset-password 가 미발급 사번이면 계정을 새로 만든다
  // (근태 명단에 없는 사번은 404 EMP_NOT_IN_EXCEL — attErrorMap 으로 한국어 메시지 노출).
  const attNewEmp = document.getElementById("att-user-new-emp");
  const attIssueBtn = document.getElementById("att-user-issue-btn");
  attIssueBtn?.addEventListener("click", async () => {
    const empId = String(attNewEmp?.value || "").trim();
    if (!empId) {
      IRMS.notify("사번을 입력하세요.", "error");
      attNewEmp?.focus();
      return;
    }
    if (!window.confirm(`사번 ${empId}의 임시 비밀번호를 발급할까요? (계정이 없으면 새로 만듭니다)`)) return;
    try {
      attIssueBtn.disabled = true;
      const result = await attendanceFetch("/api/attendance/admin/reset-password", {
        method: "POST",
        body: { emp_id: empId },
      });
      window.prompt(`사번 ${empId} 임시 비밀번호`, result.temporary_password || "");
      IRMS.notify(`사번 ${empId} 임시 비밀번호를 발급했습니다.`, "success");
      if (attNewEmp) attNewEmp.value = "";
      loadAttendanceUsers();
    } catch (err) {
      IRMS.notify(`발급 실패: ${attErrorMessage(err.message)}`, "error");
    } finally {
      attIssueBtn.disabled = false;
    }
  });
  attNewEmp?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      attIssueBtn?.click();
    }
  });
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

  // ── 저울 전용 입력 모드 설정 카드(대시보드에서 이전, 2026-07-20) ──────
  // GET 으로 현재 상태를 표시하고, 토글 버튼으로 PUT(x-csrftoken 직접 부착).
  (function initScaleOnlyCard() {
    const card = document.getElementById("scale-only-card");
    const statusEl = document.getElementById("scale-only-status");
    const toggleBtn = document.getElementById("scale-only-toggle");
    if (!card || !statusEl || !toggleBtn) return;

    async function loadScaleOnly() {
      try {
        const res = await fetch("/api/settings/scale-only-input", { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        const enabled = Boolean(data && data.enabled);
        statusEl.textContent = enabled ? "켜짐" : "꺼짐";
        toggleBtn.textContent = enabled ? "끄기" : "켜기";
        toggleBtn.dataset.enabled = enabled ? "1" : "0";
        toggleBtn.disabled = false;
      } catch (err) {
        statusEl.textContent = "상태 조회 실패";
        IRMS.notify(`저울 전용 입력 상태 조회 실패: ${err.message}`, "error");
      }
    }

    toggleBtn.addEventListener("click", async () => {
      const next = toggleBtn.dataset.enabled !== "1";
      toggleBtn.disabled = true;
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const res = await fetch("/api/settings/scale-only-input", {
          method: "PUT",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ enabled: next }),
        });
        if (!res.ok) {
          let msg = `HTTP ${res.status}`;
          try {
            const p = await res.json();
            if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail);
          } catch (_e) { /* noop */ }
          throw new Error(msg);
        }
        const data = await res.json();
        const enabled = Boolean(data && data.enabled);
        statusEl.textContent = enabled ? "켜짐" : "꺼짐";
        toggleBtn.textContent = enabled ? "끄기" : "켜기";
        toggleBtn.dataset.enabled = enabled ? "1" : "0";
        IRMS.notify(enabled ? "저울 전용 입력 모드를 켰습니다." : "저울 전용 입력 모드를 껐습니다.", "success");
      } catch (err) {
        IRMS.notify(`저울 전용 입력 변경 실패: ${err.message}`, "error");
      } finally {
        toggleBtn.disabled = false;
      }
    });

    loadScaleOnly();
  })();

  // ── 배합 창 예외 코드 설정 카드 ──────────────────────────────────
  // GET 으로 현재 코드를 표시하고, 저장 버튼으로 PUT(x-csrftoken 직접 부착). 책임자 전용.
  (function initBlendWindowCodeCard() {
    const card = document.getElementById("blend-window-code-card");
    const input = document.getElementById("blend-window-code-input");
    const saveBtn = document.getElementById("blend-window-code-save");
    if (!card || !input || !saveBtn) return;

    async function loadCode() {
      try {
        const res = await fetch("/api/settings/blend-window-override", { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        input.value = (data && data.code) || "";
        saveBtn.disabled = false;
      } catch (err) {
        IRMS.notify(`배합 창 예외 코드 조회 실패: ${err.message}`, "error");
      }
    }

    saveBtn.addEventListener("click", async () => {
      const code = String(input.value || "").trim();
      if (code.length < 4 || code.length > 32) {
        IRMS.notify("코드는 4자 이상 32자 이하여야 합니다.", "error");
        return;
      }
      saveBtn.disabled = true;
      try {
        const headers = { "Content-Type": "application/json" };
        const token = IRMS._core && IRMS._core.getCsrfToken ? IRMS._core.getCsrfToken() : "";
        if (token) headers["x-csrftoken"] = token;
        const res = await fetch("/api/settings/blend-window-override", {
          method: "PUT",
          credentials: "same-origin",
          headers,
          body: JSON.stringify({ code }),
        });
        if (!res.ok) {
          let msg = `HTTP ${res.status}`;
          try {
            const p = await res.json();
            if (p && p.detail) msg = typeof p.detail === "object" ? (p.detail.message || msg) : String(p.detail);
          } catch (_e) { /* noop */ }
          throw new Error(msg);
        }
        IRMS.notify("배합 창 예외 코드를 변경했습니다.", "success");
      } catch (err) {
        IRMS.notify(`배합 창 예외 코드 변경 실패: ${err.message}`, "error");
      } finally {
        saveBtn.disabled = false;
      }
    });

    loadCode();
  })();

  refreshDashboard();
});
