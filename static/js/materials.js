/**
 * materials — 자재 관리 화면(/materials) 컨트롤러.
 *
 * 품목코드 탭과 자재 LOT 탭을 묶는 얇은 껍데기다. 실제 동작은 두 모듈이 갖고 있다:
 *   - 품목코드: management/item-codes.js 의 IRMS.management.createItemCodesPanel
 *   - 자재 LOT: material_lots.js (자체 DOMContentLoaded 로 스스로 붙는다 — 여기서
 *     건드리지 않는다. 탭이 숨겨져 있어도 한 번 읽어 두므로 전환이 즉시 끝난다.)
 *
 * 탭 전환은 management.js 의 규약(.mgmt-tab[data-tab] ↔ #tab-{name}.active)을 그대로
 * 따른다 — 두 화면의 탭이 같은 CSS(management.css)를 쓰기 때문에 동작도 같아야 한다.
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});

  document.addEventListener("DOMContentLoaded", () => {
    const dom = {
      shell: document.querySelector(".app-shell"),
      topbarEyebrow: document.querySelector(".topbar-eyebrow"),
      topbarHeading: document.querySelector(".topbar-heading"),
      tabBtns: document.querySelectorAll(".mgmt-tab"),
      tabPanels: document.querySelectorAll(".tab-panel"),
      codesSearch: document.getElementById("codes-search"),
      codesUncoded: document.getElementById("codes-uncoded"),
      codesRefreshBtn: document.getElementById("codes-refresh-btn"),
      codesBody: document.getElementById("codes-body"),
    };
    const canManage = dom.shell && dom.shell.dataset.canManage === "1";
    const ctx = { dom, canManage, state: {} };

    // 탭별 상단 제목 — 화면 하나에 성격이 다른 두 표가 있어, 어느 쪽을 보고 있는지
    // 제목으로도 말해 준다(management.js 의 syncTopbarTitle 과 같은 취지).
    const tabTitles = {
      codes: { eyebrow: "운영 관리", heading: "품목코드" },
      lots: { eyebrow: "운영 관리", heading: "자재 LOT" },
    };

    function syncTopbarTitle(tabName) {
      const title = tabTitles[tabName] || tabTitles.codes;
      if (dom.topbarEyebrow) dom.topbarEyebrow.textContent = title.eyebrow;
      if (dom.topbarHeading) dom.topbarHeading.textContent = title.heading;
      document.title = `BRM · ${title.heading}`;
    }

    // 첫 탭은 템플릿이 정한다(책임자=품목코드, 담당자=자재 LOT). 여기서 codes 를
    // 가정하지 않고 지금 active 인 탭에 상단 제목을 맞춘다.
    const initialTab = document.querySelector(".mgmt-tab.active");
    if (initialTab && initialTab.dataset.tab) syncTopbarTitle(initialTab.dataset.tab);

    dom.tabBtns.forEach((btn) => {
      btn.addEventListener("click", () => {
        dom.tabBtns.forEach((b) => b.classList.remove("active"));
        dom.tabPanels.forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        const panel = document.getElementById(`tab-${btn.dataset.tab}`);
        if (panel) panel.classList.add("active");
        syncTopbarTitle(btn.dataset.tab);
        // 품목코드 탭으로 돌아올 때마다 최신화 — 다른 탭/창에서 코드가 바뀌었을 수 있다.
        if (btn.dataset.tab === "codes" && ctx.itemCodes) ctx.itemCodes.refresh();
      });
    });

    // 품목코드 패널은 책임자에게만 마크업이 렌더된다(codes-body 부재 = 담당자).
    if (canManage && dom.codesBody && IRMS.management && IRMS.management.createItemCodesPanel) {
      const itemCodes = IRMS.management.createItemCodesPanel(ctx);
      ctx.itemCodes = itemCodes;
      itemCodes.init();
      itemCodes.refresh();
    }

    // 배치 폐기 기록 — 마크업이 있을 때(책임자)만 부른다. 자재가 실물로 나갔는데
    // 제품이 되지 못한 양이라, 사용량 집계에서 빠진 자리를 여기서 메운다.
    const discardBody = document.getElementById("mlot-discard-body");
    if (canManage && discardBody) {
      const SOURCE_LABEL = {
        overweight: "과중량",
        rescale_limit: "3회 증량 차단",
        manual: "직접 기록",
      };
      const esc = (value) =>
        String(value == null ? "" : value).replace(/[&<>"']/g, (c) => ({
          "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
        }[c]));
      const gram = (value) => {
        const n = Number(value);
        return Number.isFinite(n) ? n.toLocaleString("ko-KR", { maximumFractionDigits: 2 }) : "-";
      };
      const request = IRMS._core && IRMS._core.request;
      const empty = document.getElementById("mlot-discard-empty");
      if (request) {
        request("/blend/lot-audit/batch-discards")
          .then((data) => {
            const items = (data && data.items) || [];
            if (!items.length) {
              if (empty) empty.hidden = false;
              return;
            }
            discardBody.innerHTML = items
              .map((it) => {
                const mats = (it.details || [])
                  .map((d) => `${esc(d.material_name)} ${gram(d.actual_amount)} g`)
                  .join(", ");
                return (
                  "<tr>" +
                  `<td>${esc(it.work_date)}<br /><span class="muted small">${esc(it.worker)}</span></td>` +
                  `<td>${esc(it.product_name)}</td>` +
                  `<td class="muted small">${esc(it.reason)}</td>` +
                  `<td>${esc(SOURCE_LABEL[it.source] || it.source)}</td>` +
                  `<td class="muted small">${mats || "-"}</td>` +
                  `<td class="num">${gram(it.discarded_g)}</td>` +
                  "</tr>"
                );
              })
              .join("");
          })
          .catch(() => {
            if (empty) {
              empty.hidden = false;
              empty.textContent = "배치 폐기 기록을 불러오지 못했습니다.";
            }
          });
      }
    }

    // 탭 딥링크 — /materials?tab=lots(대시보드 '자재 LOT 기준 파일' 카드 등)가 지정한
    // 탭을 연다. 위 탭 버튼의 click 핸들러를 그대로 태운다(다른 동작은 건드리지 않는다).
    const requestedTab = new URLSearchParams(window.location.search).get("tab");
    if (requestedTab) {
      const btn = Array.from(dom.tabBtns).find((b) => b.dataset.tab === requestedTab);
      if (btn) btn.click();
    }
  });
})();
