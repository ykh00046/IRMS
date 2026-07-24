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
      '  <div class="blend-window-guard-icon" aria-hidden="true">⚠</div>' +
      '  <h2 class="blend-window-guard-title">다른 창이 떠 있습니다</h2>' +
      '  <p class="blend-window-guard-text">이미 다른 창에서 배합을 진행 중입니다.<br>' +
      '     저울 값이 엉뚱한 창에 들어가거나 임시저장이 섞일 수 있으니,<br>' +
      '     <strong>이 창을 닫고 한 창에서만</strong> 작업해 주세요.</p>' +
      '  <p class="blend-window-guard-hint">원래 창을 닫으면 이 안내는 자동으로 사라집니다.</p>' +
      '  <button type="button" class="btn" id="blend-window-guard-continue">그래도 이 창에서 계속</button>' +
      '</div>';
    el.querySelector("#blend-window-guard-continue").addEventListener("click", function () {
      // 이 창을 쓰겠다고 선택 → 상대 창들이 대신 막히도록 인계 신호를 보낸다(슬롯 이동).
      bc.postMessage({ type: "takeover", from: myId });
      others = {};
      hideBlock();
    });
    return el;
  }

  function showBlock() {
    if (blocked) return;
    blocked = true;
    if (!overlay) overlay = buildOverlay();
    if (!overlay.isConnected) document.body.appendChild(overlay);
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
