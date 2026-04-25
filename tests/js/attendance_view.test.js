const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

function createClassList() {
  const values = new Set();
  return {
    add(...tokens) {
      tokens.forEach((token) => values.add(token));
    },
    remove(...tokens) {
      tokens.forEach((token) => values.delete(token));
    },
    toggle(token, force) {
      if (force === true) {
        values.add(token);
        return true;
      }
      if (force === false) {
        values.delete(token);
        return false;
      }
      if (values.has(token)) {
        values.delete(token);
        return false;
      }
      values.add(token);
      return true;
    },
    contains(token) {
      return values.has(token);
    },
    toString() {
      return Array.from(values).join(" ");
    },
  };
}

function createElement(id = "") {
  return {
    id,
    value: "",
    hidden: false,
    disabled: false,
    innerHTML: "",
    textContent: "",
    className: "",
    dataset: {},
    children: [],
    appendChild(child) {
      this.children.push(child);
      return child;
    },
    addEventListener() {},
    classList: createClassList(),
  };
}

function createContext() {
  const elementIds = [
    "att-month-label",
    "att-month-prev",
    "att-month-next",
    "att-profile-name",
    "att-profile-meta",
    "att-admin-picker",
    "att-emp-select",
    "att-emp-direct",
    "att-emp-direct-btn",
    "att-change-pw-btn",
    "att-logout-btn",
    "att-late",
    "att-early-leave",
    "att-outing",
    "att-wd-normal",
    "att-wd-overtime",
    "att-wd-night",
    "att-wd-early",
    "att-hd-normal",
    "att-hd-overtime",
    "att-hd-night",
    "att-hd-early",
    "att-year-late-count",
    "att-year-late-hours",
    "att-year-late-scope",
    "att-year-leave-days",
    "att-year-leave-scope",
    "att-year-leave-breakdown",
    "att-rows-body",
    "att-reset-banner",
    "att-reset-banner-dismiss",
  ];

  const elements = Object.fromEntries(
    elementIds.map((id) => [id, createElement(id)])
  );
  elements["att-reset-banner"].dataset.empId = "171013";

  const fetch = async () => ({
    ok: true,
    json: async () => ({
      profile: null,
      summary: {},
      annual_summary: {},
      rows: [],
      available_months: ["2026-04"],
    }),
  });

  const localStorage = {
    getItem() {
      return null;
    },
    setItem() {},
  };

  const context = {
    console,
    fetch,
    localStorage,
    window: {
      location: { origin: "http://127.0.0.1:8000", assign() {} },
      IRMS: { notify() {} },
      fetch,
      localStorage,
    },
    document: {
      body: {
        dataset: {
          adminMode: "false",
          empId: "171013",
        },
      },
      cookie: "",
      getElementById(id) {
        return elements[id] || null;
      },
      createElement() {
        return createElement();
      },
    },
    URL,
  };
  context.window.window = context.window;
  context.window.document = context.document;
  context.window.localStorage = localStorage;
  context.IRMS = context.window.IRMS;
  context.__elements = elements;
  return context;
}

async function loadAttendanceJs() {
  const code = fs.readFileSync("static/js/attendance.js", "utf8");
  const context = createContext();
  vm.runInNewContext(code, context, { filename: "static/js/attendance.js" });
  await new Promise((resolve) => setImmediate(resolve));
  return {
    hooks: context.window.IRMS.__attendanceTest,
    elements: context.__elements,
  };
}

async function testFormatsAttendanceNumbersWithoutRounding() {
  const { hooks } = await loadAttendanceJs();
  assert.equal(hooks.formatFixed(1.239), "1.23");
  assert.equal(hooks.formatHours(8), "8.00h");
  assert.equal(hooks.formatDays(0.259), "0.25");
  assert.match(hooks.hoursCell(0), /--/);
  assert.match(hooks.hoursCell(2.759), /2\.75/);
}

async function testWeekdayAndRestDayClassesAreDistinct() {
  const { hooks } = await loadAttendanceJs();
  assert.equal(hooks.isWeekdayType("\uD3C9\uC77C2"), true);
  assert.equal(
    hooks.rowClassName({
      weekday: "\uAE08",
      day_type: "\uD3C9\uC77C2",
      attendance_code: "",
      has_issue: false,
    }),
    ""
  );

  const restClassName = hooks.rowClassName({
    weekday: "\uC218",
    day_type: "\uC8FC\uD734",
    attendance_code: "",
    has_issue: false,
  });
  assert.match(restClassName, /att-day-rest/);
  assert.doesNotMatch(restClassName, /att-day-holiday/);
}

async function testAttendanceCodeAndIssueMarkerAreRenderable() {
  const { hooks } = await loadAttendanceJs();
  assert.match(
    hooks.attendanceCodeCell({ attendance_code: "\uC5F0\uCC28" }),
    /att-code-pill-leave/
  );
  assert.match(
    hooks.dateCell({
      date: "2026-04-24",
      has_issue: true,
      issues: ["\uC9C0\uAC01 \uBBF8\uCC98\uB9AC"],
    }),
    /att-issue-chip/
  );
}

async function testAnnualSummaryRendersLeaveBreakdown() {
  const { hooks, elements } = await loadAttendanceJs();
  hooks.renderAnnualSummary({
    year: 2026,
    months_count: 4,
    available_months_count: 4,
    skipped_months: [],
    late_count: 3,
    late_total: 1.759,
    annual_leave_days: 1.75,
    annual_leave_full_days: 1.0,
    annual_leave_half_days: 0.5,
    annual_leave_quarter_days: 0.25,
  });

  assert.equal(elements["att-year-late-count"].textContent, "3");
  assert.equal(elements["att-year-late-hours"].textContent, "1.75h");
  assert.equal(elements["att-year-leave-days"].textContent, "1.75");
  assert.match(
    elements["att-year-leave-breakdown"].textContent,
    /\uC5F0\uCC28 1\.00.*\uBC18\uCC28 0\.50.*\uBC18\uBC18\uCC28 0\.25/
  );
}

(async () => {
  await testFormatsAttendanceNumbersWithoutRounding();
  await testWeekdayAndRestDayClassesAreDistinct();
  await testAttendanceCodeAndIssueMarkerAreRenderable();
  await testAnnualSummaryRendersLeaveBreakdown();
  console.log("attendance_view.test.js passed");
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
