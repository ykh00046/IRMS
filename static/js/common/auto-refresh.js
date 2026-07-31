/**
 * auto-refresh.js — 새 서버 버전 배포 감지 후 클라이언트 자동 새로고침.
 *
 * 운영 PC(serve.py)는 주기적으로 git pull 후 서버를 재시작한다. 이미 열린 페이지는
 * 재시작 전까지 옛 정적 자산을 붙들고 있어 수동 F5 가 필요했다. 이 모듈이
 * GET /api/version 을 폴링해 마커가 바뀌면(=새 배포) 안전한 시점에 새로고침한다.
 *
 * 배합 화면은 진행 중 계량이 localStorage(irms.blend.draft)에 저장·복구되므로 안전하다.
 * 다만 작업 중 강제 리로드는 피하고, 초안이 사라지거나 무활동 상태가 될 때 반영한다.
 *
 * 의존성: (선택) IRMS.notify. 없으면 토스트만 생략, 동작은 유지.
 */
(function () {
  "use strict";

  var VERSION_URL = "/api/version";
  var POLL_MS = 3 * 60 * 1000;        // 폴링 주기 ~3분
  var DRAFT_KEY = "irms.blend.draft"; // 배합 임시저장 키
  var DRAFT_FRESH_MS = 10 * 60 * 1000; // 이 시간 내 저장된 초안 = 작업 진행 중
  var IDLE_MS = 30 * 1000;            // 무활동 30초면 안전한 새로고침 시점
  var RELOAD_DELAY_MS = 1500;         // 작업 중 아닐 때 부드러운 지연

  var baseline = null; // 최초 관측한 서버 버전(기준값)
  var pollTimer = null;
  var stopped = false; // 새로고침 예약/수행 후 폴링 중단 플래그

  function fetchVersion() {
    // 서버 재시작 중 연결 거부 등은 조용히 무시하고 다음 틱에 재시도한다.
    return fetch(VERSION_URL, { credentials: "same-origin", cache: "no-store" })
      .then(function (res) {
        if (!res.ok) return null;
        return res.json();
      })
      .then(function (data) {
        return data && data.version ? String(data.version) : null;
      })
      .catch(function () {
        return null;
      });
  }

  function draftInProgress() {
    try {
      var raw = window.localStorage.getItem(DRAFT_KEY);
      if (!raw) return false;
      var d = JSON.parse(raw);
      if (!d || !d.savedAt) return false;
      var age = Date.now() - Date.parse(d.savedAt);
      return age >= 0 && age < DRAFT_FRESH_MS;
    } catch (_e) {
      return false;
    }
  }

  function toast(message, type) {
    if (window.IRMS && typeof window.IRMS.notify === "function") {
      window.IRMS.notify(message, type || "info");
    }
  }

  function showBanner(message) {
    if (document.getElementById("version-refresh-banner")) return;
    var bar = document.createElement("div");
    bar.id = "version-refresh-banner";
    bar.setAttribute("role", "status");
    bar.textContent = message;
    bar.style.cssText = [
      "position:fixed",
      "left:0",
      "right:0",
      "bottom:0",
      "z-index:9999",
      "padding:10px 16px",
      "text-align:center",
      "background:#1f2a44",
      "color:#fff",
      "font-size:14px",
      "line-height:1.4",
      "box-shadow:0 -2px 8px rgba(0,0,0,.25)"
    ].join(";");
    document.body.appendChild(bar);
  }

  function stopPolling() {
    stopped = true;
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  function doReload() {
    stopPolling();
    window.location.reload();
  }

  function scheduleSafeReload() {
    // 배합 작업 중: 초안이 사라지거나(저장/취소) 30초 무활동이면 새로고침.
    var lastActivity = Date.now();
    function bump() {
      lastActivity = Date.now();
    }
    var events = ["mousemove", "keydown", "pointerdown", "touchstart", "scroll"];
    events.forEach(function (ev) {
      window.addEventListener(ev, bump, { passive: true });
    });

    var guard = window.setInterval(function () {
      if (!draftInProgress()) {
        window.clearInterval(guard);
        doReload();
        return;
      }
      if (Date.now() - lastActivity >= IDLE_MS) {
        window.clearInterval(guard);
        doReload();
      }
    }, 5000);
  }

  function handleNewVersion() {
    stopPolling(); // 새로고침 예약됨 — 루프 방지
    if (draftInProgress()) {
      showBanner("새 버전이 배포되었습니다 — 작업을 마치면 자동으로 반영됩니다");
      scheduleSafeReload();
    } else {
      toast("새 버전으로 새로고침합니다", "info");
      window.setTimeout(doReload, RELOAD_DELAY_MS);
    }
  }

  function tick() {
    if (stopped) return;
    fetchVersion().then(function (v) {
      if (stopped || !v) return; // 실패 → 다음 틱 재시도
      if (baseline === null) {
        baseline = v; // 최초 기준값 확보
        return;
      }
      if (v !== baseline) {
        handleNewVersion();
      }
    });
  }

  function start() {
    tick(); // 최초 1회: 기준값 확보
    pollTimer = window.setInterval(tick, POLL_MS);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
