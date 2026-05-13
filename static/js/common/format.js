/**
 * format.js — pure formatters and storage utilities.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). See docs/01-plan/features/split-common-js.plan.md.
 *
 * Exports (window.IRMS.*):
 *   statusLabel, statusClass, formatDateTime, toDateOnly, formatValue,
 *   escapeHtml, debounce, loadPreference, savePreference, clearPreference,
 *   colorLabel
 *
 * Side effects: none.
 * Dependencies: core.js (for window.IRMS namespace).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

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

  function colorLabel(color) {
    var map = {
      black: "BLACK", red: "RED", blue: "BLUE", yellow: "YELLOW",
      none: "기타", all: "전체",
    };
    return map[color] || "기타";
  }

  Object.assign(IRMS, {
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
    colorLabel,
  });
})();
