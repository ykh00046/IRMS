// IRMS attendance session guard - protects shared field PCs from forgotten logouts.
//
//   A) pagehide / beforeunload  → navigator.sendBeacon('/api/attendance/logout')
//      so closing the tab or window terminates the session immediately.
//   B) On-screen countdown badge with activity reset (mousemove / keydown /
//      touchstart / scroll). Default 3 minutes when the tab is visible.
//   C) When the tab becomes hidden, the deadline collapses to 30 s so a worker
//      who alt-tabs away or locks the screen does not leave a live session.
//
// At T=0 the badge redirects to /attendance/login after issuing the logout.
// Server-side 5 min idle remains as the safety net for cases where this
// script never gets a chance to run (browser crash, OS kill, etc).
//
// 책임자(IRMS 관리자 세션)는 제외한다 — 책임자에게는 IRMS 쪽 유휴 정책이 따로 있고,
// 여기 3분 타이머가 겹치면 이상 목록을 읽는 도중에 튕겨 나간다(2026-08-14 검토 5번).
// 판별은 GET /api/attendance/session 의 admin_mode 로 하고, 서버 렌더가 이미 알려준
// data-admin-mode 를 먼저 보아 배지가 잠깐 떴다 사라지는 깜빡임을 막는다.
// 직원 세션 동작은 그대로다.

(function () {
  "use strict";

  const VISIBLE_TIMEOUT_MS = 3 * 60 * 1000;   // 3분
  const HIDDEN_TIMEOUT_MS = 30 * 1000;        // 30초
  const TICK_MS = 1000;
  const LOGOUT_URL = "/api/attendance/logout";
  const LOGIN_URL = "/attendance/login";

  let deadline = Date.now() + VISIBLE_TIMEOUT_MS;
  let tickHandle = null;
  let firedLogout = false;
  // 앱 안에서 화면을 옮기는 중(근태 ↔ 비밀번호 변경)이면 pagehide 가 떠도 로그아웃
  // beacon 을 쏘지 않는다. 종전엔 화면 이동도 '탭 닫힘'으로 보고 세션을 지워,
  // 임시 비밀번호로 로그인한 직원이 변경 화면에서 [변경하기]를 누르는 순간
  // 401(세션 만료)을 받았다 — 아무리 빨리 해도 같았다(2026-08-28 신입 개통 사고).
  let inAppNavigation = false;

  function allowNavigation() {
    inAppNavigation = true;
    // 이동이 어떤 이유로든 무산되면 다시 탭 닫힘 보호로 돌아간다.
    window.setTimeout(() => { inAppNavigation = false; }, 5000);
  }

  window.IRMS = window.IRMS || {};
  window.IRMS.attendanceSession = { allowNavigation };

  function csrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function buildBadge() {
    const badge = document.createElement("div");
    badge.id = "att-session-badge";
    badge.setAttribute("role", "status");
    badge.setAttribute("aria-live", "polite");
    badge.title = "남은 시간 안에 활동이 없으면 자동 로그아웃됩니다. 클릭하면 즉시 로그아웃.";
    badge.textContent = "자동 로그아웃 --:--";
    badge.addEventListener("click", () => {
      manualLogoutAndRedirect();
    });
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
    const newDeadline = Date.now() + HIDDEN_TIMEOUT_MS;
    if (newDeadline < deadline) deadline = newDeadline;
  }

  function fireLogout(reason) {
    if (firedLogout) return;
    firedLogout = true;
    try {
      const body = new Blob([], { type: "application/json" });
      // sendBeacon ignores custom headers, so this endpoint is CSRF-exempt
      // server-side. The session cookie is sent automatically.
      navigator.sendBeacon(LOGOUT_URL, body);
    } catch (_err) {
      // Swallow - we're about to navigate away anyway.
    }
    window.location.replace(`${LOGIN_URL}?reason=${encodeURIComponent(reason)}`);
  }

  function manualLogoutAndRedirect() {
    if (firedLogout) return;
    firedLogout = true;
    fetch(LOGOUT_URL, {
      method: "POST",
      credentials: "same-origin",
      headers: { "x-csrftoken": csrfToken() },
      keepalive: true,
    }).finally(() => {
      window.location.replace(LOGIN_URL);
    });
  }

  function tick(badge) {
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      fireLogout("idle");
      return;
    }
    badge.textContent = `자동 로그아웃 ${formatRemaining(remaining)}`;
    applyUrgency(badge, remaining);
  }

  function attach() {
    if (document.getElementById("att-session-badge")) return;
    const badge = buildBadge();

    const ACTIVITY_EVENTS = ["mousemove", "keydown", "touchstart", "scroll", "click"];
    let lastReset = 0;
    function onActivity() {
      const now = Date.now();
      // Throttle: only reset every 2s so repeated mousemoves are cheap.
      if (now - lastReset < 2000) return;
      lastReset = now;
      resetDeadline();
    }
    ACTIVITY_EVENTS.forEach((evt) =>
      window.addEventListener(evt, onActivity, { passive: true })
    );

    document.addEventListener("visibilitychange", () => {
      if (document.hidden) shortenForHidden();
      else resetDeadline();
    });

    // (A) Tab/window close → fire-and-forget logout via sendBeacon.
    function onBye() {
      if (firedLogout) return;
      if (inAppNavigation) return;   // 의도한 화면 이동 — 세션을 살려 둔다
      firedLogout = true;
      try {
        const body = new Blob([], { type: "application/json" });
        navigator.sendBeacon(LOGOUT_URL, body);
      } catch (_err) {
        /* nothing we can do during teardown */
      }
    }
    window.addEventListener("pagehide", onBye);
    window.addEventListener("beforeunload", onBye);

    // 같은 출처 링크 클릭(배너의 <a href="/attendance/change-password"> 등)도 앱 안
    // 이동이다 — JS 가 location.assign 하는 곳만 allowNavigation() 을 부르면
    // 템플릿의 일반 링크가 빠진다. capture 단계라 preventDefault 된 클릭은 5초 뒤
    // 자동으로 원래 보호로 돌아간다.
    document.addEventListener(
      "click",
      (event) => {
        const anchor = event.target?.closest?.("a[href]");
        if (!anchor || anchor.target === "_blank" || anchor.hasAttribute("download")) return;
        let url;
        try {
          url = new URL(anchor.getAttribute("href"), window.location.href);
        } catch (_err) {
          return;
        }
        if (url.origin !== window.location.origin) return;
        allowNavigation();
      },
      true
    );

    tick(badge);
    tickHandle = window.setInterval(() => tick(badge), TICK_MS);
  }

  async function isAdminSession() {
    // 서버 렌더 힌트가 책임자라고 말하면 그대로 믿는다(같은 서버 판정이다).
    if (document.body?.dataset?.adminMode === "true") return true;
    try {
      const res = await fetch("/api/attendance/session", {
        credentials: "same-origin",
      });
      if (!res.ok) return false;
      const data = await res.json();
      return data?.admin_mode === true;
    } catch (_err) {
      // 물어보지 못하면 직원으로 본다 — 공용 PC 보호가 우선이다.
      return false;
    }
  }

  async function start() {
    if (await isAdminSession()) return;   // 책임자에겐 타이머를 걸지 않는다
    attach();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
