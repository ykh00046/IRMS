/**
 * ui.js — DOM helpers (loading overlays, toasts, login/logout binders).
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   showLoading, hideLoading, btnLoading, notify, bindLoginForm,
 *   initTableScrollHints
 *
 * Side effects (executed on script parse):
 *   bindLogoutButton() — attaches click handler to #logout-btn if present
 *
 * Dependencies: core.js (safeNextUrl), api-users.js (logout for bindLogoutButton).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { safeNextUrl } = IRMS._core;

  function showLoading(el) {
    if (!el) return null;
    let overlay = el.querySelector(".loading-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "loading-overlay";
      overlay.innerHTML = '<div class="spinner"></div>';
      el.style.position = el.style.position || "relative";
      el.appendChild(overlay);
    }
    requestAnimationFrame(() => overlay.classList.add("active"));
    return overlay;
  }

  function hideLoading(el) {
    if (!el) return;
    const overlay = el.querySelector(".loading-overlay");
    if (overlay) overlay.classList.remove("active");
  }

  function btnLoading(btn, loading) {
    if (!btn) return;
    if (loading) {
      btn._origHTML = btn.innerHTML;
      btn.innerHTML = '<div class="spinner"></div>';
      btn.classList.add("loading");
      btn.disabled = true;
    } else {
      btn.innerHTML = btn._origHTML || btn.innerHTML;
      btn.classList.remove("loading");
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
    // 오류·강조 경고(잘못 계량, 수기 입력 등 중대한 메시지)는 더 오래 띄운다.
    const ttl = /error|big/.test(type || "") ? 6000 : 2800;
    window.setTimeout(() => {
      node.remove();
    }, ttl);
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
        await IRMS.logout();
        window.location.assign("/");
      } catch (error) {
        notify(`로그아웃 실패: ${error.message}`, "error");
        logoutBtn.disabled = false;
      }
    });
  }

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
        window.location.assign(safeNextUrl(nextUrl, opts.defaultNext));
      } catch (error) {
        setError(error.message === "INVALID_CREDENTIALS" ? opts.failMsg : error.message);
        if (submitBtn) submitBtn.disabled = false;
      }
    });
  }

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

  Object.assign(IRMS, {
    showLoading,
    hideLoading,
    btnLoading,
    notify,
    bindLoginForm,
    initTableScrollHints,
  });

  // Side effect: attach logout button if present on the page.
  bindLogoutButton();
})();
