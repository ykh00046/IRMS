/* 배합 입력 창 단일화 가드 — 한 브라우저에 배합 창은 하나만 쓰도록 안내한다.
 *
 * 배경: 작업자가 A 를 걸어두고 새 창에서 B 를 동시에 계량하면 (1) 저울 PRINT 가 어느
 * 창으로 갈지 보장되지 않고(저울 전용 모드에서 값이 엉뚱한 배합에 들어감), (2) 임시저장이
 * 한 키(irms.blend.draft)를 공유해 서로 덮어써 복구가 오염된다. 이 앱은 '한 브라우저 =
 * 배합 하나'를 전제로 만들어졌으므로, 두 번째 배합 창을 열면 막고 안내한다.
 *
 * 방식: 서버 없이 BroadcastChannel 로 같은 브라우저(같은 origin)의 다른 배합 창과 존재만
 * 주고받는다. 새로 연 창이 기존 창의 응답을 받으면 자기 화면을 덮개로 막는다. 지속 잠금
 * (localStorage lock)을 쓰지 않으므로 '다른 창이 죽었는데 계속 막히는' 오작동이 없다 —
 * 응답이 없으면 막지 않고, 막힌 뒤 상대가 사라지면(닫힘 통지 또는 재확인 무응답) 자동 해제.
 *
 * /blend, /blend/bulk, /blend/continuous 만 이 스크립트를 로드하므로 관리·기록 화면은 무관.
 */
(function () {
  "use strict";
  if (!("BroadcastChannel" in window)) return; // 미지원 환경은 가드 없이 통과(기존 동작).

  var CH = "irms-blend-window";
  var myId = String(Date.now()) + "-" + Math.random().toString(36).slice(2);

  // 비상 예외(정말 오류로 잘못 막혔을 때만) — 눈에 보이는 버튼은 없다. 경고 아이콘을
  // OVERRIDE_TAPS 번 연속 눌러야 코드 입력칸이 나타나고, 관리자만 아는 코드를 넣어야
  // 넘어갈 수 있다. 책임자 혼자서는 방법을 모르게 해서 관리자 문의를 강제하는 소프트
  // 게이트다(현장 꼼수 차단이 목적 — 암호학적 보안이 아니라 사회적 장벽). 코드는 서버
  // (app_settings)에 저장되고 사용자 관리 화면에서 관리자가 변경한다(기본 111111). 여기선
  // 코드를 갖지 않고 서버에 대조만 요청한다(코드가 클라이언트로 내려오지 않음).
  var OVERRIDE_TAPS = 5;

  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  var bc;
  try { bc = new BroadcastChannel(CH); } catch (_e) { return; }

  var others = {};          // otherId -> 마지막 응답 시각(ms)
  var blocked = false;
  var overlay = null;

  function otherCount() {
    var now = Date.now();
    var n = 0;
    for (var id in others) {
      if (Object.prototype.hasOwnProperty.call(others, id)) {
        if (now - others[id] > 6000) { delete others[id]; }  // 6초 무응답 = 사라진 것으로 간주.
        else { n++; }
      }
    }
    return n;
  }

  function buildOverlay() {
    var el = document.createElement("div");
    el.className = "blend-window-guard";
    el.innerHTML =
      '<div class="blend-window-guard-card">' +
      '  <div class="blend-window-guard-icon" id="blend-window-guard-secret" aria-hidden="true">⚠</div>' +
      '  <h2 class="blend-window-guard-title">다른 창이 떠 있습니다</h2>' +
      '  <p class="blend-window-guard-text">이미 다른 창에서 배합을 진행 중입니다.<br>' +
      '     저울 값이 엉뚱한 창에 들어가거나 임시저장이 섞일 수 있으니,<br>' +
      '     <strong>이 창을 닫고 한 창에서만</strong> 작업해 주세요.</p>' +
      '  <p class="blend-window-guard-hint">원래 창을 닫으면 이 안내는 자동으로 사라집니다.</p>' +
      '  <div class="blend-window-guard-override" id="blend-window-guard-override" hidden>' +
      '    <label class="blend-window-guard-override-label" for="blend-window-guard-code">관리자 확인 코드</label>' +
      '    <div class="blend-window-guard-override-row">' +
      '      <input class="input" id="blend-window-guard-code" type="password" autocomplete="off" inputmode="text" />' +
      '      <button type="button" class="btn" id="blend-window-guard-code-ok">확인</button>' +
      '    </div>' +
      '    <p class="blend-window-guard-override-hint">정말 오류로 막힌 경우에만 사용하세요. 코드는 관리자에게 문의하세요.</p>' +
      '  </div>' +
      '</div>';

    function doOverride() {
      // 이 창을 쓰겠다고 선택 → 상대 창들이 대신 막히도록 인계 신호를 보낸다(슬롯 이동).
      bc.postMessage({ type: "takeover", from: myId });
      others = {};
      hideBlock();
    }

    // 숨은 비상구: 경고 아이콘을 OVERRIDE_TAPS 번(1.5초 내 연속) 눌러야 코드 입력칸이 나타난다.
    var taps = 0, tapTimer = null;
    var secret = el.querySelector("#blend-window-guard-secret");
    var overrideBox = el.querySelector("#blend-window-guard-override");
    var codeEl = el.querySelector("#blend-window-guard-code");
    secret.addEventListener("click", function () {
      taps += 1;
      if (tapTimer) clearTimeout(tapTimer);
      tapTimer = setTimeout(function () { taps = 0; }, 1500);
      if (taps >= OVERRIDE_TAPS) {
        taps = 0;
        overrideBox.hidden = false;
        if (codeEl) codeEl.focus();
      }
    });

    var okBtn = el.querySelector("#blend-window-guard-code-ok");
    function tryCode() {
      if (!codeEl) return;
      var val = codeEl.value.trim();
      if (!val) { codeEl.focus(); return; }
      if (okBtn) okBtn.disabled = true;
      // 서버에 대조 요청(코드는 내려받지 않는다). 일치 시에만 진행.
      fetch("/api/settings/blend-window-override/verify", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "x-csrftoken": csrfToken() },
        body: JSON.stringify({ code: val }),
      }).then(function (r) { return r.ok ? r.json() : { ok: false }; })
        .then(function (d) {
          if (d && d.ok) { doOverride(); }
          else { codeEl.value = ""; codeEl.focus(); }  // 조용히 실패(안내 없음).
        })
        .catch(function () { codeEl.value = ""; codeEl.focus(); })
        .then(function () { if (okBtn) okBtn.disabled = false; });
    }
    if (okBtn) okBtn.addEventListener("click", tryCode);
    if (codeEl) codeEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.isComposing) { e.preventDefault(); tryCode(); }
    });
    return el;
  }

  function showBlock() {
    if (blocked) return;
    blocked = true;
    if (!overlay) overlay = buildOverlay();
    if (!overlay.isConnected) document.body.appendChild(overlay);
    // 재노출 시 비상구는 다시 숨기고 코드칸을 비운다(이전에 펼쳐뒀던 상태가 남지 않게).
    var ov = overlay.querySelector("#blend-window-guard-override");
    if (ov) ov.hidden = true;
    var codeEl = overlay.querySelector("#blend-window-guard-code");
    if (codeEl) codeEl.value = "";
    overlay.hidden = false;
  }

  function hideBlock() {
    blocked = false;
    if (overlay) overlay.hidden = true;
  }

  bc.onmessage = function (e) {
    var m = e.data || {};
    if (!m || m.from === myId) return;
    switch (m.type) {
      case "ping":
        // 누군가 존재를 물음 → 내가 여기 있다고 응답.
        bc.postMessage({ type: "here", from: myId });
        break;
      case "here":
        // 내가 새로 열었는데 기존 창이 응답 → 이 창을 막는다.
        others[m.from] = Date.now();
        showBlock();
        break;
      case "hello":
        // 다른 창이 새로 열림(나는 기존 창) — 존재만 기록(나는 안 막힘).
        others[m.from] = Date.now();
        break;
      case "takeover":
        // 상대가 '이 창에서 계속'을 선택 → 내가 물러나 막힌다.
        others[m.from] = Date.now();
        showBlock();
        break;
      case "bye":
        delete others[m.from];
        if (otherCount() === 0) hideBlock();
        break;
      default:
        break;
    }
  };

  // 열릴 때: 등장 통지(hello) + 존재 문의(ping).
  bc.postMessage({ type: "hello", from: myId });
  bc.postMessage({ type: "ping", from: myId });

  // 주기적 재확인 — 막힌 상태에서 상대가 조용히 사라지면(크래시 등) 자동 해제.
  setInterval(function () {
    if (blocked) {
      bc.postMessage({ type: "ping", from: myId });
      // 응답 정리 후 남은 상대가 없으면 해제.
      setTimeout(function () { if (otherCount() === 0) hideBlock(); }, 800);
    }
  }, 3000);

  // 닫힐 때: 다른 창들이 즉시 해제할 수 있게 통지.
  function announceBye() { try { bc.postMessage({ type: "bye", from: myId }); } catch (_e) { /* 무시 */ } }
  window.addEventListener("pagehide", announceBye);
  window.addEventListener("beforeunload", announceBye);
})();
