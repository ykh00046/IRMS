/**
 * status.js — 배합 기록 · DHR 관리 (/status).
 * 배합 기록 목록(필터·정렬·페이지) + 행 클릭 시 배합 실적서(DHR) 상세 + 인쇄/Excel.
 * 결재·점도 등록 등 쓰기 작업은 배합 화면에서 수행한다(여기는 조회·출력 중심).
 *
 * 2026-08-14 재구축 — 진입 즉시 500행을 전부 그려 18,659px 짜리 한 페이지가 되던 화면을
 * 50행/쪽으로 나눴다. 서버 조회는 종전대로 1회(최신 500건)이고 쪽 나누기는 이 화면에서만
 * 한다. 선택은 화면에 그려진 체크박스가 아니라 id 집합(Set)이 소유하므로 쪽을 넘겨도,
 * 정렬을 바꿔도, 조건을 다시 조회해도 살아남는다.
 */
document.addEventListener("DOMContentLoaded", () => {
  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const $ = (id) => document.getElementById(id);
  const notify = (msg, kind) => { if (IRMS.notify) IRMS.notify(msg, kind); };
  // 기본 소수 2자리 — 저울(XP 0.01g) 해상도에 맞춤
  const fmt = (v, d = 2) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));

  // ── 화면 상태 ────────────────────────────────────────────────────
  const PAGE_SIZE = 50;          // 쪽 크기 고정 — 현장에서 고를 이유가 없다.
  const MAX_PRINT = 200;         // 일괄 출력 상한(서버 변환이 직렬이라 그 이상은 무의미).
  const MAX_BULK_CANCEL = 50;    // 한 번에 취소할 수 있는 상한 — 되돌릴 수 있어도 대량은 사고다.
  const COLS = 7;                // 표 열 수(빈 상태 colspan)

  let allRecords = [];           // 이번 조회로 받아 둔 목록(정렬 전 원본)
  let listMeta = {};             // 서버 응답 메타(total_available·truncated·canceled_hidden)
  const selected = new Set();    // 선택된 기록 id(Number) — 쪽·정렬·재조회를 넘어 유지된다.
  let page = 1;
  let recSort = { key: null, dir: "desc" };
  let detailId = null;
  let currentRecord = null;

  // ── 모달 접근성 헬퍼 ─────────────────────────────────────────────
  // role/aria-modal 은 템플릿에 두고, 여기서 열 때 포커스 이동·닫을 때 복원 +
  // Esc + 배경 클릭 닫기를 건다. 표준 닫기 경로 = 닫기 버튼 · 배경 클릭 · Esc.
  function createModal(overlayId, opts) {
    opts = opts || {};
    const overlay = $(overlayId);
    let opener = null;
    function open(focusEl) {
      // 재렌더(같은 모달을 다시 open)일 때는 원래 오프너를 보존해 복원 대상이 흔들리지 않게.
      if (overlay.hidden) opener = document.activeElement;
      overlay.hidden = false;
      // body 클래스 — 뒤 페이지 스크롤 잠금 + 인쇄(@media print) 범위 판정에 쓴다.
      if (opts.bodyClass) document.body.classList.add(opts.bodyClass);
      const target = focusEl
        || (opts.initialFocus && $(opts.initialFocus))
        || overlay.querySelector(".ss-modal");
      if (target && target.focus) setTimeout(() => { try { target.focus(); } catch (_e) { /* noop */ } }, 0);
    }
    function close() {
      if (overlay.hidden) return;
      // beforeClose 가 false 를 돌려주면 닫지 않는다 — 편집 폼 위에서 닫기·배경 클릭·
      // Esc 가 저장 안 한 변경을 무경고로 버리던 구멍의 마개(2026-08-05 감사 R-13).
      if (opts.beforeClose && !opts.beforeClose()) return;
      overlay.hidden = true;
      if (opts.bodyClass) document.body.classList.remove(opts.bodyClass);
      if (opts.onClose) opts.onClose();
      if (opener && opener.focus) { try { opener.focus(); } catch (_e) { /* noop */ } }
      opener = null;
    }
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(); });
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
    });
    return { open, close };
  }
  // 편집 폼(#e-rows)이 열려 있으면 닫기 전에 한 번 묻는다 — 25줄 수정 중 배경 오클릭
  // 한 번에 전부 날아가던 경로(R-13). '취소' 버튼은 상세 보기로 돌아가는 명시 동작이라 예외.
  const detailModal = createModal("status-detail-modal", {
    initialFocus: "status-detail-close",
    bodyClass: "dhr-open",
    beforeClose: () => !document.getElementById("e-rows")
      || window.confirm("수정 중입니다 — 저장하지 않은 변경이 사라집니다. 닫을까요?"),
  });

  // 목록 배지 — 증량이 있으면 표시, 미승인(책임자 부재 진행)은 빨간 배지.
  // 플래그는 목록 행이 직접 싣고 온다(2026-08-14). 종전에는 /blend/rescales/summary 를
  // 따로 받아 짝맞췄는데, 그 응답이 무조건 최신 1000건이라 그 밖의 기록은 배지가
  // 조용히 사라졌고 조회마다 이벤트 배열 전체가 오갔다.
  function rescaleBadge(r) {
    let out = "";
    if (r.rescale_count) {
      out += r.rescale_unacked
        ? ' <span class="rescale-badge unacked" title="책임자 미승인 증량">미승인 증량</span>'
        : ` <span class="rescale-badge" title="증량 승인됨">증량 ${r.rescale_count}회</span>`;
    }
    // 수기 입력을 책임자 부재로 진행한 기록 — 확인 전까지 배지 유지(증량과 동일 정책).
    if (r.manual_unacked) {
      out += ' <span class="rescale-badge unacked" title="책임자 미확인 수기 입력">미승인 수기 입력</span>';
    }
    return out;
  }

  // 일괄 재생성 표식 — 현장 계량이 아니라 문서·계획용으로 한 번에 재생성한 기록.
  function bulkBadge(r) {
    if (!r || !r.is_bulk_regenerated) return "";
    return ' <span class="bulk-regen-badge" title="일괄 재생성으로 만든 문서·계획용 기록">일괄 재생성</span>';
  }

  // 상세 모달 — 증량 이력(전 총량→후 총량, 승인자 또는 부재 사유).
  // 이벤트 목록은 상세 API 가 내려주는 rec.rescale_events_json 에서 그린다(마스킹 없는 정본).
  function rescaleBlock(rec) {
    if (!rec) return "";
    const id = rec.id;
    let events = [];
    if (rec.rescale_events_json) {
      try {
        const parsed = JSON.parse(rec.rescale_events_json);
        if (Array.isArray(parsed)) events = parsed;
      } catch (_e) { events = []; }  // 손상된 JSON — 빈 배열로 폴백
    }
    if (!events.length) return "";
    const rows = events
      .map((e, i) => {
        const who = e.approver
          ? `승인자: ${esc(e.approver)}`
          : e.absence_reason
            ? `책임자 부재: ${esc(e.absence_reason)}`
            : "책임자 부재";
        // 증량을 몰아온 자재 요약(저장 시점에 첨부된 drivers). 과거 기록엔 없으니 빠져도 OK.
        const drivers = Array.isArray(e.drivers) ? e.drivers : [];
        const driverLines = drivers.length
          ? `<ul class="blend-rescale-drivers muted small">${drivers.map((d) => {
              const name = d.material_name == null ? "-" : esc(d.material_name);
              return `<li>${name}: 이론 ${fmt(d.theory_before)}g / 실제 ${fmt(d.actual)}g — ${fmt(d.over)}g 초과</li>`;
            }).join("")}</ul>`
          : "";
        return `<li>${i + 1}. ${fmt(e.before_total)} g → ${fmt(e.after_total)} g <span class="muted small">(${who})</span>${driverLines}</li>`;
      })
      .join("");
    const unackedTag = rec.rescale_unacked
      ? ' <span class="rescale-badge unacked">미승인</span>'
      : "";
    // 확인 처리는 내용을 보는 이 자리에서 — 목록/대시보드에서 안 보고 누르게 하지 않는다.
    const ackBtn = rec.rescale_unacked && canManage()
      ? ` <button class="btn btn-sm accent" id="detail-rescale-ack" data-id="${id}" type="button">확인 처리</button>`
      : "";
    return `<div class="blend-rescale-block"><b>증량 이력${unackedTag}</b>${ackBtn}<ul class="blend-rescale-list">${rows}</ul></div>`;
  }

  // 책임자 여부 — 템플릿이 책임자에게만 렌더하는 요소로 판정(기존 패턴).
  function canManage() {
    return Boolean($("status-rec-delete-selected"));
  }

  // 수기 입력(책임자 부재) 블록 — 사유를 보여주고, 미확인이면 여기서 확인 처리.
  function manualBlock(rec) {
    const reason = rec.manual_absence_reason;
    if (!reason && !rec.manual_unacked) return "";
    const unackedTag = rec.manual_unacked
      ? ' <span class="rescale-badge unacked">미확인</span>'
      : "";
    const ackBtn = rec.manual_unacked && canManage()
      ? ` <button class="btn btn-sm accent" id="detail-manual-ack" data-id="${rec.id}" type="button">확인 처리</button>`
      : "";
    return `<div class="blend-rescale-block"><b>수기 입력(책임자 부재)${unackedTag}</b>${ackBtn}`
      + `<p class="muted small blend-manual-reason">사유: ${esc(reason || "-")}</p></div>`;
  }

  // 계량 중 자재 폐기 블록 — '처음부터 다시' 재계량에서 실제로 버린 자재의 흔적.
  // 편차 강제라 최종 수치엔 안 보이는 소모를 여기서만 볼 수 있다(2026-08-05).
  function discardBlock(rec) {
    let events = [];
    try {
      const parsed = JSON.parse(rec.discard_events_json || "[]");
      if (Array.isArray(parsed)) events = parsed;
    } catch (_e) { /* 손상된 JSON — 표시 생략(저장 데이터는 서버가 정규화) */ }
    if (!events.length) return "";
    const rows = events
      .map((e) => `<li>${esc(e.material_name || "-")}: ${fmt(e.amount_g)} g 폐기</li>`)
      .join("");
    return `<div class="blend-rescale-block"><b>계량 중 자재 폐기</b>`
      + `<ul class="blend-rescale-list">${rows}</ul></div>`;
  }

  // 기본 삭제 = '취소'(soft) — 기록은 남고 목록·출력·집계에서만 빠지며 복원할 수 있다.
  // 물리 삭제는 되돌릴 수 없어 별도 경로(cancelRecord 이후 '완전 삭제')로만 도달한다.
  async function cancelRecord(recordId, reason) {
    await request(`/blend/records/${recordId}`, {
      method: "DELETE",
      query: { reason },
    });
  }

  async function hardDeleteRecord(recordId, reason) {
    await request(`/blend/records/${recordId}`, {
      method: "DELETE",
      query: { hard: 1, reason },
    });
  }

  async function restoreRecord(recordId) {
    await request(`/blend/records/${recordId}/restore`, { method: "POST" });
  }

  async function loadWorkers() {
    try {
      const data = await request("/blend/workers");
      const sel = $("status-rec-worker");
      (data.items || []).forEach((w) => {
        const o = document.createElement("option");
        o.value = w;
        o.textContent = w;
        sel.appendChild(o);
      });
    } catch (_e) {
      /* 작업자 목록 실패는 조회에 영향 없음 */
    }
  }

  // 제품 필터 모집단 — 목록과 같은 테이블(blend_records)에서 온다. 종전에는 완료·비재생성
  // 한정 통계(product-usage)에서 이름만 뽑아 써서, 취소분·일괄재생성 기록만 있는 제품이
  // 목록에는 보이는데 필터에는 없었다(2026-08-14 검토 12번).
  async function loadProducts() {
    try {
      const data = await request("/blend/records/product-names");
      const sel = $("status-rec-product");
      (data.items || []).filter((n) => n).forEach((name) => {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = name;
        sel.appendChild(o);
      });
    } catch (_e) {
      /* 제품 목록 실패는 조회에 영향 없음 (작업자와 동일 fail-soft) */
    }
  }

  const isUnackedOnly = () => Boolean($("status-rec-unacked") && $("status-rec-unacked").checked);

  async function loadRecords(opts) {
    const keepPage = Boolean(opts && opts.keepPage);
    const body = $("status-rec-body");
    // 조회 중 로딩 표시 — 초기 진입·재조회 모두 공용 .spinner 로.
    body.innerHTML = `<tr><td colspan="${COLS}"><div class="table-loading"><span class="spinner"></span> 불러오는 중…</div></td></tr>`;
    const query = {
      start_date: $("status-rec-from").value || undefined,
      end_date: $("status-rec-to").value || undefined,
      worker: $("status-rec-worker").value || undefined,
      product: $("status-rec-product").value || undefined,
      search: $("status-rec-search").value.trim() || undefined,
      include_canceled: ($("status-rec-canceled") && $("status-rec-canceled").checked) ? 1 : undefined,
      // '미확인만' 은 서버가 전체 테이블에서 거른다 — 클라이언트 필터는 500건 절단 뒤라
      // 상한 밖(오래된) 미확인 건이 영영 보이지 않았다(2026-08-14 검토 1번).
      unacked: isUnackedOnly() ? 1 : undefined,
    };
    try {
      const data = await request("/blend/records", { query });
      allRecords = data.items || [];
      listMeta = data;
      if (!keepPage) page = 1;
      renderTruncNote(data);
      renderRecordSummary(data, allRecords.length);
      renderTable();
    } catch (e) {
      allRecords = [];
      listMeta = {};
      body.innerHTML = `<tr><td colspan="${COLS}" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
      const note = $("status-rec-note");
      if (note) note.hidden = true;
      updatePager(0, 0, 0);
      updateSelectionUI();
    }
  }

  // 서버가 최신 limit(기본 500)건만 반환 — 상한 도달 시 전체 M 과 함께 좁히기 안내.
  function renderTruncNote(data) {
    const note = $("status-rec-note");
    if (!note) return;
    if (data.truncated) {
      note.textContent =
        `최근 ${fmt(data.limit || (data.items || []).length, 0)}건만 표시 (전체 ${fmt(data.total_available, 0)}건) — ` +
        "날짜·작업자·검색으로 범위를 좁히거나 ‘전체 Excel’로 내려받으세요.";
      note.hidden = false;
    } else {
      note.hidden = true;
    }
  }

  // ── 표 그리기(정렬 → 쪽 자르기 → 렌더) ──────────────────────────
  // 서버를 다시 부르지 않는다. 정렬·쪽 이동은 전부 이 함수 한 번으로 끝난다.
  function renderTable() {
    const body = $("status-rec-body");
    const items = sortRecords(allRecords);
    markSortHeaders();
    if (!items.length) {
      body.innerHTML = isUnackedOnly()
        ? `<tr><td colspan="${COLS}" class="muted">미확인 증량·수기 입력이 없습니다.</td></tr>`
        : `<tr><td colspan="${COLS}" class="muted">기록이 없습니다.</td></tr>`;
      updatePager(0, 0, 0);
      updateSelectionUI();
      return;
    }
    const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE));
    if (page > pages) page = pages;
    if (page < 1) page = 1;
    const start = (page - 1) * PAGE_SIZE;
    const slice = items.slice(start, start + PAGE_SIZE);
    body.innerHTML = "";
    slice.forEach((r) => body.appendChild(recordRow(r)));
    updatePager(items.length, start, slice.length);
    updateSelectionUI();
  }

  function recordRow(r) {
    const tr = document.createElement("tr");
    tr.className = "blend-row";
    // 키보드만으로도 상세에 닿아야 한다 — 행이 클릭 전용이면 마우스가 없는 경로가 막힌다.
    tr.tabIndex = 0;
    tr.dataset.id = String(r.id);
    // 수동 입력 ⚠ — 서버가 책임자에게만 플래그를 내려주므로(비책임자는 False 마스킹)
    // 목록에 표시해도 책임자 로그인 시에만 보인다.
    const manualTag = r.manual_entry ? ' <span class="manual-entry-dot" title="수동 입력">⚠</span>' : "";
    // 취소된 기록은 목록에서 한눈에 구분되어야 한다(취소 포함으로 조회했을 때).
    // 취소는 되돌릴 수 있는 양성 상태 — 붉은 '미승인' 배지가 아니라 중립 .status-canceled 칩.
    const canceledTag = r.status === "canceled"
      ? ' <span class="status-chip status-canceled" title="취소된 기록 — 상세에서 복원할 수 있습니다">취소됨</span>' : "";
    const checked = selected.has(Number(r.id)) ? " checked" : "";
    tr.innerHTML =
      `<td class="chk-col"><input type="checkbox" class="rec-chk" value="${r.id}"${checked} aria-label="${esc(r.product_lot)} 선택" /></td>` +
      `<td>${esc(r.work_date)}</td><td>${esc(r.product_lot)}${manualTag}${canceledTag}${rescaleBadge(r)}${bulkBadge(r)}</td>` +
      `<td>${esc(r.product_name)}</td>` +
      `<td>${esc(r.worker)}</td><td class="num">${fmt(r.total_amount)}</td><td>${esc(r.scale || "-")}</td>`;
    tr.addEventListener("click", () => openDetail(r.id));
    tr.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); openDetail(r.id); }
    });
    const chk = tr.querySelector(".rec-chk");
    chk.addEventListener("click", (e) => e.stopPropagation());
    chk.addEventListener("change", (e) => {
      if (e.target.checked) selected.add(Number(r.id));
      else selected.delete(Number(r.id));
      updateSelectionUI();
    });
    return tr;
  }

  function updatePager(total, start, count) {
    const pager = $("status-pager");
    if (!pager) return;
    if (!total) { pager.hidden = true; return; }
    pager.hidden = false;
    const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    const info = $("status-page-info");
    if (info) {
      info.textContent =
        `${fmt(start + 1, 0)}–${fmt(start + count, 0)} / ${fmt(total, 0)}건 · ${page}/${pages}쪽`;
    }
    const prev = $("status-page-prev");
    const next = $("status-page-next");
    if (prev) prev.disabled = page <= 1;
    if (next) next.disabled = page >= pages;
  }

  // 선택 상태 표시 — 화면 밖(다른 쪽)에 있는 선택까지 세어 말한다. 종전에는 선택이
  // 체크박스에만 있어 쪽을 넘기거나 정렬만 바꿔도 조용히 사라졌다.
  function updateSelectionUI() {
    const el = $("status-sel-count");
    if (el) el.textContent = selected.size ? `선택 ${fmt(selected.size, 0)}건` : "선택 없음";
    const clear = $("status-sel-clear");
    if (clear) clear.hidden = selected.size === 0;
    const chks = [...document.querySelectorAll("#status-rec-body .rec-chk")];
    const all = $("status-rec-all");
    if (all) {
      const every = chks.length > 0 && chks.every((c) => c.checked);
      all.checked = every;
      all.indeterminate = !every && chks.some((c) => c.checked);
    }
  }

  // 선택 id — 표에 보이는 정렬 순서대로. 출력 상한(200건)이 "어떤 200건"인지가
  // 이 순서다. 조건을 바꿔 지금 목록에 없는 선택은 뒤에 붙인다(선택은 유지되므로).
  function selectedIdsInOrder() {
    const ordered = sortRecords(allRecords)
      .map((r) => Number(r.id))
      .filter((id) => selected.has(id));
    const known = new Set(ordered);
    return ordered.concat([...selected].filter((id) => !known.has(id)));
  }

  // 목록 정렬 — 기본은 작업일 역순(서버 순서). 머리글을 누르면 그 열로 다시 정렬한다.
  // 서버가 최신 limit 건만 주므로 정렬 대상은 '지금 화면에 온 것'이고, 상한에 걸리면
  // 위 안내 줄이 그 사실을 말한다(정렬이 전체를 뒤진다고 오해하지 않도록).
  function sortRecords(items) {
    if (!recSort.key) return items;
    const { key, dir } = recSort;
    return items.slice().sort((a, b) => {
      const x = a[key];
      const y = b[key];
      let c;
      if (typeof x === "number" && typeof y === "number") c = x - y;
      else c = String(x == null ? "" : x).localeCompare(String(y == null ? "" : y), "ko");
      return dir === "asc" ? c : -c;
    });
  }

  function markSortHeaders() {
    document.querySelectorAll(".status-sortable th[data-sort]").forEach((th) => {
      th.classList.toggle("sorted-asc", th.dataset.sort === recSort.key && recSort.dir === "asc");
      th.classList.toggle("sorted-desc", th.dataset.sort === recSort.key && recSort.dir === "desc");
    });
  }

  // 조회 요약 — 몇 건을 어떤 조건으로 보고 있는지, 취소분이 몇 건 숨겨졌는지.
  function renderRecordSummary(data, shown) {
    const el = $("status-rec-summary");
    if (!el) return;
    const parts = [];
    const from = $("status-rec-from").value;
    const to = $("status-rec-to").value;
    parts.push(from || to ? `기간 ${from || "처음"} ~ ${to || "오늘"}` : "기간 전체");
    const worker = $("status-rec-worker").value;
    if (worker) parts.push(`작업자 ${worker}`);
    const product = $("status-rec-product").value;
    if (product) parts.push(`제품 ${product}`);
    const search = $("status-rec-search").value.trim();
    if (search) parts.push(`검색 "${search}"`);
    // '미확인만' 은 서버 필터라 shown·total 이 모두 미확인 기준이다 — 두 숫자가 같은
    // 모집단을 가리키도록 표현도 맞춘다("미확인 N건 (조건 전체 M건)").
    const unacked = isUnackedOnly();
    let text = `${parts.join(" · ")} · ${unacked ? "미확인 " : ""}${fmt(shown, 0)}건`;
    const total = Number(data.total_available || 0);
    if (total > shown) text += ` (조건 전체 ${fmt(total, 0)}건)`;
    // 취소분은 기본으로 숨긴다 — 몇 건이 빠졌는지 말해 주지 않으면 없는 줄 안다.
    // 단 '미확인만' 일 때는 적지 않는다: 서버가 이 수를 셀 때만 미확인 조건을 빼고 세어
    // (전체 취소분 − 미확인 건수)가 나온다. 6건짜리 목록 옆에 "취소 124건 숨김" 이
    // 붙어 뜻이 통하지 않는다. 서버 계산이 고쳐지면 이 예외도 함께 없앤다.
    const hidden = Number(data.canceled_hidden || 0);
    if (hidden > 0 && !unacked) text += ` · 취소 ${fmt(hidden, 0)}건 숨김`;
    el.textContent = text;
  }

  // 취소된 기록 상세 — 사유·취소자·시각·자동 삭제 예정일(F15). 목록의 '취소됨' 배지만으론
  // 복원/완전 삭제를 판단할 근거(왜·언제·언제 사라지는지)가 화면에 없었다. 서버가 감사
  // 로그에서 읽어 cancel_info 로 내려준다(과거 취소분 소급 표시).
  function cancelBlock(rec) {
    if (rec.status !== "canceled") return "";
    const info = rec.cancel_info || {};
    // 감사 로그 시각은 UTC(Z) — 화면의 작성 시각(로컬)과 섞이면 9시간 어긋나 보인다.
    // Z 가 붙은 값은 로컬로 변환해 표시한다.
    const dt = (s) => {
      if (!s) return "";
      const str = String(s);
      if (str.endsWith("Z")) {
        const d = new Date(str);
        if (!Number.isNaN(d.getTime())) {
          const p = (n) => String(n).padStart(2, "0");
          return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
        }
      }
      return esc(str.slice(0, 16).replace("T", " "));
    };
    const who = info.actor ? ` · 취소: ${esc(info.actor)}` : "";
    const at = info.canceled_at ? ` (${dt(info.canceled_at)})` : "";
    const purge = info.purge_at
      ? `<br />자동 삭제 예정: <b>${dt(info.purge_at)}</b> (취소 후 ${esc(String(info.retention_days))}일 보존, 그 전까지 [기록 복원] 가능)`
      : "";
    return `<div class="blend-cancel-block">
      <b>취소된 기록</b> — 사유: ${info.reason ? esc(info.reason) : '<span class="muted">(기록 없음)</span>'}${who}${at}${purge}
    </div>`;
  }

  function approvalCell(label, name, at, sign) {
    const img = sign ? `<img class="dhr-sign-img" src="${sign}" alt="서명" />` : "";
    return `<div class="dhr-sign">
      <div class="dhr-sign-role">${label}</div>${img}
      <div class="dhr-sign-name">${esc(name || "")}</div>
      <div class="dhr-sign-at">${at ? esc(at.slice(0, 16).replace("T", " ")) : ""}</div>
    </div>`;
  }

  async function openDetail(id) {
    let rec;
    try {
      rec = await request(`/blend/records/${id}`);
    } catch (e) {
      // 종전에는 행을 눌러도 아무 일이 없었다 — 실패가 콘솔에만 남아 '먹통'으로 보였다.
      notify(`기록을 불러오지 못했습니다: ${e.message || e}`, "error");
      return;
    }
    detailId = id;
    currentRecord = rec;
    setEditChromeHidden(false);
    $("status-detail-title").textContent = `배합 실적서 — ${rec.product_lot}`;
    const v = rec.variance || {};
    // 공정 설명 줄(레시피 '설명' 열) — 기록 당시 위치에 전폭 안내 행으로 삽입
    const steps = rec.steps || [];
    const stepRowsAt = (pos) => steps
      .filter((st) => st.position === pos)
      .map((st) => `<tr class="blend-step-row"><td colspan="7">▸ ${esc(st.note)}</td></tr>`)
      .join("");
    const rows = (rec.details || [])
      .map(
        (d, i) => {
          // 투입 로스 보정 분해 표시(2라운드) — detail.loss_comp_g>0 행의 이론량 옆 작은 배지.
          // DHR 인쇄물·Excel·다른 API 출력은 건드리지 않는다(이 상세 화면만).
          const comp = Number(d.loss_comp_g);
          const compBadge = comp > 0
            ? ` <span class="blend-losscomp-badge" title="기본 ${fmt(Number(d.theory_amount) - comp)} + 투입 로스 보정 ${fmt(comp)} = ${fmt(d.theory_amount)}">보정 +${fmt(comp)}g</span>`
            : "";
          return stepRowsAt(i) +
          `<tr><td>${i + 1}</td><td>${esc(d.material_name)}</td>` +
          `<td class="num">${fmt(d.ratio, 2)}</td><td class="num">${fmt(d.theory_amount)}${compBadge}</td>` +
          // 저울 연동 중 손입력한 자재는 실제량 옆에 행별 ⚠ (상세에서만 표시)
          `<td class="num">${fmt(d.actual_amount)}${d.manual_entry ? ' <span class="manual-entry-mark" title="수동 입력">⚠</span>' : ""}</td>` +
          `<td class="num ${d.variance > 0 ? "var-up" : d.variance < 0 ? "var-down" : ""}">${d.variance == null ? "-" : (d.variance > 0 ? "+" : "") + fmt(d.variance, 2)}</td>` +
          `<td>${esc(d.material_lot || "-")}</td></tr>`;
        },
      )
      .join("") + stepRowsAt((rec.details || []).length);
    const linkedVisc = (rec.viscosity || []).length
      ? `<ul class="blend-visc-list">${rec.viscosity
          .map(
            (x) =>
              `<li><b>${esc(x.product_code)}</b> ${fmt(x.viscosity)} <span class="muted small">${esc(x.measured_date || "")}${x.created_by ? " · " + esc(x.created_by) : ""}</span></li>`,
          )
          .join("")}</ul>`
      : '<p class="muted small">측정된 점도가 없습니다. (등록은 점도 관리 화면에서)</p>';
    // 점도 등록은 '점도 관리' 화면 한 곳으로 통일 — 여기선 측정값을 읽기전용으로만 표시.
    const visc = `<div class="blend-visc-block"><b>점도 측정</b>${linkedVisc}</div>`;
    const manualBadge = rec.manual_entry
      ? ' <span class="status-chip manual-entry-chip">⚠ 수동 입력</span>'
      : "";
    const bulkLine = rec.is_bulk_regenerated
      ? '<p class="dhr-note bulk-regen-note">※ 일괄 재생성 기록 — 현장 계량이 아니라 문서·계획용으로 한 번에 생성한 기록입니다.</p>'
      : "";
    // 머리에 있어야 할 값인데 화면에만 없던 것들(2026-08-14 검토 10번):
    //  · 품목코드 — ERP 대조의 기준 키. 기록 스냅샷 우선(없으면 레시피 폴백)으로 서버가 준다.
    //  · 세부 품명 — 같은 제품명 아래 갈래를 가르는 값(내부 컬럼명은 ink_name).
    //  · 반응기 — 어느 호기에서 돌았는지. 이월·점도 추적의 단서.
    // 인쇄물(DHR PDF/Excel)은 인허가 양식이라 손대지 않는다 — 이 상세 화면에만 붙인다.
    const detailName = rec.ink_name
      ? `<div><span class="dhr-k">세부 품명</span><b>${esc(rec.ink_name)}</b></div>`
      : "";
    const reactorCell = rec.reactor
      ? `<div><span class="dhr-k">반응기</span><b>${esc(rec.reactor)}</b></div>`
      : "";
    $("status-detail-body").innerHTML =
      `<div class="dhr-head">
        <div><span class="dhr-k">제품 LOT</span><b>${esc(rec.product_lot)}</b></div>
        <div><span class="dhr-k">제품</span><b>${esc(rec.product_name)}</b></div>
        <div><span class="dhr-k">품목코드</span><b>${esc(rec.product_code || "-")}</b></div>
        ${detailName}
        <div><span class="dhr-k">작업자</span><b>${esc(rec.worker)}${manualBadge}</b></div>
        <div><span class="dhr-k">작업일시</span><b>${esc(rec.work_date)} ${esc(rec.work_time || "")}</b></div>
        <div><span class="dhr-k">총 배합량</span><b>${fmt(rec.total_amount)} g</b></div>
        <div><span class="dhr-k">저울</span><b>${esc(rec.scale || "-")}</b></div>
        ${reactorCell}
      </div>
      ${bulkLine}
      ${cancelBlock(rec)}
      ${rescaleBlock(rec)}
      ${manualBlock(rec)}
      ${discardBlock(rec)}
      <div class="table-wrap"><table class="blend-table">
        <thead><tr><th>#</th><th>품목</th><th class="num">비율(%)</th><th class="num">이론(g)</th><th class="num">실제(g)</th><th class="num">편차(g)</th><th>자재 LOT</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><td colspan="3">합계</td><td class="num">${fmt(v.theory_total)}</td><td class="num">${fmt(v.actual_total)}</td><td class="num">${(v.net_variance > 0 ? "+" : "") + fmt(v.net_variance, 2)}</td><td></td></tr></tfoot>
      </table></div>
      ${rec.note ? `<p class="dhr-note">비고: ${esc(rec.note)}</p>` : ""}
      <div class="dhr-foot-row">
        <div class="dhr-approvals dhr-approvals-single">${approvalCell("작성", rec.created_by, rec.created_at, rec.worker_sign)}</div>
        ${visc}
      </div>`;
    // 상태에 따라 취소/복원/완전 삭제 버튼 노출을 가른다 — 정상 기록은 되돌릴 수 있는
    // '취소'만, 이미 취소된 기록에서만 '복원'과 (되돌릴 수 없는) '완전 삭제'가 보인다.
    const isCanceled = rec.status === "canceled";
    const setHidden = (elId, hidden) => { const el = $(elId); if (el) el.hidden = hidden; };
    setHidden("status-cancel-rec", isCanceled);
    setHidden("status-restore", !isCanceled);
    setHidden("status-delete", !isCanceled);
    // 미확인 증량/수기 입력 확인 처리 — 내용을 본 이 자리에서. 처리 후 목록·모달을 새로
    // 그려 배지가 그 자리에서 사라진다(대시보드·트레이는 다음 폴링에 반영).
    const wireAck = (btnId, path, label) => {
      const btn = $(btnId);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await request(`/blend/records/${rec.id}/${path}`, { method: "POST" });
          notify(`${label} 확인 처리했습니다.`, "success");
          await loadRecords({ keepPage: true });   // 목록 배지 제거(보던 쪽 유지)
          await openDetail(rec.id);                // 모달 재렌더 — 미확인 태그·버튼 제거
        } catch (e) {
          btn.disabled = false;
          notify(`확인 처리 실패: ${e.message || e}`, "error");
        }
      });
    };
    wireAck("detail-rescale-ack", "rescale-ack", "증량을");
    wireAck("detail-manual-ack", "manual-absence-ack", "수기 입력을");
    detailModal.open();
  }

  // ── 전체 수정(책임자 전용) ────────────────────────────────────
  // 헤더 액션(PDF/인쇄/Excel/수정/삭제 + 서명 체크)을 편집 중에는 숨긴다.
  const EDIT_HIDE_IDS = ["status-pdf", "status-excel", "status-edit",
                         "status-cancel-rec", "status-restore", "status-delete"];

  function setEditChromeHidden(hidden) {
    EDIT_HIDE_IDS.forEach((id) => { const el = $(id); if (el) el.style.display = hidden ? "none" : ""; });
    const signWrap = $("status-detail-sign");
    if (signWrap && signWrap.closest("label")) signWrap.closest("label").style.display = hidden ? "none" : "";
  }

  function editRow(d) {
    // 반응기 이월 행은 표식을 보존하고 실제량을 읽기전용으로 둔다(1차 총량으로 서버가
    // 강제하므로 수정 무의미). data-carried 로 저장 시 carried_over 를 되돌려보낸다.
    const carried = d.carried_over ? 1 : 0;
    const actualAttr = carried
      ? ' readonly title="반응기 이월 — 1차 총량으로 자동 기록(수정 불가)"'
      : "";
    const mark = carried
      ? ' <span class="carried-badge" title="반응기 이월 행 — 1차 총량으로 자동 기록">이월</span>'
      : "";
    return `<tr class="edit-row" data-carried="${carried}">
      <td><input class="input e-name" value="${esc(d.material_name || "")}" placeholder="자재명" />${mark}</td>
      <td><input class="input num e-ratio" type="number" step="0.01" min="0" value="${d.ratio ?? ""}" /></td>
      <td><input class="input num e-theory" type="number" step="0.01" min="0" value="${d.theory_amount ?? ""}" /></td>
      <td><input class="input num e-actual" type="number" step="0.01" min="0" value="${d.actual_amount ?? ""}"${actualAttr} /></td>
      <td><input class="input e-lot" value="${esc(d.material_lot || "")}" placeholder="LOT(선택)" /></td>
      <td><button class="btn btn-sm danger e-del" type="button" title="행 삭제">×</button></td>
    </tr>`;
  }

  function renderEditForm(rec) {
    setEditChromeHidden(true);
    $("status-detail-title").textContent = `배합 실적서 수정 — ${rec.product_lot}`;
    const rows = (rec.details || []).map(editRow).join("");
    $("status-detail-body").innerHTML =
      `<div class="edit-head-grid">
        <label class="edit-f"><span>제품명</span><input class="input" id="e-product" value="${esc(rec.product_name || "")}" /></label>
        <label class="edit-f"><span>작업자</span><input class="input" id="e-worker" value="${esc(rec.worker || "")}" /></label>
        <label class="edit-f"><span>작업일</span><input class="input" id="e-date" type="date" value="${esc(rec.work_date || "")}" /></label>
        <label class="edit-f"><span>작업시간</span><input class="input" id="e-time" value="${esc(rec.work_time || "")}" placeholder="HH:MM" /></label>
        <label class="edit-f"><span>총 배합량(g)</span><input class="input num" id="e-total" type="number" step="0.01" min="0" value="${rec.total_amount ?? ""}" /></label>
        <label class="edit-f"><span>저울</span><input class="input" id="e-scale" value="${esc(rec.scale || "")}" /></label>
        <label class="edit-f"><span>반응기(1~4, 선택)</span><input class="input num" id="e-reactor" type="number" min="1" max="4" value="${rec.reactor ?? ""}" /></label>
        <label class="edit-f edit-f-wide"><span>비고</span><input class="input" id="e-note" value="${esc(rec.note || "")}" /></label>
      </div>
      <div class="table-wrap"><table class="blend-table edit-table">
        <thead><tr><th>품목</th><th class="num">비율(%)</th><th class="num">이론(g)</th><th class="num">실제(g)</th><th>자재 LOT</th><th></th></tr></thead>
        <tbody id="e-rows">${rows}</tbody>
      </table></div>
      <div class="edit-actions">
        <button class="btn btn-sm" id="e-add-row" type="button">＋ 행 추가</button>
        <span class="edit-spacer"></span>
        <button class="btn btn-sm" id="e-cancel" type="button">취소</button>
        <button class="btn btn-sm accent" id="e-save" type="button">저장</button>
      </div>
      <p class="login-error" id="e-error" hidden></p>
      <p class="muted small">제품 LOT·서명·생성 정보는 그대로 유지됩니다. 자재별 편차는 레시피의 허용 편차(기본 ±0.05g) 이내여야 저장됩니다.</p>`;

    $("e-rows").addEventListener("click", (ev) => {
      const del = ev.target.closest(".e-del");
      if (!del) return;
      const tr = del.closest("tr");
      // 값이 든 행만 확인 — 빈 행 삭제는 조용히(BOM 편집기 행 삭제와 동일 규칙 2026-08-05).
      const filled = [...tr.querySelectorAll("input")].some((i) => i.value.trim() !== "");
      if (filled && !window.confirm("이 행을 삭제할까요? 입력된 값이 사라집니다.")) return;
      tr.remove();
    });
    $("e-add-row").addEventListener("click", () => {
      $("e-rows").insertAdjacentHTML("beforeend", editRow({}));
    });
    $("e-cancel").addEventListener("click", () => openDetail(rec.id));
    $("e-save").addEventListener("click", () => saveEdit(rec.id));
  }

  function collectEdit() {
    const details = [...document.querySelectorAll("#e-rows tr")].map((tr) => {
      const name = tr.querySelector(".e-name").value.trim();
      if (!name) return null;
      const numOrNull = (sel) => {
        const v = tr.querySelector(sel).value;
        return v === "" ? null : Number(v);
      };
      return {
        material_name: name,
        ratio: numOrNull(".e-ratio"),
        theory_amount: numOrNull(".e-theory"),
        actual_amount: numOrNull(".e-actual"),
        material_lot: tr.querySelector(".e-lot").value.trim() || null,
        // 반응기 이월 표식 보존 — 저장 시 손실되지 않게 되돌려보낸다. 서버가 recipe_id 로
        // 재검증하고 1차 총량으로 강제한다(create 경로와 동일 불변식).
        carried_over: tr.dataset.carried === "1",
      };
    }).filter(Boolean);
    const reactorRaw = $("e-reactor").value;
    return {
      // recipe_id 를 함께 보낸다 — 이월 재검증(파생·기준 자재·1차 LOT)과 레시피별 허용 편차
      // 적용에 필요. 미전송 시 이월 행이 서버에서 거부(파생 판정 불가)되므로 반드시 포함.
      recipe_id: currentRecord ? (currentRecord.recipe_id ?? null) : null,
      product_name: $("e-product").value.trim(),
      worker: $("e-worker").value.trim(),
      work_date: $("e-date").value,
      work_time: $("e-time").value.trim() || null,
      total_amount: Number($("e-total").value),
      scale: $("e-scale").value.trim() || null,
      note: $("e-note").value.trim() || null,
      reactor: reactorRaw === "" ? null : Number(reactorRaw),
      details,
    };
  }

  async function saveEdit(id) {
    const err = $("e-error");
    err.hidden = true;
    const body = collectEdit();
    if (!body.product_name) { err.textContent = "제품명을 입력하세요."; err.hidden = false; return; }
    if (!body.worker) { err.textContent = "작업자를 입력하세요."; err.hidden = false; return; }
    if (!body.work_date) { err.textContent = "작업일을 입력하세요."; err.hidden = false; return; }
    if (!(body.total_amount > 0)) { err.textContent = "총 배합량을 입력하세요."; err.hidden = false; return; }
    if (!body.details.length) { err.textContent = "자재를 1개 이상 입력하세요."; err.hidden = false; return; }
    const btn = $("e-save");
    IRMS.btnLoading && IRMS.btnLoading(btn, true);
    try {
      await request(`/blend/records/${id}`, { method: "PUT", body });
      notify("배합 기록을 수정했습니다.", "success");
      await openDetail(id);
      await loadRecords({ keepPage: true });
    } catch (e) {
      err.textContent = e.message || String(e);
      err.hidden = false;
    } finally {
      IRMS.btnLoading && IRMS.btnLoading(btn, false);
    }
  }

  if ($("status-edit")) {
    $("status-edit").addEventListener("click", () => {
      if (currentRecord) renderEditForm(currentRecord);
    });
  }

  // ── 조회·정렬·쪽 이동 ────────────────────────────────────────────
  $("status-rec-apply").addEventListener("click", () => loadRecords());
  // 머리글 정렬 — 같은 열을 다시 누르면 방향만 뒤집는다. 서버를 다시 부르지 않고
  // 이미 받아 둔 목록만 다시 그린다(종전에는 loadRecords 를 불러 API 를 두 번 치고
  // 선택까지 날렸다). 보던 쪽도 그대로 둔다.
  document.querySelectorAll(".status-sortable th[data-sort]").forEach((th) => {
    th.addEventListener("click", () => {
      const key = th.dataset.sort;
      recSort = {
        key,
        dir: recSort.key === key && recSort.dir === "desc" ? "asc" : "desc",
      };
      renderTable();
    });
  });
  const gotoPage = (delta) => {
    page += delta;
    renderTable();
    const wrap = document.querySelector("#status-rec-body");
    const box = wrap && wrap.closest(".table-wrap");
    if (box && box.scrollIntoView) box.scrollIntoView({ block: "start", behavior: "smooth" });
  };
  if ($("status-page-prev")) $("status-page-prev").addEventListener("click", () => gotoPage(-1));
  if ($("status-page-next")) $("status-page-next").addEventListener("click", () => gotoPage(1));

  // '미확인만'·'취소 포함' 은 서버 조건이라 다시 조회한다(체크 즉시 — 조회 버튼을
  // 또 누르게 하지 않는다). 선택 집합은 조회를 넘어 유지된다.
  if ($("status-rec-unacked")) $("status-rec-unacked").addEventListener("change", () => loadRecords());
  const canceledChk = $("status-rec-canceled");
  if (canceledChk) canceledChk.addEventListener("change", () => loadRecords());

  // 전체 선택 — 지금 보이는 쪽의 행에만 적용된다(다른 쪽의 선택은 건드리지 않는다).
  $("status-rec-all").addEventListener("change", (e) => {
    document.querySelectorAll("#status-rec-body .rec-chk").forEach((c) => {
      c.checked = e.target.checked;
      if (e.target.checked) selected.add(Number(c.value));
      else selected.delete(Number(c.value));
    });
    updateSelectionUI();
  });
  if ($("status-sel-clear")) {
    $("status-sel-clear").addEventListener("click", () => {
      selected.clear();
      document.querySelectorAll("#status-rec-body .rec-chk").forEach((c) => { c.checked = false; });
      updateSelectionUI();
    });
  }

  // ── 서명 체크 단일화 ─────────────────────────────────────────────
  // 툴바(목록 출력용)와 모달(단건 출력용) 두 곳에 같은 뜻의 체크가 있었는데 서로 몰랐다.
  // 툴바에서 켜고 상세를 열어 PDF 를 뽑으면 서명이 빠졌다 — 하나의 상태로 묶는다.
  const signTop = $("status-sign");
  const signDetail = $("status-detail-sign");
  function setSign(on) {
    if (signTop) signTop.checked = on;
    if (signDetail) signDetail.checked = on;
  }
  const signOn = () => Boolean((signTop && signTop.checked) || (signDetail && signDetail.checked));
  if (signTop) signTop.addEventListener("change", () => setSign(signTop.checked));
  if (signDetail) signDetail.addEventListener("change", () => setSign(signDetail.checked));

  // ── 출력 ─────────────────────────────────────────────────────────
  // 긴 출력 작업(일괄 PDF·ZIP·전체 Excel)은 새 탭 스트리밍이라 완료 신호가 없다.
  // 예전에는 누르면 빈 탭만 열리고 몇 분간 아무 일도 없어, 안 된 줄 알고 다시 눌러
  // 같은 변환을 큐에 하나 더 쌓았다(서버는 Excel 변환을 직렬로 처리한다).
  // 완료를 알 수 없으니 '무엇이 진행 중인지' 알리고 그동안 버튼을 잠근다.
  function startLongExport(btn, count, label) {
    notify(
      `${label} ${fmt(count, 0)}건을 준비합니다 — 새 탭에서 다운로드됩니다. ` +
      "건수가 많으면 몇 분 걸릴 수 있으니 기다려 주세요.",
      "warn",
    );
    if (!btn) return;
    const orig = btn.innerHTML;
    btn.disabled = true;
    btn.textContent = "준비 중…";
    // 건당 여유를 두되 상한 90초 — 완료를 알 수 없으므로 재시도 자체를 막지는 않는다.
    const wait = Math.min(90000, 4000 + count * 700);
    setTimeout(() => { btn.disabled = false; btn.innerHTML = orig; }, wait);
  }

  // 취소된 기록은 배합일지 출력에서 서버가 제외한다 — 선택에 섞여 있으면 결과 부수가
  // 조용히 줄어 "몇 장이 안 나왔다"로 나타났다. 출력 전에 몇 건이 빠지는지 말한다.
  function confirmCanceledInSelection(ids) {
    const byId = new Map(allRecords.map((r) => [Number(r.id), r]));
    const n = ids.filter((id) => {
      const r = byId.get(id);
      return r && r.status === "canceled";
    }).length;
    if (!n) return true;
    return window.confirm(`선택 중 취소된 ${n}건은 배합일지에서 제외됩니다. 계속할까요?`);
  }

  function wireBatchExport(btnId, path, label) {
    const btn = $(btnId);
    if (!btn) return;
    btn.addEventListener("click", () => {
      const ids = selectedIdsInOrder();
      if (!ids.length) { notify("기록을 선택하세요(전체 선택 가능).", "warn"); return; }
      if (!confirmCanceledInSelection(ids)) return;
      // 어떤 200건인지 말한다 — 종전 "최대 200건" 은 무작위 200건으로 읽혔다.
      if (ids.length > MAX_PRINT) {
        notify(`표 정렬 순서 기준 위에서 ${MAX_PRINT}건까지 출력합니다.`, "warn");
      }
      const sign = signOn() ? "&sign=1" : "";
      const picked = ids.slice(0, MAX_PRINT);
      startLongExport(btn, picked.length, label);
      window.open(`/api/blend/records/${path}?ids=${picked.join(",")}${sign}`, "_blank");
    });
  }
  wireBatchExport("status-rec-dhr-batch", "dhr-batch", "배합일지");
  wireBatchExport("status-rec-dhr-zip", "dhr-zip", "배합일지 ZIP");

  // 책임자에게만 렌더되므로 null 가드 필수 — 없으면 비책임자 화면에서 예외가 나
  // 이후 리스너(조회·상세 열기 등)가 전부 등록되지 않는다.
  const bulkCancelBtn = $("status-rec-delete-selected");
  if (bulkCancelBtn) bulkCancelBtn.addEventListener("click", async () => {
    const ids = selectedIdsInOrder();
    if (!ids.length) { notify("취소할 기록을 선택하세요.", "warn"); return; }
    // 상한 — 되돌릴 수 있는 동작이지만 한 번에 수백 건은 사고다(순차 처리라 수 분이 걸리고,
    // 중간에 끊기면 어디까지 갔는지 사람이 세야 한다). 잘라서 진행하지 않고 멈춘다.
    if (ids.length > MAX_BULK_CANCEL) {
      notify(
        `한 번에 최대 ${MAX_BULK_CANCEL}건까지 취소할 수 있습니다 — 지금 ${fmt(ids.length, 0)}건 선택. ` +
        "선택을 나눠서 진행해 주세요.",
        "error",
      );
      return;
    }
    if (!window.confirm(
      `${ids.length}건을 취소합니다 — 각 기록에 사유가 남습니다.\n` +
      "(기록은 지워지지 않고 목록·출력에서 빠지며, 상세에서 복원할 수 있습니다.)"
    )) return;
    const reason = window.prompt(`선택한 배합 기록 ${ids.length}건의 취소 사유를 입력하세요.`);
    if (reason === null) return;
    if (!reason.trim()) { notify("사유를 입력해야 취소할 수 있습니다.", "error"); return; }
    // 순차 처리 중 실패해도 이미 처리된 건수를 반드시 알린다 — 예전에는 오류만 띄우고
    // 몇 건이 이미 지워졌는지 알려주지 않아 작업자가 상태를 알 수 없었다.
    let done = 0;
    const origLabel = bulkCancelBtn.textContent;
    bulkCancelBtn.disabled = true;
    try {
      for (const id of ids) {
        bulkCancelBtn.textContent = `취소 중 ${done + 1}/${ids.length}`;
        await cancelRecord(id, reason.trim());
        selected.delete(Number(id));   // 취소한 건은 선택에서 뺀다(다음 동작에 섞이지 않게)
        done += 1;
      }
      notify(`${done}건을 취소했습니다.`, "success");
    } catch (e) {
      notify(`${done}건 취소 후 실패했습니다 (${ids.length - done}건 남음): ${e.message || e}`, "error");
    } finally {
      bulkCancelBtn.disabled = false;
      bulkCancelBtn.textContent = origLabel;
    }
    await loadRecords({ keepPage: true });
  });

  $("status-rec-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadRecords();
  });
  $("status-detail-close").addEventListener("click", () => detailModal.close());
  $("status-pdf").addEventListener("click", () => {
    if (!detailId) return;
    window.open(`/api/blend/records/${detailId}/pdf${signOn() ? "?sign=1" : ""}`, "_blank");
  });
  $("status-excel").addEventListener("click", () => {
    if (!detailId) return;
    // 서명 체크를 단건 Excel 도 따른다 — 종전에는 이 경로에서만 조용히 무시돼
    // 서명 없는 파일이 서명본으로 오해됐다(서버가 sign 을 지원한다, 2026-08-14).
    window.open(`/api/blend/records/${detailId}/export${signOn() ? "?sign=1" : ""}`, "_blank");
  });
  // [취소] — 기본 경로. 기록은 남고 목록·출력·집계에서 빠지며 언제든 복원할 수 있다.
  const cancelBtn = $("status-cancel-rec");
  if (cancelBtn) cancelBtn.addEventListener("click", async () => {
    if (!detailId) return;
    // 보존 기한(기본 3일)을 결정하는 순간에 말한다 — 기한은 title 툴팁에만 있어서
    // '기한 없는 약속'으로 읽히던 문제(2026-08-05 전수 감사 R-1). 기한이 지나면
    // 서버가 기동 시 자동 완전 삭제한다(IRMS_CANCELED_RETENTION_DAYS).
    const reason = window.prompt(
      "이 배합 기록을 취소합니다. 사유를 입력하세요.\n" +
      "(기록은 목록·출력에서 빠지며, 취소 후 3일 안에는 복원할 수 있습니다.\n" +
      " 3일이 지나면 자동으로 완전히 삭제됩니다.)"
    );
    if (reason === null) return;              // 취소 버튼
    if (!reason.trim()) { notify("사유를 입력해야 취소할 수 있습니다.", "error"); return; }
    try {
      await cancelRecord(detailId, reason.trim());
      detailModal.close();
      detailId = null;
      notify("배합 기록을 취소했습니다. 필요하면 상세에서 복원할 수 있습니다.", "success");
      await loadRecords({ keepPage: true });
    } catch (e) {
      notify(`취소 실패: ${e.message || e}`, "error");
    }
  });

  // [복원] — 취소된 기록을 되돌린다.
  const restoreBtn = $("status-restore");
  if (restoreBtn) restoreBtn.addEventListener("click", async () => {
    if (!detailId) return;
    try {
      await restoreRecord(detailId);
      const id = detailId;
      notify("배합 기록을 복원했습니다.", "success");
      await loadRecords({ keepPage: true });
      await openDetail(id);   // 버튼 상태를 새 상태로 다시 그린다
    } catch (e) {
      notify(`복원 실패: ${e.message || e}`, "error");
    }
  });

  // [완전 삭제] — 되돌릴 수 없다. 취소된 기록에서만 보이고, 제품 LOT 을 정확히 입력해야
  // 진행된다(오클릭 방지). 사유는 서버가 필수로 요구하며 기록 전체 스냅샷과 함께 감사에 남는다.
  // 버튼들은 책임자에게만 렌더링된다(can_manage) — 비책임자 화면에서는 null 이므로
  // 반드시 가드한다. 가드가 없으면 이 줄에서 예외가 나 이후 리스너가 전부 등록되지 않는다.
  const hardDelBtn = $("status-delete");
  if (hardDelBtn) hardDelBtn.addEventListener("click", async () => {
    if (!detailId || !currentRecord) return;
    const lot = String(currentRecord.product_lot || "");
    const typed = window.prompt(
      `되돌릴 수 없는 완전 삭제입니다.\n진행하려면 제품 LOT 을 그대로 입력하세요:\n${lot}`
    );
    if (typed === null) return;
    if (typed.trim() !== lot) { notify("제품 LOT 이 일치하지 않아 중단했습니다.", "error"); return; }
    const reason = window.prompt("완전 삭제 사유를 입력하세요(감사 기록에 남습니다).");
    if (reason === null) return;
    if (!reason.trim()) { notify("사유를 입력해야 삭제할 수 있습니다.", "error"); return; }
    try {
      await hardDeleteRecord(detailId, reason.trim());
      selected.delete(Number(detailId));
      detailModal.close();
      detailId = null;
      notify("배합 기록을 완전히 삭제했습니다.", "success");
      await loadRecords({ keepPage: true });
    } catch (e) {
      notify(`삭제 실패: ${e.message || e}`, "error");
    }
  });

  // 전체 Excel — 종전에는 location.assign 이라 서버가 변환하는 몇 분 동안 이 화면을
  // 떠나 있었고, 실패하면 오류 페이지에 남아 조회 조건·선택이 전부 날아갔다.
  // 새 탭으로 내보내고 진행 안내를 띄운다(일괄 출력과 같은 규칙).
  $("status-rec-export-all").addEventListener("click", () => {
    const q = new URLSearchParams();
    const map = {
      start_date: $("status-rec-from").value,
      end_date: $("status-rec-to").value,
      worker: $("status-rec-worker").value,
      // 목록과 같은 제품 필터 — 화면은 걸러 놓고 파일은 전 제품이던 어긋남 해소(R-12).
      product: $("status-rec-product") ? $("status-rec-product").value : "",
      search: $("status-rec-search").value.trim(),
    };
    Object.entries(map).forEach(([k, val]) => {
      if (val) q.set(k, val);
    });
    // 화면의 '취소 포함' 체크와 같이 취소 기록까지 내려받는다 — 정합.
    if ($("status-rec-canceled") && $("status-rec-canceled").checked) {
      q.set("include_canceled", "1");
    }
    const count = Number(listMeta.total_available || allRecords.length || 0);
    startLongExport($("status-rec-export-all"), count, "전체 Excel");
    window.open(`/api/blend/records/export-all?${q.toString()}`, "_blank");
  });

  // ── 딥링크 ───────────────────────────────────────────────────────
  // ?search= : 배합 분석의 자재 LOT 추적 등에서 특정 LOT/제품으로 바로 필터된 기록 열기.
  // ?from=&to= : 대시보드 '오늘 배합' 카드처럼 기간을 지정해 들어오는 경로.
  // ?unacked=1 : 미확인(증량·수기)만 — 트레이 재촉 알림에서 바로 그 목록으로.
  const urlParams = new URLSearchParams(window.location.search);
  const urlSearch = urlParams.get("search");
  if (urlSearch) $("status-rec-search").value = urlSearch;
  const urlFrom = urlParams.get("from");
  if (urlFrom) $("status-rec-from").value = urlFrom;
  const urlTo = urlParams.get("to");
  if (urlTo) $("status-rec-to").value = urlTo;
  const urlUnacked = String(urlParams.get("unacked") || "").toLowerCase();
  if (["1", "true", "yes", "on"].includes(urlUnacked) && $("status-rec-unacked")) {
    $("status-rec-unacked").checked = true;
  }

  loadWorkers();
  loadProducts();
  loadRecords();
});
