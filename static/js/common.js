/**
 * common.js — DOMContentLoaded bootstrap for IRMS shared UI.
 *
 * After the split-common-js PDCA cycle (2026-05), this file holds only
 * the on-page DOM wiring that runs once per page load:
 *   - mobile nav-toggle hamburger
 *   - floating chat sidebar (toggle + overlay + close)
 *   - Enter-to-send key handler for .chat-textarea
 *   - initTableScrollHints invocation on DOMContentLoaded
 *
 * Public IRMS.* surface and HTTP clients live in static/js/common/*.js;
 * load those 12 modules before this file. See:
 *   - docs/archive/2026-05/split-common-js/ (PDCA documents)
 *   - docs/02-design/features/split-common-js.design.md §5.2 (load order)
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

  const navToggle = document.getElementById("nav-toggle");
  const topNav = document.querySelector(".top-nav");
  if (navToggle && topNav) {
    navToggle.addEventListener("click", () => {
      const isOpen = topNav.classList.toggle("open");
      navToggle.classList.toggle("active", isOpen);
      navToggle.setAttribute("aria-expanded", String(isOpen));
    });
  }

  // Floating chat sidebar
  const chatFloat = document.querySelector(".chat-float");
  if (chatFloat) {
    const toggle = document.createElement("button");
    toggle.className = "chat-float-toggle";
    toggle.type = "button";
    toggle.setAttribute("aria-label", "메시지");
    toggle.innerHTML = '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
    document.body.appendChild(toggle);

    const closeBtn = document.createElement("button");
    closeBtn.className = "chat-float-close";
    closeBtn.type = "button";
    closeBtn.setAttribute("aria-label", "닫기");
    closeBtn.innerHTML = "&times;";
    const chatHead = chatFloat.querySelector(".chat-head");
    if (chatHead) {
      chatHead.appendChild(closeBtn);
    } else {
      chatFloat.prepend(closeBtn);
    }

    function setChatOpen(open) {
      chatFloat.classList.toggle("open", open);
      toggle.classList.toggle("active", open);
    }

    toggle.addEventListener("click", () => setChatOpen(!chatFloat.classList.contains("open")));
    closeBtn.addEventListener("click", () => setChatOpen(false));

    // Close on overlay (backdrop) click
    const overlay = document.createElement("div");
    overlay.className = "chat-float-overlay";
    document.body.appendChild(overlay);
    overlay.addEventListener("click", () => setChatOpen(false));

    const origSetChatOpen = setChatOpen;
    setChatOpen = function (open) {
      origSetChatOpen(open);
      overlay.classList.toggle("active", open);
    };
  }

  // Enter to send in chat textareas (Shift+Enter for newline)
  document.addEventListener("keydown", (e) => {
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !e.isComposing &&
      e.target.classList.contains("chat-textarea")
    ) {
      e.preventDefault();
      const form = e.target.closest("form");
      if (form) form.requestSubmit();
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", IRMS.initTableScrollHints);
  } else {
    IRMS.initTableScrollHints();
  }
})();
