(function () {
  "use strict";

  const VISIBLE_TIMEOUT_MS = 3 * 60 * 1000;
  const HIDDEN_TIMEOUT_MS = 30 * 1000;
  const TICK_MS = 1000;
  const LOGOUT_URL = "/api/blend/session/logout";
  const LOGIN_URL = "/blend/login";

  let deadline = Date.now() + VISIBLE_TIMEOUT_MS;
  let firedLogout = false;

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function workerName() {
    const workerInput = document.getElementById("blend-worker");
    return (workerInput && workerInput.value ? workerInput.value : "작업자").trim();
  }

  function buildBadge() {
    const badge = document.createElement("button");
    badge.id = "blend-session-badge";
    badge.className = "session-countdown-badge";
    badge.type = "button";
    badge.setAttribute("aria-live", "polite");
    badge.title = "남은 시간 안에 활동이 없으면 자동 로그아웃됩니다. 클릭하면 즉시 로그아웃.";
    badge.addEventListener("click", manualLogoutAndRedirect);
    document.body.appendChild(badge);
    return badge;
  }

  function formatRemaining(ms) {
    const total = Math.max(0, Math.ceil(ms / 1000));
    const min = Math.floor(total / 60);
    const sec = total % 60;
    return `${min}:${sec.toString().padStart(2, "0")}`;
  }

  function applyUrgency(badge, ms) {
    badge.classList.toggle("is-warning", ms <= 30 * 1000 && ms > 10 * 1000);
    badge.classList.toggle("is-critical", ms <= 10 * 1000);
  }

  function resetDeadline() {
    if (firedLogout) return;
    deadline = Date.now() + (document.hidden ? HIDDEN_TIMEOUT_MS : VISIBLE_TIMEOUT_MS);
  }

  function shortenForHidden() {
    if (firedLogout) return;
    const hiddenDeadline = Date.now() + HIDDEN_TIMEOUT_MS;
    if (hiddenDeadline < deadline) deadline = hiddenDeadline;
  }

  function redirectToLogin(reason) {
    const next = `${window.location.pathname}${window.location.search}`;
    window.location.replace(
      `${LOGIN_URL}?reason=${encodeURIComponent(reason)}&next=${encodeURIComponent(next)}`,
    );
  }

  function fireLogout(reason) {
    if (firedLogout) return;
    firedLogout = true;
    try {
      navigator.sendBeacon(LOGOUT_URL, new Blob([], { type: "application/json" }));
    } catch (_error) {
      fetch(LOGOUT_URL, { method: "POST", credentials: "same-origin", keepalive: true });
    }
    redirectToLogin(reason);
  }

  function fireLogoutSilently() {
    if (firedLogout) return;
    firedLogout = true;
    try {
      navigator.sendBeacon(LOGOUT_URL, new Blob([], { type: "application/json" }));
    } catch (_error) {
      fetch(LOGOUT_URL, { method: "POST", credentials: "same-origin", keepalive: true });
    }
  }

  function manualLogoutAndRedirect() {
    if (firedLogout) return;
    firedLogout = true;
    fetch(LOGOUT_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-csrftoken": csrfToken() },
      keepalive: true,
    }).finally(() => redirectToLogin("manual"));
  }

  function tick(badge) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      fireLogout("idle");
      return;
    }
    badge.textContent = `${workerName()} · 자동 로그아웃 ${formatRemaining(remaining)}`;
    applyUrgency(badge, remaining);
  }

  function attach() {
    if (document.getElementById("blend-session-badge")) return;
    const badge = buildBadge();
    const activityEvents = ["mousemove", "keydown", "touchstart", "scroll", "click"];
    let lastReset = 0;

    function onActivity() {
      const now = Date.now();
      if (now - lastReset < 2000) return;
      lastReset = now;
      resetDeadline();
    }

    activityEvents.forEach((eventName) =>
      window.addEventListener(eventName, onActivity, { passive: true })
    );
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) shortenForHidden();
      else resetDeadline();
    });
    window.addEventListener("pagehide", fireLogoutSilently);
    tick(badge);
    window.setInterval(() => tick(badge), TICK_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
