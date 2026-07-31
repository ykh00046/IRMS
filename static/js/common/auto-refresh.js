/**
 * auto-refresh.js — 새 서버 버전 배포 감지 후 클라이언트 자동 새로고침.
 *
 * 운영 PC(serve.py)는 주기적으로 git pull 후 서버를 재시작한다. 이미 열린 페이지는
 * 재시작 전까지 옛 정적 자산을 붙들고 있어 수동 F5 가 필요했다. 이 모듈이
 * GET /api/version 을 폴링해 마커가 바뀌면(=새 배포) 안전한 시점에 새로고침한다.
 *
 * 안전 원칙(단일 경로): 새 버전을 감지하면 항상 배너를 먼저 띄우고, 실제 새로고침은
 * '안전한 시점'에만 한다 — 진행 중인 배합 초안이 없고(단건 irms.blend.draft·이어서
 * irms.blend.cont.draft 모두 확인) 사용자가 ~30초 무활동일 때. 사용자가 실제로
 * 조작 중일 때는 절대 강제 새로고침하지 않는다(레시피/점도 폼의 미저장 입력 보호).
 * window-guard 로 막힌 중복 창은 아예 새로고침을 예약하지 않는다.
 *
 * 배합 초안은 localStorage 에 저장·복구되고(readDraft 는 24h 보존) 진행 여부는
 * '최근 저장(10분)'이 아니라 '존재'로 판단한다 — 60분 유휴 지평보다 짧은 신선도
 * 창을 쓰면 계량 중이지만 잠시 손을 뗀 초안이 만료된 것처럼 보이는 문제가 있었다.
 *
 * 의존성 없음 — 안내는 하단 고정 배너로만 표시하고, 새로고침은 안전 게이트가 판단한다.
 */
(function () {
  "use strict";

  var VERSION_URL = "/api/version";
  var POLL_MS = 3 * 60 * 1000;        // 폴링 주기 ~3분
  // 배합 임시저장 키 — 단건/이어서 두 가지 모두 진행 중으로 본다.
  var DRAFT_KEYS = ["irms.blend.draft", "irms.blend.cont.draft"];
  var DRAFT_TTL_MS = 24 * 60 * 60 * 1000; // 초안 보존 지평(readDraft 24h 와 동일)
  var IDLE_MS = 30 * 1000;            // 무활동 30초면 안전한 새로고침 시점

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

  function blocked() {
    // window-guard 로 막힌 중복 창 — 서버 세션을 공유하므로 새로고침을 예약하지 않는다.
    return Boolean(window.IRMS && window.IRMS.blendWindowBlocked);
  }

  function draftExists(key) {
    try {
      var raw = window.localStorage.getItem(key);
      if (!raw) return false;
      var d = JSON.parse(raw);
      if (!d) return false;
      // 존재로 판단(10분 신선도 창 폐기). savedAt 이 있으면 24h 보존 지평만 적용해
      // 버려진 초안이 영원히 새로고침을 막지 않게 한다.
      if (d.savedAt) {
        var age = Date.now() - Date.parse(d.savedAt);
        return !(age >= 0 && age >= DRAFT_TTL_MS);
      }
      return true;
    } catch (_e) {
      return false;
    }
  }

  function draftInProgress() {
    for (var i = 0; i < DRAFT_KEYS.length; i++) {
      if (draftExists(DRAFT_KEYS[i])) return true;
    }
    return false;
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
    // 단일 안전 경로: 진행 중 배합 초안이 없고 사용자가 ~30초 무활동일 때만 새로고침.
    // 사용자가 실제 조작 중(마우스/키/포인터/스크롤)이면 절대 강제 새로고침하지 않는다 —
    // 배합뿐 아니라 레시피/점도 폼의 미저장 입력도 이 무활동 게이트로 보호된다.
    var lastActivity = Date.now();
    function bump() {
      lastActivity = Date.now();
    }
    var events = ["mousemove", "keydown", "pointerdown", "touchstart", "scroll"];
    events.forEach(function (ev) {
      window.addEventListener(ev, bump, { passive: true });
    });

    var guard = window.setInterval(function () {
      if (draftInProgress()) return;              // 배합 진행 중 — 대기
      if (Date.now() - lastActivity < IDLE_MS) return; // 조작 중 — 대기
      window.clearInterval(guard);
      doReload();
    }, 5000);
  }

  function handleNewVersion() {
    stopPolling(); // 새로고침 예약됨 — 루프 방지
    // 항상 배너를 먼저 띄우고, 안전한 시점(초안 없음 + 무활동)에만 새로고침한다.
    showBanner("새 버전이 배포되었습니다 — 작업을 잠시 멈추면 자동으로 반영됩니다");
    scheduleSafeReload();
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
        // window-guard 로 막힌 중복 창은 새로고침을 예약하지 않는다(주 창 세션 보호).
        // baseline 을 진전시키지 않고 폴링을 유지하다가, 차단이 풀리면 그때 예약한다.
        if (blocked()) return;
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
