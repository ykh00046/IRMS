/**
 * /weighing page controller — DOM 수집, ctx 생성, 6개 work 모듈 조립, 이벤트 바인딩.
 * Split from 760 LOC single-file into static/js/work/*.js + this controller (split-work-js, 2026-05).
 *
 * Module composition (factory + shared ctx pattern, Phase 3 계승):
 *   - IRMS.work.createStockBanner(ctx)        → { refresh, start }
 *   - IRMS.work.createRecipeTable(ctx)        → { render, bindRowActions, countRecipeMaterials }
 *   - IRMS.work.createWeighingRender(ctx)     → { render, syncControls, resetProgress, getQueueColorCounts }
 *   - IRMS.work.createWeighingActions(ctx)    → { open, close, loadQueue, advance, undo, isOpen }
 *   - IRMS.work.createImportNotifications(ctx)→ { check, start }
 *   - IRMS.work.createIdleLogout(ctx)         → { start, stop }
 *
 * 공유 가변 상태는 단일 ctx.state 객체로 모든 모듈이 동일 참조 공유.
 * 모듈 간 직접 참조는 0 — 교차 호출은 ctx.weighingRender / ctx.onRefreshTable / ctx.onRefreshWeighingQueue 경유.
 */
document.addEventListener("DOMContentLoaded", () => {
  // ── 1단계: DOM 참조 수집 ──
  const dom = {
    shell: document.querySelector(".site-shell"),
    tableHead: document.getElementById("work-head"),
    tableBody: document.getElementById("work-body"),
    statsCount: document.getElementById("work-count"),
    statsStatus: document.getElementById("work-status"),
    roomMeta: document.getElementById("work-chat-room-meta"),
    roomTabs: document.getElementById("work-chat-room-tabs"),
    chatMessages: document.getElementById("work-chat-messages"),
    chatForm: document.getElementById("work-chat-form"),
    chatInput: document.getElementById("work-chat-input"),
    chatSend: document.getElementById("work-chat-send"),
    chatStage: document.getElementById("work-chat-stage"),  // 미존재(work.html에 없음) — null로 전달 → chat.js의 stage?.value 안전 처리

    weighingRefreshMainBtn: document.getElementById("weighing-refresh-main-btn"),
    weighingMode: document.getElementById("weighing-mode"),
    weighingCloseBtn: document.getElementById("weighing-close-btn"),
    weighingRefreshBtn: document.getElementById("weighing-refresh-btn"),
    weighingAdvanceBtn: document.getElementById("weighing-advance-btn"),
    weighingUndoBtn: document.getElementById("weighing-undo-btn"),
    weighingProgressFill: document.getElementById("weighing-progress-fill"),
    weighingProgressText: document.getElementById("weighing-progress-text"),
    weighingSummary: document.getElementById("weighing-summary"),
    weighingStateBadge: document.getElementById("weighing-state-badge"),
    weighingProductName: document.getElementById("weighing-product-name"),
    weighingInkLabel: document.getElementById("weighing-ink-label"),
    weighingPositionLabel: document.getElementById("weighing-position-label"),
    weighingMaterialName: document.getElementById("weighing-material-name"),
    weighingTargetValue: document.getElementById("weighing-target-value"),
    weighingActionHint: document.getElementById("weighing-action-hint"),
    weighingNextValue: document.getElementById("weighing-next-value"),
    weighingPowderBtn: document.getElementById("weighing-powder-btn"),
    weighingLiquidBtn: document.getElementById("weighing-liquid-btn"),
    liquidColorPicker: document.getElementById("liquid-color-picker"),
    weighingModeLabel: document.getElementById("weighing-mode-label"),
    workStockBanner: document.getElementById("work-stock-banner"),
    weighingCurrentCard: document.querySelector(".weighing-current"),
  };

  // ── 2단계: 공유 ctx 객체 생성 ──
  const ctx = {
    dom,
    state: {
      // 재고
      lowStockSet: new Set(),
      // 테이블 race-guard
      loadingToken: 0,
      // 채팅 (chatModule이 proxy를 통해 read/write)
      currentUsername: dom.shell?.dataset.currentUsername || "",
      selectedRoomKey: window.localStorage.getItem("irms_chat_room") || "notice",
      rooms: [],
      chatLatestIdByRoom: {},
      chatSending: false,
      chatTimerId: null,
      // import 알림 폴링
      recipeImportNotice: {
        initialized: false,
        checking: false,
        lastSeenId: Number(window.localStorage.getItem("irms_last_recipe_import_id") || 0),
        timerId: null,
      },
      // 계량 모드
      weighing: {
        open: false,
        loading: false,
        advancing: false,
        undoing: false,
        queue: [],
        doneCount: 0,
        initialTotal: 0,
        colorGroup: "all",
        pendingRecipeCompletion: null,
        lastCompleted: null,
        lastSpokenStepKey: null,
      },
      stageLabels: {
        registered: "Registered",
        in_progress: "In Progress",
        completed: "Completed",
      },
    },
    colorLabel: IRMS.colorLabel,
  };

  // ── 3단계: 모듈 인스턴스화 + 2단계 와이어링 ──
  ctx.stockBanner = IRMS.work.createStockBanner(ctx);
  ctx.recipeTable = IRMS.work.createRecipeTable(ctx);
  ctx.onRefreshTable = ctx.recipeTable.render;
  ctx.weighingRender = IRMS.work.createWeighingRender(ctx);
  ctx.weighingActions = IRMS.work.createWeighingActions(ctx);
  ctx.onRefreshWeighingQueue = async () => {
    if (ctx.weighingActions.isOpen()) {
      await ctx.weighingActions.loadQueue();
    }
  };
  const importNotifications = IRMS.work.createImportNotifications(ctx);
  const idleLogout = IRMS.work.createIdleLogout(ctx);

  // ── 4단계: 채팅 모듈 (state proxy로 ctx.state와 양방향 동기화) ──
  const chatModule = IRMS.createChat({
    prefix: "chat",
    stageLabels: ctx.state.stageLabels,
    elements: { roomTabs: dom.roomTabs, chatMessages: dom.chatMessages, roomMeta: dom.roomMeta },
    state: {
      get rooms() { return ctx.state.rooms; },
      set rooms(v) { ctx.state.rooms = v; },
      get selectedRoomKey() { return ctx.state.selectedRoomKey; },
      set selectedRoomKey(v) { ctx.state.selectedRoomKey = v; },
      get latestByRoom() { return ctx.state.chatLatestIdByRoom; },
      set latestByRoom(v) { ctx.state.chatLatestIdByRoom = v; },
      get timerId() { return ctx.state.chatTimerId; },
      set timerId(v) { ctx.state.chatTimerId = v; },
      get currentUsername() { return ctx.state.currentUsername; },
    },
  });

  function refreshChatPanel(options) { return chatModule.refresh(options); }
  function startChatPolling() { chatModule.startPolling(3000); }

  // ── 5단계: 정적 이벤트 바인딩 ──
  chatModule.bindRoomTabs(dom.roomTabs);
  chatModule.bindForm({ form: dom.chatForm, input: dom.chatInput, stage: dom.chatStage, send: dom.chatSend });
  ctx.recipeTable.bindRowActions();

  // Powder mode: start immediately with "all"
  if (dom.weighingPowderBtn) {
    dom.weighingPowderBtn.addEventListener("click", () => {
      ctx.weighingActions.open("all", "파우더 모드");
    });
  }
  // Liquid mode: show color picker
  if (dom.weighingLiquidBtn) {
    dom.weighingLiquidBtn.addEventListener("click", () => {
      if (dom.liquidColorPicker) dom.liquidColorPicker.hidden = !dom.liquidColorPicker.hidden;
    });
  }
  // Liquid color selection
  document.querySelectorAll(".liquid-color-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const color = btn.dataset.liquidColor;
      ctx.weighingActions.open(color, "액상 모드 — " + (btn.textContent || color));
    });
  });
  if (dom.weighingRefreshMainBtn) {
    dom.weighingRefreshMainBtn.addEventListener("click", () => {
      ctx.weighingActions.loadQueue({ notifySummary: true });
    });
  }
  if (dom.weighingCloseBtn) {
    dom.weighingCloseBtn.addEventListener("click", ctx.weighingActions.close);
  }
  if (dom.weighingRefreshBtn) {
    dom.weighingRefreshBtn.addEventListener("click", () => {
      ctx.weighingActions.loadQueue({ notifySummary: true });
    });
  }
  if (dom.weighingAdvanceBtn) {
    dom.weighingAdvanceBtn.addEventListener("click", ctx.weighingActions.advance);
  }
  if (dom.weighingUndoBtn) {
    dom.weighingUndoBtn.addEventListener("click", ctx.weighingActions.undo);
  }
  if (dom.weighingMode) {
    dom.weighingMode.addEventListener("click", (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      if (target.hasAttribute("data-close-weighing")) {
        ctx.weighingActions.close();
      }
    });
  }

  // 키보드 단축키 (Esc / Enter / Space)
  document.addEventListener("keydown", (event) => {
    if (!ctx.weighingActions.isOpen()) {
      return;
    }

    if (event.key === "Escape") {
      event.preventDefault();
      ctx.weighingActions.close();
      return;
    }

    const target = event.target;
    const isTypingTarget =
      target instanceof HTMLElement &&
      ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
    if (isTypingTarget) {
      return;
    }

    if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
      event.preventDefault();
      ctx.weighingActions.advance();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      importNotifications.check({ silent: true });
      refreshChatPanel({ replace: false, silent: true });
    }
  });

  // ── 6단계: 부팅 ──
  ctx.stockBanner.refresh();
  ctx.stockBanner.start();
  ctx.recipeTable.render();
  refreshChatPanel({ replace: true, silent: true });
  importNotifications.check({ silent: true });
  importNotifications.start();
  startChatPolling();
  idleLogout.start();
});
