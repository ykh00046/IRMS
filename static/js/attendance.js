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
  const empFilterInput = document.getElementById("att-emp-filter");
  const pendingCard = document.getElementById("att-pending-card");
  const pendingCount = document.getElementById("att-pending-count");
  const pendingNote = document.getElementById("att-pending-note");
  const monthMissing = document.getElementById("att-month-missing");
  const monthMissingText = document.getElementById("att-month-missing-text");
  const monthMissingBtn = document.getElementById("att-month-missing-btn");
  const anomalyPanel = document.getElementById("att-anomaly-panel");
  const anomalyTitle = document.getElementById("att-anomaly-title");
  const anomalyBody = document.getElementById("att-anomaly-body");

  const state = {
    month: currentMonthString(),
    availableMonths: [],
    selectedEmpId: adminMode && !ownEmpId ? "" : ownEmpId,
    employees: [],
  };

  // 판정 사유 해설 사전 — 사유 문자열의 출처는 서버의
  // src/services/attendance_excel/anomaly.py (_append_issue 로 붙는 값 전수).
  // 2026-08-14: 책임자가 트레이 알림의 "구분 0 / 내용 근태 이상 / 추가 내용 빈칸" 을
  // 받고 무슨 뜻인지 알 수 없었던 사고 때문에, 화면은 원문 사유와 해설을 함께 적는다.
  // 사전에 없는 사유(예: "근태코드 확인: XX")는 원문만 그대로 보여준다.
  const ISSUE_EXPLANATIONS = {
    "근태코드 누락(지각)":
      "지각 공제시간은 입력돼 있는데 근태코드에 '지각'이 없습니다 — ERP 근태코드 입력 대기",
    "근태코드 누락(조퇴)":
      "조퇴 공제시간은 입력돼 있는데 근태코드에 '조퇴'가 없습니다 — ERP 근태코드 입력 대기",
    "근태코드 누락(외출)":
      "외출 공제시간은 입력돼 있는데 근태코드에 '외출'이 없습니다 — ERP 근태코드 입력 대기",
    "공제시간 불일치":
      "휴가 코드인데 지각/조퇴 공제시간이 함께 있습니다 — 이중 차감 의심",
    "지각 미처리": "기준 출근보다 늦게 타각했는데 지각 공제가 아직 없습니다",
    "조퇴 미처리": "기준 퇴근보다 일찍 타각했는데 조퇴 공제가 아직 없습니다",
    "출근 누락": "출근 타각 기록이 없습니다",
    "퇴근 누락": "퇴근 타각 기록이 없습니다",
  };

  function explainIssue(issue) {
    const text = String(issue || "").trim();
    if (!text) return "";
    return ISSUE_EXPLANATIONS[text] || "";
  }

  function issueListHtml(issues) {
    const labels = (Array.isArray(issues) ? issues : []).filter(Boolean);
    if (!labels.length) return "";
    return labels
      .map((issue) => {
        const explanation = explainIssue(issue);
        return `<li class="att-issue-item">
            <span class="att-issue-label">${escapeHtml(issue)}</span>
            ${
              explanation
                ? `<span class="att-issue-why">${escapeHtml(explanation)}</span>`
                : ""
            }
          </li>`;
      })
      .join("");
  }

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
    return /(?:지각|조퇴|외출|누락|미타각)/.test(value);
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
      // detail 은 문자열일 수도, 구조체({code, available_months, ...})일 수도 있다.
      // 월 파일 없음(404)은 2026-08-14 부터 구조체로 온다 — 문자열만 다루던 예전
      // 방식으로 읽으면 "[object Object]" 가 되어 안내가 통째로 무의미해진다.
      let detail = "";
      let raw = null;
      try {
        const payload = await response.json();
        raw = payload?.detail;
        detail =
          (raw && typeof raw === "object"
            ? raw.detail || raw.code || ""
            : raw) || "";
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
      // 임시비번 변경은 소프트 유도(배너 + "나중에 변경")일 뿐 하드 게이트가
      // 아니다. 서버의 어떤 조회 엔드포인트도 403 PASSWORD_RESET_REQUIRED 를
      // 반환하지 않으므로(§4.2), 과거의 403 처리 분기는 도달 불가라 제거했다.
      const error = new Error(String(detail));
      error.detail = raw;
      error.status = response.status;
      throw error;
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
    // 조기 return 금지 — 값이 없으면 '그리지 않는' 게 아니라 '0 으로 지워야' 한다.
    // 예전에는 여기서 빠져나가, 조회 실패 시 앞사람 숫자가 화면에 그대로 남았다.
    summary = summary || {};
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
        '<td colspan="18"><div class="empty-state">표시할 근태 데이터가 없습니다.</div></td>';
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

      // 사유 서브라인 — 툴팁에만 있으면 터치 화면에서는 볼 방법이 없다(2026-08-14).
      const issueRows = issueListHtml(row.issues);
      if (issueRows) {
        const detail = document.createElement("tr");
        detail.className = "att-issue-detail-row";
        detail.innerHTML = `
          <td colspan="18">
            <div class="att-issue-detail">
              <span class="att-issue-detail-date att-num">${escapeHtml(
                String(row.date || "").slice(5)
              )}</span>
              <ul class="att-issue-list">${issueRows}</ul>
            </div>
          </td>
        `;
        tbody.appendChild(detail);
      }
    });
  }

  // 미처리 이상 = 아직 ERP 근태코드/공제로 정리되지 않은 행. 위쪽 '이번 달 근태 이상'
  // 카드(ERP 반영분)와 숫자의 뜻이 정반대라 카드도 라벨도 따로 둔다.
  function renderPending(rows) {
    if (!pendingCount || !pendingNote) return;
    const list = Array.isArray(rows) ? rows : [];
    const issueRows = list.filter(
      (row) => Array.isArray(row?.issues) && row.issues.filter(Boolean).length
    );
    const count = issueRows.length;
    pendingCount.textContent = String(count);
    pendingNote.textContent = count
      ? "ERP 근태코드·공제 입력 대기 · 눌러서 첫 이상 행으로"
      : "이상 없음";
    pendingCard?.classList.toggle("is-alert", count > 0);
    pendingNote.classList.toggle("warn", count > 0);
  }

  function scrollToFirstIssue() {
    const target = document.querySelector("#att-rows-body tr.att-row-issue");
    if (!target) {
      window.IRMS?.notify?.("이 달에는 미처리 이상이 없습니다.", "info");
      return;
    }
    target.scrollIntoView({ behavior: "smooth", block: "center" });
    target.classList.add("is-flash");
    window.setTimeout(() => target.classList.remove("is-flash"), 1600);
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

  // available_months 는 최신 달이 앞(내림차순)이다.
  // delta < 0 = '이전'(더 과거), delta > 0 = '다음'(더 미래).
  function nearestMonth(delta) {
    const list = state.availableMonths || [];
    if (delta < 0) return list.find((month) => month < state.month);
    return list.slice().reverse().find((month) => month > state.month);
  }

  function updateMonthNav() {
    monthLabel.textContent = state.month;
    const list = state.availableMonths || [];
    const idx = list.indexOf(state.month);
    if (idx === -1) {
      // 파일이 없는 달에 서 있어도 이동은 살아 있어야 한다 — 예전에는 여기서 두 버튼이
      // 모두 죽어 지난달조차 볼 수 없었다(2026-08-14 검토 4번).
      monthPrev.disabled = !nearestMonth(-1);
      monthNext.disabled = !nearestMonth(1);
      return;
    }
    monthPrev.disabled = idx >= list.length - 1;
    monthNext.disabled = idx <= 0;
  }

  async function loadEmployeesForAdmin() {
    const payload = await apiGet("/api/attendance/admin/employees", {
      month: state.month,
    });
    if (!payload) return;

    state.availableMonths = payload.available_months || [];
    state.employees = payload.items || [];

    if (empDirectInput && !empDirectInput.value && state.selectedEmpId) {
      empDirectInput.value = state.selectedEmpId;
    }

    renderEmpOptions();
    adminPicker.hidden = false;
    updateMonthNav();
  }

  function employeeMatchesFilter(emp, keyword) {
    if (!keyword) return true;
    const haystack = [emp?.name, emp?.emp_id, emp?.department]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return haystack.includes(keyword);
  }

  // select 자체와 change 배선은 그대로 두고 option 만 좁힌다(2026-08-14 검토 7번).
  // 지금 조회 중인 직원은 걸러지더라도 목록에서 사라지지 않게 남겨야, 좁히기 도중
  // select 값이 빈칸으로 바뀌어 '누구를 보고 있는지' 표시가 어긋나지 않는다.
  function renderEmpOptions() {
    if (!empSelect) return;
    const keyword = String(empFilterInput?.value || "")
      .trim()
      .toLowerCase();
    const all = state.employees || [];
    const items = all.filter(
      (emp) =>
        employeeMatchesFilter(emp, keyword) ||
        (state.selectedEmpId && emp.emp_id === state.selectedEmpId)
    );

    empSelect.innerHTML = "";

    if (!all.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "표시 가능한 직원이 없습니다";
      empSelect.appendChild(option);
      empSelect.disabled = true;
      return;
    }

    empSelect.disabled = false;
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = items.length
      ? "-- 직원 선택 --"
      : "-- 조건에 맞는 직원 없음 --";
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
  }

  // 책임자 이상 목록 — 신규 API(/admin/anomalies). 팝업이 사라진 뒤에도 같은 목록을
  // 화면에서 다시 볼 수 있어야 한다(2026-08-14 검토 2번).
  async function loadAnomalyPanel() {
    if (!anomalyPanel || !anomalyBody) return;
    try {
      const payload = await apiGet("/api/attendance/admin/anomalies", {
        month: state.month,
      });
      if (!payload) return;
      renderAnomalyPanel(payload);
    } catch (error) {
      // 목록을 못 불러온 것이 근태 조회를 막지는 않는다 — 조용히 사실만 적는다.
      anomalyBody.innerHTML = `<p class="att-anomaly-empty">이상 목록을 불러오지 못했습니다 (${escapeHtml(
        String(error.message || error)
      )}).</p>`;
      if (anomalyTitle) anomalyTitle.textContent = "이번 달 이상 전체";
      anomalyPanel.hidden = false;
    }
  }

  function renderAnomalyPanel(payload) {
    const items = Array.isArray(payload?.items) ? payload.items : [];
    const detailTotal = Number(payload?.detail_total || 0);
    const month = payload?.month || state.month;

    if (anomalyTitle) {
      anomalyTitle.textContent = items.length
        ? `${month} 이상 전체 (${items.length}명 · ${detailTotal}건)`
        : `${month} 이상 전체`;
    }

    if (!items.length) {
      anomalyBody.innerHTML =
        '<p class="att-anomaly-empty">이상 없음 — 이번 달 미처리 이상이 없습니다.</p>';
      anomalyPanel.hidden = false;
      return;
    }

    anomalyBody.innerHTML = items
      .map((item) => {
        const details = Array.isArray(item.details) ? item.details : [];
        const dates = Array.isArray(item.dates) ? item.dates : [];
        const detailHtml = details.length
          ? details
              .map(
                (detail) => `
              <div class="att-anomaly-detail">
                <span class="att-anomaly-date att-num">${escapeHtml(
                  detail.display_date || detail.date || ""
                )}</span>
                <ul class="att-issue-list">${issueListHtml(detail.issues)}</ul>
              </div>`
              )
              .join("")
          : `<div class="att-anomaly-detail">
               <ul class="att-issue-list">${issueListHtml(item.issues)}</ul>
             </div>`;

        return `
          <div class="att-anomaly-row" role="button" tabindex="0" data-emp-id="${escapeHtml(
            item.emp_id || ""
          )}">
            <div class="att-anomaly-who">
              <strong>${escapeHtml(item.name || "-")}</strong>
              <span class="att-num">${escapeHtml(item.emp_id || "")}</span>
              <span>${escapeHtml(item.department || "")}</span>
              <span class="att-anomaly-count">${dates.length || details.length}일</span>
            </div>
            <div class="att-anomaly-details">${detailHtml}</div>
          </div>`;
      })
      .join("");

    anomalyPanel.hidden = false;
  }

  function selectEmployeeFromPanel(empId) {
    const value = String(empId || "").trim();
    if (!value) return;
    state.selectedEmpId = value;
    if (empSelect) {
      renderEmpOptions();
      empSelect.value = value;
    }
    if (empDirectInput) empDirectInput.value = value;
    loadView().then(() => {
      document
        .querySelector(".att-profile")
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  async function loadView() {
    try {
      let payload;

      if (adminMode && !ownEmpId) {
        if (!state.selectedEmpId) {
          // 선택이 비면 전부 지운다 — 카드 하나라도 남으면 앞사람 숫자를 새 사번의
          // 것으로 읽는다(clearView 주석 참고).
          clearView();
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

      if (!payload) { clearView(); return; }

      hideMonthMissing();
      state.availableMonths = payload.available_months || state.availableMonths;
      if (adminMode && state.selectedEmpId && !payload.profile) {
        // 프로필이 없으면 나머지 수치도 이 사번의 것이 아니다 — 통째로 비운다.
        clearView();
        window.IRMS?.notify?.(
          "해당 사번의 근태 정보를 찾지 못했습니다.",
          "warn"
        );
        return;
      }

      renderProfile(payload.profile);
      renderSummary(payload.summary);
      renderAnnualSummary(payload.annual_summary);
      renderRows(payload.rows);
      renderPending(payload.rows);
      updateMonthNav();
    } catch (error) {
      const message = String(error.message || error);
      if (message.includes("MONTH_FILE_NOT_FOUND")) {
        clearView();
        showMonthMissing(error.detail);
      } else if (message.includes("FILE_LOCKED_RETRY")) {
        clearView();
        window.IRMS?.notify?.(
          "엑셀 파일이 열려 있습니다. 잠시 후 다시 시도해 주세요.",
          "error"
        );
      } else {
        clearView();
        window.IRMS?.notify?.(`조회 실패: ${message}`, "error");
      }
    }
  }

  // 개인정보 오귀속 방지 — 다른 직원/다른 달을 불러오기 전과 실패 시 화면을 비운다.
  // 이게 없으면 오타 사번을 조회했을 때 이름만 바뀌고 지각·연차·상세는 앞사람 것이
  // 그대로 남아, 책임자가 그 숫자를 새 사번의 것으로 읽는다.
  function clearView() {
    renderProfile(null);
    renderSummary({});
    renderAnnualSummary({});
    renderRows([]);
    renderPending([]);
  }

  function hideMonthMissing() {
    if (!monthMissing) return;
    monthMissing.hidden = true;
    if (monthMissingBtn) {
      monthMissingBtn.hidden = true;
      delete monthMissingBtn.dataset.month;
    }
    monthLabel.textContent = state.month;
  }

  // 월 파일 없음 안내 — detail 은 {code, requested_month, available_months} 구조체다.
  // 구형(문자열 "MONTH_FILE_NOT_FOUND") 응답도 방어한다: 그때는 이미 알고 있는
  // available_months 로 대체해 이동 버튼을 살린다.
  function showMonthMissing(detail) {
    const requested =
      (detail && typeof detail === "object" && detail.requested_month) ||
      state.month;
    const months =
      detail && typeof detail === "object" && Array.isArray(detail.available_months)
        ? detail.available_months
        : state.availableMonths || [];

    if (months.length) {
      state.availableMonths = months;
    }
    updateMonthNav();
    monthLabel.textContent = `${requested} (파일 없음)`;

    if (!monthMissing || !monthMissingText) return;

    const fallback = (state.availableMonths || []).filter(
      (month) => month !== requested
    )[0];
    monthMissingText.textContent = fallback
      ? `${requested} 파일이 아직 없습니다 (ERP 배치 전).`
      : `${requested} 파일이 아직 없습니다 (ERP 배치 전). 볼 수 있는 다른 달도 없습니다.`;

    if (monthMissingBtn) {
      if (fallback) {
        monthMissingBtn.textContent = `${fallback} 보기`;
        monthMissingBtn.dataset.month = fallback;
        monthMissingBtn.hidden = false;
      } else {
        monthMissingBtn.hidden = true;
        delete monthMissingBtn.dataset.month;
      }
    }
    monthMissing.hidden = false;
  }

  function moveMonth(delta) {
    const list = state.availableMonths || [];
    const idx = list.indexOf(state.month);
    if (idx === -1) {
      const target = nearestMonth(delta);
      if (target) goToMonth(target);
      return;
    }

    const nextIdx = idx - delta;
    if (nextIdx < 0 || nextIdx >= list.length) return;

    state.month = list[nextIdx];
    refreshView();
  }

  function goToMonth(month) {
    const value = String(month || "").trim();
    if (!value) return;
    state.month = value;
    refreshView();
  }

  // 근태 엑셀 열 인식 상태 — 책임자에게만, 이상이 있을 때만 띄운다.
  // ERP 가 열 순서를 바꾸면 값이 조용히 어긋나는데(2026-06 실제 발생), 지금까지 그
  // 경고는 서버 로그에만 남아 아무도 보지 않았다(2026-08-08).
  async function checkHeaderMapping() {
    const banner = document.getElementById("att-header-banner");
    const text = document.getElementById("att-header-banner-text");
    if (!banner || !text) return;
    try {
      const res = await fetch(
        `/api/attendance/admin/header-check?month=${encodeURIComponent(state.month || "")}`,
        { credentials: "same-origin" },
      );
      if (!res.ok) { banner.hidden = true; return; }
      const data = await res.json();
      const bad = (data.files || []).filter((f) => !f.ok);
      if (!bad.length) { banner.hidden = true; return; }
      const parts = bad.map((f) => {
        if (f.error) return `${f.file}: 읽지 못함(${f.error})`;
        if (f.fallback) return `${f.file}: 헤더를 인식하지 못해 예전 열 순서로 읽는 중`;
        return `${f.file}: 열 ${f.missing_optional.join(", ")} 을(를) 찾지 못함`;
      });
      text.textContent =
        `근태 엑셀의 열 인식에 문제가 있습니다 — ${parts.join(" · ")}. `
        + "표시된 값이 실제와 다를 수 있으니 ERP 내보내기 형식을 확인하세요.";
      banner.hidden = false;
    } catch (_e) {
      banner.hidden = true;   // 진단 실패가 근태 조회를 막지 않는다
    }
  }

  async function refreshView() {
    if (adminMode) {
      await loadEmployeesForAdmin();
      checkHeaderMapping();   // 조회를 기다리게 하지 않는다
      loadAnomalyPanel();     // 이상 목록도 개인 조회를 막지 않는다
    }
    await loadView();
  }

  monthPrev?.addEventListener("click", () => moveMonth(-1));
  monthNext?.addEventListener("click", () => moveMonth(1));
  monthMissingBtn?.addEventListener("click", () => {
    goToMonth(monthMissingBtn.dataset.month || "");
  });

  empFilterInput?.addEventListener("input", renderEmpOptions);

  pendingCard?.addEventListener("click", scrollToFirstIssue);
  pendingCard?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      scrollToFirstIssue();
    }
  });

  function anomalyRowFrom(target) {
    return target instanceof Element
      ? target.closest(".att-anomaly-row")
      : null;
  }

  anomalyBody?.addEventListener("click", (event) => {
    const row = anomalyRowFrom(event.target);
    if (row) selectEmployeeFromPanel(row.dataset.empId);
  });
  anomalyBody?.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    const row = anomalyRowFrom(event.target);
    if (!row) return;
    event.preventDefault();
    selectEmployeeFromPanel(row.dataset.empId);
  });

  // 버튼 자체가 본인 사번 세션일 때만 렌더된다(템플릿 {% if emp_id %}) — 여기 가드는
  // 혹시 남아 있는 DOM 을 위한 안전망일 뿐이다.
  changePwBtn?.addEventListener("click", () => {
    if (!ownEmpId) return;
    // 세션 가드의 pagehide beacon 이 이 이동을 '탭 닫힘'으로 오인해 로그아웃하지 않게.
    window.IRMS?.attendanceSession?.allowNavigation?.();
    window.location.assign("/attendance/change-password");
  });

  logoutBtn?.addEventListener("click", async () => {
    try {
      await apiPost("/api/attendance/logout", {});
    } catch (_) {
      // Ignore logout errors and continue redirect.
    }
    if (adminMode) {
      // 공용 PC: 근태 세션만 지우면 책임자의 IRMS 세션이 살아남고,
      // /attendance/login 은 책임자 세션을 보면 곧장 책임자 모드로 되돌린다
      // (pages.py attendance_login_page). 그래서 다음 직원은 로그인 창을 아예
      // 만나지 못했다 — 책임자 세션까지 함께 끝낸다.
      try {
        await apiPost("/api/auth/logout", {});
      } catch (_) {
        // Ignore logout errors and continue redirect.
      }
    }
    window.location.assign("/attendance/login");
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
    // sessionStorage 를 쓴다 — localStorage 로 두면 ×를 한 번 누른 순간 그 PC 에서
    // 다시는 경고가 뜨지 않아, 공용 PC 에서 임시 비밀번호가 무기한 유지된다.
    // 세션 한정으로 두어 다음 로그인 때 다시 알린다.
    const storageKey = empId ? `irms_att_reset_dismissed_${empId}` : "";
    // 쿠키 차단·시크릿 모드에서는 sessionStorage 접근 자체가 예외를 던진다. 그때는
    // 배너를 못 숨기는 게 맞지, 초기화가 통째로 죽어 경고가 안 뜨는 건 안 된다
    // (임시 비밀번호를 쓰는 중이라는 경고라 놓치면 그대로 남는다).
    const readDismissed = () => {
      if (!storageKey) return false;
      try {
        return sessionStorage.getItem(storageKey) === "1";
      } catch (error) {
        return false;
      }
    };

    // 배너는 서버 게이트({% if password_reset_required %})로 이미 노출된 상태다.
    // JS 가 안 돌아도 경고는 보여야 하므로 '보이기'는 하지 않고, 이번 세션에서
    // 이미 닫은 경우에만 숨긴다(공용 PC 대비 sessionStorage 한정).
    if (readDismissed()) {
      banner.hidden = true;
    }

    // 저장소 일원화(2026-08-14 검토 9번) — 이 플래그의 정본은 sessionStorage 다.
    // 예전 판이 localStorage 에 같은 키를 남겼고(비밀번호 변경 화면의 청소 코드도
    // localStorage 를 지운다), 그 잔재가 남아 있으면 어느 쪽이 진짜인지 헷갈린다.
    // 여기서 통째로 걷어내 sessionStorage 한 곳만 남긴다.
    try {
      Object.keys(localStorage)
        .filter((key) => key.startsWith("irms_att_reset_dismissed_"))
        .forEach((key) => localStorage.removeItem(key));
    } catch (error) {
      /* 접근이 막힌 브라우저면 지울 잔재도 없다 */
    }

    dismissBtn?.addEventListener("click", () => {
      banner.hidden = true;
      if (!storageKey) return;
      try {
        sessionStorage.setItem(storageKey, "1");
      } catch (error) {
        /* 저장 못 해도 이번 화면에서는 이미 숨겼다 */
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
    explainIssue,
    issueListHtml,
    renderPending,
    renderAnomalyPanel,
    showMonthMissing,
    nearestMonth,
    renderEmpOptions,
    state,
  };

  (async function init() {
    initResetBanner();
    await refreshView();
  })();
})();
