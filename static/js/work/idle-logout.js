/**
 * idle-logout module — 담당자 30분 비활동 자동 로그아웃.
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.createIdleLogout(ctx)
 * Returns: { start, stop }
 * ctx deps: 없음 (글로벌 document 이벤트만)
 *
 * 동작: 활동 이벤트(mousemove/mousedown/keydown/touchstart/scroll) 발생 시
 * 30분 타이머를 리셋. 만료 시 IRMS.logout() 후 /weighing/select로 이동.
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  const IDLE_TIMEOUT_MS = 30 * 60 * 1000;
  const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "touchstart", "scroll"];

  NS.work.createIdleLogout = function (_ctx) {
    let idleTimer = null;
    let started = false;

    function resetIdleTimer() {
      if (idleTimer) {
        clearTimeout(idleTimer);
      }
      idleTimer = setTimeout(async () => {
        try {
          await IRMS.logout();
        } catch (_e) {
          /* ignore */
        }
        window.location.assign("/weighing/select");
      }, IDLE_TIMEOUT_MS);
    }

    function start() {
      if (started) {
        return;
      }
      started = true;
      ACTIVITY_EVENTS.forEach((evt) => {
        document.addEventListener(evt, resetIdleTimer, { passive: true });
      });
      resetIdleTimer();
    }

    function stop() {
      if (idleTimer) {
        clearTimeout(idleTimer);
        idleTimer = null;
      }
      ACTIVITY_EVENTS.forEach((evt) => {
        document.removeEventListener(evt, resetIdleTimer);
      });
      started = false;
    }

    return { start, stop };
  };
})();
