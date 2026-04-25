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
  const empDirectInput = document.getElementById("att-emp-direct");
  const empDirectBtn = document.getElementById("att-emp-direct-btn");
  const changePwBtn = document.getElementById("att-change-pw-btn");
  const logoutBtn = document.getElementById("att-logout-btn");

  const state = {
    month: currentMonthString(),
    availableMonths: [],
    selectedEmpId: adminMode && !ownEmpId ? "" : ownEmpId,
  };

  function currentMonthString() {
    const now = new Date();
    const year = now.getFullYear();
    const month = String(now.getMonth() + 1).padStart(2, "0");
    return `${year}-${month}`;
  }

  function truncateDecimals(value, digits = 2) {
    const numeric = Number(value || 0);
    if (!isFinite(numeric)) return 0;
    const factor = 10 ** digits;
    return Math.trunc(numeric * factor) / factor;
  }

  function formatFixed(value, digits = 2) {
    return truncateDecimals(value, digits).toFixed(digits);
  }

  function formatHours(value) {
    return `${formatFixed(value, 2)}h`;
  }

  function formatDays(value) {
    return formatFixed(value, 2);
  }

  function formatCountHours(count, hours) {
    return `${count || 0}회 ${formatHours(hours)}`;
  }

  function isWeekdayType(dayType) {
    const value = String(dayType || "").trim();
    return !value || value === "평일" || value === "평일2";
  }

  function isRestDayType(dayType) {
    const value = String(dayType || "").trim();
    return value === "주휴" || value === "무휴";
  }

  function isSaturday(weekday) {
    return String(weekday || "").trim() === "토";
  }

  function isSunday(weekday) {
    return String(weekday || "").trim() === "일";
  }

  function isLeaveText(text) {
    const value = String(text || "").trim();
    if (!value) return false;
    return /(?:연차|반차|반반차)/.test(value);
  }

  function isIssueText(text) {
    const value = String(text || "").trim();
    if (!value) return false;
    return /(?:지각|조퇴|외출|누락)/.test(value);
  }

  function isLeaveRow(row) {
    return isLeaveText(row?.attendance_code) || isLeaveText(row?.day_type);
  }

  function leaveBreakdownText(summary) {
    return `연차 ${formatDays(summary?.annual_leave_full_days || 0)} · 반차 ${formatDays(summary?.annual_leave_half_days || 0)} · 반반차 ${formatDays(summary?.annual_leave_quarter_days || 0)}`;
  }

  async function apiGet(path, query) {
    const url = new URL(path, window.location.origin);
    if (query) {
      Object.entries(query).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== "") {
          url.searchParams.set(key, String(value));
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
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  async function apiPost(path, payload) {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        "x-csrftoken": csrfToken(),
      },
      body: JSON.stringify(payload || {}),
    });

    if (!response.ok) {
      let detail = "";
      try {
        const responseBody = await response.json();
        detail = responseBody?.detail?.detail || responseBody?.detail || "";
      } catch (_) {
        detail = response.statusText;
      }
      throw new Error(String(detail));
    }

    return response.json();
  }

  function renderProfile(profile) {
    if (!profile) {
      profileName.textContent = "-";
      profileMeta.textContent = "";
      return;
    }

    profileName.textContent = `${profile.name || "-"} 님`;
    const parts = [
      profile.emp_id ? `사번 ${profile.emp_id}` : null,
      profile.department || null,
      profile.factory || null,
      profile.shift_time ? `근무시간 ${profile.shift_time}` : null,
    ].filter(Boolean);
    profileMeta.textContent = parts.join(" · ");
  }

  function renderSummary(summary) {
    if (!summary) return;
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

  function renderAnnualSummary(summary) {
    const year =
      summary?.year ||
      Number(String(state.month || "").slice(0, 4)) ||
      new Date().getFullYear();
    const months = Number(summary?.months_count || 0);
    const availableMonths = Number(summary?.available_months_count || 0);
    const skippedMonths = Array.isArray(summary?.skipped_months)
      ? summary.skipped_months
      : [];

    const lateCount = document.getElementById("att-year-late-count");
    const lateHours = document.getElementById("att-year-late-hours");
    const lateScope = document.getElementById("att-year-late-scope");
    const leaveDays = document.getElementById("att-year-leave-days");
    const leaveScope = document.getElementById("att-year-leave-scope");
    const leaveBreakdown = document.getElementById("att-year-leave-breakdown");

    if (lateCount) {
      lateCount.textContent = String(summary?.late_count || 0);
    }
    if (lateHours) {
      lateHours.textContent = formatHours(summary?.late_total || 0);
    }
    if (leaveDays) {
      leaveDays.textContent = formatDays(summary?.annual_leave_days || 0);
    }
    if (leaveBreakdown) {
      leaveBreakdown.textContent = leaveBreakdownText(summary);
    }

    let scopeText =
      months > 0
        ? `${year}년 ${months}개월 반영`
        : `${year}년 반영 데이터 없음`;
    if (availableMonths > 0 && skippedMonths.length > 0) {
      scopeText += ` · ${skippedMonths.length}개월 제외`;
    }

    if (lateScope) {
      lateScope.textContent = scopeText;
      lateScope.classList.toggle("warn", skippedMonths.length > 0);
    }
    if (leaveScope) {
      leaveScope.textContent = `${scopeText} · 합계 기준`;
      leaveScope.classList.toggle("warn", skippedMonths.length > 0);
    }
  }

  function renderRows(rows) {
    const tbody = document.getElementById("att-rows-body");
    tbody.innerHTML = "";

    if (!rows || !rows.length) {
      const tr = document.createElement("tr");
      tr.className = "att-empty-row";
      tr.innerHTML =
        '<td colspan="18">표시할 근태 데이터가 없습니다.</td>';
      tbody.appendChild(tr);
      return;
    }

    rows.forEach((row) => {
      const tr = document.createElement("tr");
      tr.className = rowClassName(row);

      const missIn = !row.check_in;
      const missOut = !row.check_out;
      const late = Number(row.late_hours || 0) > 0;
      const earlyLeave = Number(row.early_leave_hours || 0) > 0;
      const outing = Number(row.outing_hours || 0) > 0;

      tr.innerHTML = `
        <td class="att-date-cell">${dateCell(row)}</td>
        <td class="att-weekday-cell">${escapeHtml(row.weekday)}</td>
        <td class="att-daytype-cell">${dayTypePill(row)}</td>
        <td class="att-code-cell">${attendanceCodeCell(row)}</td>
        <td class="${missIn ? "att-miss" : "att-time-cell"}"><span class="att-num">${escapeHtml(
          row.check_in || "--"
        )}</span></td>
        <td class="${missOut ? "att-miss" : "att-time-cell"}"><span class="att-num">${escapeHtml(
          row.check_out || "--"
        )}</span>${row.next_day ? " +" : ""}</td>
        <td class="att-col-weekday att-col-first">${hoursCell(row.weekday_normal)}</td>
        <td class="att-col-weekday">${hoursCell(row.weekday_overtime)}</td>
        <td class="att-col-weekday">${hoursCell(row.weekday_night)}</td>
        <td class="att-col-weekday">${hoursCell(row.weekday_early)}</td>
        <td class="att-col-holiday att-col-first">${hoursCell(row.holiday_normal)}</td>
        <td class="att-col-holiday">${hoursCell(row.holiday_overtime)}</td>
        <td class="att-col-holiday">${hoursCell(row.holiday_night)}</td>
        <td class="att-col-holiday">${hoursCell(row.holiday_early)}</td>
        <td class="att-col-adjust-first ${late ? "att-late" : ""}">${hoursCell(row.late_hours)}</td>
        <td class="${earlyLeave ? "att-late" : ""}">${hoursCell(row.early_leave_hours)}</td>
        <td class="${outing ? "att-late" : ""}">${hoursCell(row.outing_hours)}</td>
        <td class="att-note-cell">${escapeHtml(row.note || "")}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function rowClassName(row) {
    const classes = [];
    const weekday = String(row.weekday || "").trim();
    const dayType = String(row.day_type || "").trim();

    if (row.has_issue) {
      classes.push("att-row-issue");
    }

    if (isRestDayType(dayType)) {
      classes.push("att-day-rest");
    } else if (isLeaveRow(row)) {
      classes.push("att-day-leave");
    } else if (isSaturday(weekday)) {
      classes.push("att-day-saturday");
    } else if (isSunday(weekday)) {
      classes.push("att-day-sunday");
    } else if (dayType && !isWeekdayType(dayType)) {
      classes.push("att-day-holiday");
    }

    return classes.join(" ");
  }

  function escapeHtml(text) {
    if (text === null || text === undefined) return "";
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function hoursCell(value) {
    const numeric = Number(value || 0);
    if (!isFinite(numeric) || numeric === 0) {
      return '<span class="att-zero">--</span>';
    }
    return `<span class="att-num">${formatFixed(value, 2)}</span>`;
  }

  function attendanceCodeCell(row) {
    const code = String(row.attendance_code || "").trim();
    if (!code) {
      return '<span class="att-code-empty">-</span>';
    }

    let toneClass = "";
    if (isLeaveText(code)) {
      toneClass = " att-code-pill-leave";
    } else if (isIssueText(code)) {
      toneClass = " att-code-pill-issue";
    }

    return `<span class="att-code-pill${toneClass}">${escapeHtml(code)}</span>`;
  }

  function dateCell(row) {
    const issueLabels = Array.isArray(row.issues)
      ? row.issues.filter(Boolean)
      : [];
    const issueTitle = issueLabels.length
      ? ` title="${escapeHtml(issueLabels.join(", "))}"`
      : "";
    const issueChip = row.has_issue
      ? `<span class="att-issue-chip"${issueTitle}>이상</span>`
      : "";

    return `
      <div class="att-date-stack">
        <span class="att-num">${escapeHtml(String(row.date || "").slice(5))}</span>
        ${issueChip}
      </div>
    `;
  }

  function dayTypePill(row) {
    const dayType = String(row.day_type || "").trim();
    const weekday = String(row.weekday || "").trim();

    let cls = "att-pill att-pill-weekday";
    let text = dayType || "평일";

    if (isRestDayType(dayType)) {
      cls = "att-pill att-pill-rest";
      text = dayType;
    } else if (isSaturday(weekday)) {
      cls = "att-pill att-pill-saturday";
      text = dayType || "토요일";
    } else if (isSunday(weekday)) {
      cls = "att-pill att-pill-sunday";
      text = dayType || "일요일";
    } else if (dayType && !isWeekdayType(dayType)) {
      cls = "att-pill att-pill-holiday";
      text = dayType;
    }

    return `<span class="${cls}">${escapeHtml(text)}</span>`;
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

    if (empDirectInput && !empDirectInput.value && state.selectedEmpId) {
      empDirectInput.value = state.selectedEmpId;
    }

    if (!items.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "표시 가능한 직원이 없습니다";
      empSelect.appendChild(option);
      empSelect.disabled = true;
      adminPicker.hidden = false;
      updateMonthNav();
      return;
    }

    empSelect.disabled = false;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "-- 직원 선택 --";
    empSelect.appendChild(placeholder);

    items.forEach((emp) => {
      const option = document.createElement("option");
      option.value = emp.emp_id;
      option.textContent = `${emp.name} · ${emp.emp_id} · ${emp.department || ""}`.trim();
      if (emp.emp_id === state.selectedEmpId) {
        option.selected = true;
      }
      empSelect.appendChild(option);
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
          renderAnnualSummary({});
          renderRows([]);
          return;
        }
        payload = await apiGet("/api/attendance/admin/view", {
          emp_id: state.selectedEmpId,
          month: state.month,
        });
      } else if (
        adminMode &&
        state.selectedEmpId &&
        state.selectedEmpId !== ownEmpId
      ) {
        payload = await apiGet("/api/attendance/admin/view", {
          emp_id: state.selectedEmpId,
          month: state.month,
        });
      } else {
        payload = await apiGet("/api/attendance/me", { month: state.month });
      }

      if (!payload) return;

      state.availableMonths = payload.available_months || state.availableMonths;
      if (adminMode && state.selectedEmpId && !payload.profile) {
        window.IRMS?.notify?.(
          "해당 사번의 근태 정보를 찾지 못했습니다.",
          "warn"
        );
      }

      renderProfile(payload.profile);
      renderSummary(payload.summary);
      renderAnnualSummary(payload.annual_summary);
      renderRows(payload.rows);
      updateMonthNav();
    } catch (error) {
      const message = String(error.message || error);
      if (message.includes("MONTH_FILE_NOT_FOUND")) {
        renderProfile(null);
        renderSummary({});
        renderAnnualSummary({});
        renderRows([]);
        monthLabel.textContent = `${state.month} (파일 없음)`;
      } else if (message.includes("FILE_LOCKED_RETRY")) {
        window.IRMS?.notify?.(
          "엑셀 파일이 열려 있습니다. 잠시 후 다시 시도해 주세요.",
          "error"
        );
      } else {
        window.IRMS?.notify?.(`조회 실패: ${message}`, "error");
      }
    }
  }

  function moveMonth(delta) {
    const list = state.availableMonths || [];
    const idx = list.indexOf(state.month);
    if (idx === -1) return;

    const nextIdx = idx - delta;
    if (nextIdx < 0 || nextIdx >= list.length) return;

    state.month = list[nextIdx];
    refreshView();
  }

  async function refreshView() {
    if (adminMode) {
      await loadEmployeesForAdmin();
    }
    await loadView();
  }

  monthPrev?.addEventListener("click", () => moveMonth(-1));
  monthNext?.addEventListener("click", () => moveMonth(1));

  changePwBtn?.addEventListener("click", () => {
    if (!ownEmpId) {
      window.IRMS?.notify?.(
        "관리자 전용 보기에서는 본인 근태 비밀번호를 바꿀 대상이 없습니다.",
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
      // Ignore logout errors and continue redirect.
    }
    window.location.assign(adminMode ? "/" : "/attendance/login");
  });

  empSelect?.addEventListener("change", () => {
    state.selectedEmpId = empSelect.value;
    if (empDirectInput) {
      empDirectInput.value = state.selectedEmpId;
    }
    loadView();
  });

  function selectDirectEmployee() {
    const empId = String(empDirectInput?.value || "").trim();
    if (!empId) {
      window.IRMS?.notify?.("조회할 사번을 입력해 주세요.", "warn");
      return;
    }
    state.selectedEmpId = empId;
    if (empSelect) {
      empSelect.value = "";
    }
    loadView();
  }

  empDirectBtn?.addEventListener("click", selectDirectEmployee);
  empDirectInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      selectDirectEmployee();
    }
  });

  function initResetBanner() {
    const banner = document.getElementById("att-reset-banner");
    const dismissBtn = document.getElementById("att-reset-banner-dismiss");
    if (!banner) return;

    const empId = banner.dataset.empId || "";
    const storageKey = empId ? `irms_att_reset_dismissed_${empId}` : "";
    const dismissed = storageKey && localStorage.getItem(storageKey) === "1";

    if (!dismissed) {
      banner.hidden = false;
    }

    dismissBtn?.addEventListener("click", () => {
      banner.hidden = true;
      if (storageKey) {
        localStorage.setItem(storageKey, "1");
      }
    });
  }

  window.IRMS = window.IRMS || {};
  window.IRMS.__attendanceTest = {
    truncateDecimals,
    formatFixed,
    formatHours,
    formatDays,
    isWeekdayType,
    isRestDayType,
    leaveBreakdownText,
    rowClassName,
    dayTypePill,
    hoursCell,
    attendanceCodeCell,
    dateCell,
    renderAnnualSummary,
  };

  (async function init() {
    initResetBanner();
    await refreshView();
  })();
})();
