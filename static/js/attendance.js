(function () {
  "use strict";

  const body = document.body;
  const adminMode = body.dataset.adminMode === "true";
  const ownEmpId = body.dataset.empId || "";

  const monthLabel = document.getElementById("att-month-label");
  const monthPrev = document.getElementById("att-month-prev");
  const monthNext = document.getElementById("att-month-next");
  const profileName = document.getElementById("att-profile-name");
  const profileMeta = document.getElementById("att-profile-meta");
  const adminPicker = document.getElementById("att-admin-picker");
  const empSelect = document.getElementById("att-emp-select");
  const changePwBtn = document.getElementById("att-change-pw-btn");
  const logoutBtn = document.getElementById("att-logout-btn");

  const state = {
    month: currentMonthString(),
    availableMonths: [],
    selectedEmpId: adminMode && !ownEmpId ? "" : ownEmpId,
  };

  function currentMonthString() {
    const now = new Date();
    const y = now.getFullYear();
    const m = String(now.getMonth() + 1).padStart(2, "0");
    return `${y}-${m}`;
  }

  function formatHours(value) {
    if (typeof value !== "number" || !isFinite(value)) return "0.0h";
    return `${value.toFixed(1)}h`;
  }

  function formatCountHours(count, hours) {
    return `${count || 0}회 · ${formatHours(hours)}`;
  }

  async function apiGet(path, query) {
    const url = new URL(path, window.location.origin);
    if (query) {
      Object.entries(query).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") {
          url.searchParams.set(k, String(v));
        }
      });
    }
    const response = await fetch(url, { credentials: "same-origin" });
    if (!response.ok) {
      let detail = "";
      try {
        const payload = await response.json();
        detail = payload?.detail?.detail || payload?.detail || "";
      } catch (_) {
        detail = response.statusText;
      }
      if (
        response.status === 401 &&
        String(detail).includes("ATTENDANCE_LOGIN_REQUIRED")
      ) {
        window.location.assign("/attendance/login");
        return null;
      }
      if (
        response.status === 403 &&
        String(detail).includes("PASSWORD_RESET_REQUIRED")
      ) {
        window.location.assign("/attendance/change-password");
        return null;
      }
      throw new Error(String(detail));
    }
    return response.json();
  }

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  async function apiPost(path, body) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "x-csrftoken": csrfToken(),
      },
      body: JSON.stringify(body || {}),
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

  function renderProfile(profile) {
    if (!profile) {
      profileName.textContent = "—";
      profileMeta.textContent = "";
      return;
    }
    profileName.textContent = `${profile.name || "-"} 님`;
    const parts = [
      profile.emp_id ? `사번 ${profile.emp_id}` : null,
      profile.department || null,
      profile.factory || null,
      profile.shift_time ? `근무타임 ${profile.shift_time}` : null,
    ].filter(Boolean);
    profileMeta.textContent = parts.join(" · ");
  }

  function renderSummary(summary) {
    if (!summary) return;
    document.getElementById("att-work-days").textContent = summary.work_days || 0;
    document.getElementById("att-late").textContent = formatCountHours(
      summary.late_count,
      summary.late_total
    );
    document.getElementById("att-early-leave").textContent = formatCountHours(
      summary.early_leave_count,
      summary.early_leave_total
    );
    document.getElementById("att-outing").textContent = formatCountHours(
      summary.outing_count,
      summary.outing_total
    );
    document.getElementById("att-wd-normal").textContent = formatHours(
      summary.weekday_normal
    );
    document.getElementById("att-wd-overtime").textContent = formatHours(
      summary.weekday_overtime
    );
    document.getElementById("att-wd-night").textContent = formatHours(
      summary.weekday_night
    );
    document.getElementById("att-wd-early").textContent = formatHours(
      summary.weekday_early
    );
    document.getElementById("att-hd-normal").textContent = formatHours(
      summary.holiday_normal
    );
    document.getElementById("att-hd-overtime").textContent = formatHours(
      summary.holiday_overtime
    );
    document.getElementById("att-hd-night").textContent = formatHours(
      summary.holiday_night
    );
    document.getElementById("att-hd-early").textContent = formatHours(
      summary.holiday_early
    );
  }

  function renderRows(rows) {
    const tbody = document.getElementById("att-rows-body");
    tbody.innerHTML = "";
    if (!rows || !rows.length) {
      const tr = document.createElement("tr");
      tr.className = "att-empty-row";
      tr.innerHTML = '<td colspan="13">이 달은 표시할 근태 데이터가 없습니다.</td>';
      tbody.appendChild(tr);
      return;
    }
    rows.forEach((row) => {
      const tr = document.createElement("tr");
      const isHoliday = row.day_type && row.day_type !== "평일";
      const cellClass = isHoliday ? "att-day-holiday" : "";
      const missIn = !row.check_in;
      const missOut = !row.check_out;
      const late = (row.late_hours || 0) > 0;
      tr.innerHTML = `
        <td class="${cellClass}">${escape(row.date)}</td>
        <td class="${cellClass}">${escape(row.weekday)}</td>
        <td class="${cellClass}">${escape(row.day_type)}</td>
        <td class="${cellClass} ${missIn ? "att-miss" : ""}">${escape(row.check_in || "—")}</td>
        <td class="${cellClass} ${missOut ? "att-miss" : ""}">${escape(row.check_out || "—")}${row.next_day ? " ⏭" : ""}</td>
        <td class="${cellClass}">${hoursCell(isHoliday ? row.holiday_normal : row.weekday_normal)}</td>
        <td class="${cellClass}">${hoursCell(isHoliday ? row.holiday_overtime : row.weekday_overtime)}</td>
        <td class="${cellClass}">${hoursCell(isHoliday ? row.holiday_night : row.weekday_night)}</td>
        <td class="${cellClass}">${hoursCell(isHoliday ? row.holiday_early : row.weekday_early)}</td>
        <td class="${cellClass} ${late ? "att-late" : ""}">${hoursCell(row.late_hours)}</td>
        <td class="${cellClass}">${hoursCell(row.early_leave_hours)}</td>
        <td class="${cellClass}">${hoursCell(row.outing_hours)}</td>
        <td class="${cellClass}">${escape(row.note || "")}</td>`;
      tbody.appendChild(tr);
    });
  }

  function escape(text) {
    if (text === null || text === undefined) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function hoursCell(value) {
    if (!value || value === 0) return '<span style="color:#94a3b8">0</span>';
    return Number(value).toFixed(1);
  }

  function updateMonthNav() {
    monthLabel.textContent = state.month;
    const list = state.availableMonths || [];
    const idx = list.indexOf(state.month);
    monthPrev.disabled = idx === -1 || idx >= list.length - 1;
    monthNext.disabled = idx <= 0;
  }

  async function loadEmployeesForAdmin() {
    const payload = await apiGet("/api/attendance/admin/employees", {
      month: state.month,
    });
    if (!payload) return;
    state.availableMonths = payload.available_months || [];
    const items = payload.items || [];
    empSelect.innerHTML = "";
    if (!items.length) {
      const opt = document.createElement("option");
      opt.value = "";
      opt.textContent = "이번 달 엑셀에 직원 없음";
      empSelect.appendChild(opt);
      empSelect.disabled = true;
      return;
    }
    empSelect.disabled = false;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "-- 직원 선택 --";
    empSelect.appendChild(placeholder);
    items.forEach((emp) => {
      const opt = document.createElement("option");
      opt.value = emp.emp_id;
      opt.textContent = `${emp.name} · ${emp.emp_id} · ${emp.department || ""}`.trim();
      if (emp.emp_id === state.selectedEmpId) opt.selected = true;
      empSelect.appendChild(opt);
    });
    adminPicker.hidden = false;
    updateMonthNav();
  }

  async function loadView() {
    try {
      let payload;
      if (adminMode && !ownEmpId) {
        if (!state.selectedEmpId) {
          renderProfile(null);
          renderSummary({});
          renderRows([]);
          return;
        }
        payload = await apiGet("/api/attendance/admin/view", {
          emp_id: state.selectedEmpId,
          month: state.month,
        });
      } else if (adminMode && state.selectedEmpId && state.selectedEmpId !== ownEmpId) {
        payload = await apiGet("/api/attendance/admin/view", {
          emp_id: state.selectedEmpId,
          month: state.month,
        });
      } else {
        payload = await apiGet("/api/attendance/me", { month: state.month });
      }
      if (!payload) return;
      state.availableMonths = payload.available_months || state.availableMonths;
      renderProfile(payload.profile);
      renderSummary(payload.summary);
      renderRows(payload.rows);
      updateMonthNav();
    } catch (error) {
      const msg = String(error.message || error);
      if (msg.includes("MONTH_FILE_NOT_FOUND")) {
        renderProfile(null);
        renderSummary({});
        renderRows([]);
        monthLabel.textContent = state.month + " (파일 없음)";
      } else if (msg.includes("FILE_LOCKED_RETRY")) {
        window.IRMS?.notify?.("엑셀 파일이 잠겨있습니다. 잠시 후 다시 시도해주세요.", "error");
      } else {
        window.IRMS?.notify?.(`조회 실패: ${msg}`, "error");
      }
    }
  }

  function moveMonth(delta) {
    const list = state.availableMonths || [];
    const idx = list.indexOf(state.month);
    if (idx === -1) return;
    const nextIdx = idx - delta; // list sorted desc (recent first)
    if (nextIdx < 0 || nextIdx >= list.length) return;
    state.month = list[nextIdx];
    loadView();
  }

  monthPrev?.addEventListener("click", () => moveMonth(-1));
  monthNext?.addEventListener("click", () => moveMonth(1));

  changePwBtn?.addEventListener("click", () => {
    if (!ownEmpId) {
      window.IRMS?.notify?.(
        "관리자 모드에서는 본인 계정이 없으므로 비밀번호 변경이 필요하지 않습니다.",
        "info"
      );
      return;
    }
    window.location.assign("/attendance/change-password");
  });

  logoutBtn?.addEventListener("click", async () => {
    try {
      await apiPost("/api/attendance/logout", {});
    } catch (_) {
      /* ignore */
    }
    if (adminMode) {
      window.location.assign("/");
    } else {
      window.location.assign("/attendance/login");
    }
  });

  empSelect?.addEventListener("change", () => {
    state.selectedEmpId = empSelect.value;
    loadView();
  });

  (async function init() {
    if (adminMode) {
      await loadEmployeesForAdmin();
    }
    await loadView();
  })();
})();
