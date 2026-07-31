/**
 * status.js — 배합 기록 · DHR 관리 (/status).
 * 배합 기록 목록(필터) + 클릭 시 제조이력서(DHR) 조회 + 인쇄/Excel.
 * 결재·점도 등록 등 쓰기 작업은 배합 화면에서 수행한다(여기는 조회·출력 중심).
 */
document.addEventListener("DOMContentLoaded", () => {
  const IRMS = window.IRMS || {};
  const request = IRMS._core && IRMS._core.request;
  const $ = (id) => document.getElementById(id);
  // 기본 소수 2자리 — 저울(XP 0.01g) 해상도에 맞춤
  const fmt = (v, d = 2) =>
    v === null || v === undefined || v === ""
      ? "-"
      : Number(v).toLocaleString("ko-KR", { maximumFractionDigits: d });
  const esc = (s) =>
    String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
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
      const target = focusEl
        || (opts.initialFocus && $(opts.initialFocus))
        || overlay.querySelector(".ss-modal");
      if (target && target.focus) setTimeout(() => { try { target.focus(); } catch (_e) { /* noop */ } }, 0);
    }
    function close() {
      if (overlay.hidden) return;
      overlay.hidden = true;
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
  const detailModal = createModal("status-detail-modal", { initialFocus: "status-detail-close" });
  // 증량 요약 맵(record_id → {rescale_count, rescale_unacked, rescale_events}).
  // 목록/상세 응답에 rescale 필드가 없어 자체 요약 엔드포인트로 병합 안전하게 채운다.
  let rescaleMap = {};

  async function loadRescaleMap() {
    try {
      const data = await request("/blend/rescales/summary");
      const map = {};
      (data.items || []).forEach((it) => { map[it.id] = it; });
      rescaleMap = map;
    } catch (_e) {
      rescaleMap = {};
    }
  }

  // 목록 배지 — 증량이 있으면 표시, 미승인(책임자 부재 진행)은 빨간 배지.
  function rescaleBadge(id) {
    const info = rescaleMap[id];
    if (!info) return "";
    let out = "";
    if (info.rescale_count) {
      out += info.rescale_unacked
        ? ' <span class="rescale-badge unacked" title="책임자 미승인 증량">미승인 증량</span>'
        : ` <span class="rescale-badge" title="증량 승인됨">증량 ${info.rescale_count}회</span>`;
    }
    // 수기 입력을 책임자 부재로 진행한 기록 — 확인 전까지 배지 유지(증량과 동일 정책).
    if (info.manual_unacked) {
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
  function rescaleBlock(id) {
    const info = rescaleMap[id];
    const events = (info && info.rescale_events) || [];
    if (!events.length) return "";
    const rows = events
      .map((e, i) => {
        const who = e.approver
          ? `승인자: ${esc(e.approver)}`
          : e.absence_reason
            ? `책임자 부재: ${esc(e.absence_reason)}`
            : "책임자 부재";
        return `<li>${i + 1}. ${fmt(e.before_total)} g → ${fmt(e.after_total)} g <span class="muted small">(${who})</span></li>`;
      })
      .join("");
    const unackedTag = info.rescale_unacked
      ? ' <span class="rescale-badge unacked">미승인</span>'
      : "";
    // 확인 처리는 내용을 보는 이 자리에서 — 목록/대시보드에서 안 보고 누르게 하지 않는다.
    const ackBtn = info.rescale_unacked && canManage()
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
    const info = rescaleMap[rec.id] || {};
    const reason = rec.manual_absence_reason;
    if (!reason && !info.manual_unacked) return "";
    const unackedTag = info.manual_unacked
      ? ' <span class="rescale-badge unacked">미확인</span>'
      : "";
    const ackBtn = info.manual_unacked && canManage()
      ? ` <button class="btn btn-sm accent" id="detail-manual-ack" data-id="${rec.id}" type="button">확인 처리</button>`
      : "";
    return `<div class="blend-rescale-block"><b>수기 입력(책임자 부재)${unackedTag}</b>${ackBtn}`
      + `<p class="muted small" style="margin:0.3rem 0 0;">사유: ${esc(reason || "-")}</p></div>`;
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

  async function loadProducts() {
    try {
      const data = await request("/blend/product-usage");
      const sel = $("status-rec-product");
      const names = (data.items || [])
        .map((it) => it.product_name)
        .filter((n) => n);
      names.sort((a, b) => String(a).localeCompare(String(b), "ko"));
      names.forEach((name) => {
        const o = document.createElement("option");
        o.value = name;
        o.textContent = name;
        sel.appendChild(o);
      });
    } catch (_e) {
      /* 제품 목록 실패는 조회에 영향 없음 (작업자와 동일 fail-soft) */
    }
  }

  async function loadRecords() {
    const body = $("status-rec-body");
    // 조회 중 로딩 표시 — 초기 진입·재조회 모두 공용 .spinner 로.
    body.innerHTML = '<tr><td colspan="7"><div class="table-loading"><span class="spinner"></span> 불러오는 중…</div></td></tr>';
    const query = {
      start_date: $("status-rec-from").value || undefined,
      end_date: $("status-rec-to").value || undefined,
      worker: $("status-rec-worker").value || undefined,
      product: $("status-rec-product").value || undefined,
      search: $("status-rec-search").value.trim() || undefined,
      include_canceled: ($("status-rec-canceled") && $("status-rec-canceled").checked) ? 1 : undefined,
    };
    try {
      const [data] = await Promise.all([
        request("/blend/records", { query }),
        loadRescaleMap(),
      ]);
      let items = data.items || [];
      // '미확인만' — 요약 맵(rescaleMap)의 미확인 플래그로 로드된 목록을 거른다.
      // 확인 처리 자체는 상세 모달 안에 있다(내용을 보고 누르는 자리).
      if ($("status-rec-unacked") && $("status-rec-unacked").checked) {
        items = items.filter((r) => {
          const info = rescaleMap[r.id];
          return info && (info.rescale_unacked || info.manual_unacked);
        });
      }
      const note = $("status-rec-note");
      if (note) {
        // 서버가 최신 limit(기본 500)건만 반환 — 상한 도달 시 전체 M 과 함께 좁히기 안내.
        if (data.truncated) {
          note.textContent =
            `최근 ${fmt(data.limit || items.length, 0)}건만 표시 (전체 ${fmt(data.total_available, 0)}건) — ` +
            "날짜·작업자·검색으로 범위를 좁히거나 ‘전체 Excel’로 내려받으세요.";
          note.hidden = false;
        } else {
          note.hidden = true;
        }
      }
      if (!items.length) {
        const unackedOnly = $("status-rec-unacked") && $("status-rec-unacked").checked;
        body.innerHTML = unackedOnly
          ? '<tr><td colspan="7" class="muted">미확인 증량·수기 입력이 없습니다.</td></tr>'
          : '<tr><td colspan="7" class="muted">기록이 없습니다.</td></tr>';
        return;
      }
      body.innerHTML = "";
      const allChk = $("status-rec-all");
      if (allChk) allChk.checked = false;
      items.forEach((r) => {
        const tr = document.createElement("tr");
        tr.className = "blend-row";
        // 수동 입력 ⚠ — 서버가 책임자에게만 플래그를 내려주므로(비책임자는 False 마스킹)
        // 목록에 표시해도 책임자 로그인 시에만 보인다.
        const manualTag = r.manual_entry ? ' <span class="manual-entry-dot" title="수동 입력">⚠</span>' : "";
        // 취소된 기록은 목록에서 한눈에 구분되어야 한다(취소 포함으로 조회했을 때).
        // 취소는 되돌릴 수 있는 양성 상태 — 붉은 '미승인' 배지가 아니라 중립 .status-canceled 칩.
        const canceledTag = r.status === "canceled"
          ? ' <span class="status-chip status-canceled" title="취소된 기록 — 상세에서 복원할 수 있습니다">취소됨</span>' : "";
        tr.innerHTML =
          `<td class="chk-col"><input type="checkbox" class="rec-chk" value="${r.id}" /></td>` +
          `<td>${esc(r.work_date)}</td><td>${esc(r.product_lot)}${manualTag}${canceledTag}${rescaleBadge(r.id)}${bulkBadge(r)}</td>` +
          `<td>${esc(r.product_name)}</td>` +
          `<td>${esc(r.worker)}</td><td class="num">${fmt(r.total_amount)}</td><td>${esc(r.scale || "-")}</td>`;
        tr.addEventListener("click", () => openDetail(r.id));
        tr.querySelector(".rec-chk").addEventListener("click", (e) => e.stopPropagation());
        body.appendChild(tr);
      });
    } catch (e) {
      body.innerHTML = `<tr><td colspan="7" class="muted">불러오기 실패: ${esc(e.message || e)}</td></tr>`;
      const note = $("status-rec-note");
      if (note) note.hidden = true;
    }
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
    const rec = await request(`/blend/records/${id}`);
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
        (d, i) =>
          stepRowsAt(i) +
          `<tr><td>${i + 1}</td><td>${esc(d.material_name)}</td>` +
          `<td class="num">${fmt(d.ratio, 2)}</td><td class="num">${fmt(d.theory_amount)}</td>` +
          // 저울 연동 중 손입력한 자재는 실제량 옆에 행별 ⚠ (상세에서만 표시)
          `<td class="num">${fmt(d.actual_amount)}${d.manual_entry ? ' <span class="manual-entry-mark" title="수동 입력">⚠</span>' : ""}</td>` +
          `<td class="num ${d.variance > 0 ? "var-up" : d.variance < 0 ? "var-down" : ""}">${d.variance == null ? "-" : (d.variance > 0 ? "+" : "") + fmt(d.variance, 2)}</td>` +
          `<td>${esc(d.material_lot || "-")}</td></tr>`,
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
    $("status-detail-body").innerHTML =
      `<div class="dhr-head">
        <div><span class="dhr-k">제품 LOT</span><b>${esc(rec.product_lot)}</b></div>
        <div><span class="dhr-k">제품</span><b>${esc(rec.product_name)}</b></div>
        <div><span class="dhr-k">작업자</span><b>${esc(rec.worker)}${manualBadge}</b></div>
        <div><span class="dhr-k">작업일시</span><b>${esc(rec.work_date)} ${esc(rec.work_time || "")}</b></div>
        <div><span class="dhr-k">총 배합량</span><b>${fmt(rec.total_amount)} g</b></div>
        <div><span class="dhr-k">저울</span><b>${esc(rec.scale || "-")}</b></div>
      </div>
      ${bulkLine}
      ${rescaleBlock(rec.id)}
      ${manualBlock(rec)}
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
    const setHidden = (id, hidden) => { const el = $(id); if (el) el.hidden = hidden; };
    setHidden("status-cancel-rec", isCanceled);
    setHidden("status-restore", !isCanceled);
    setHidden("status-delete", !isCanceled);
    // 미확인 증량/수기 입력 확인 처리 — 내용을 본 이 자리에서. 처리 후 요약 맵·목록·
    // 모달을 새로 그려 배지가 그 자리에서 사라진다(대시보드·트레이는 다음 폴링에 반영).
    const wireAck = (btnId, path, label) => {
      const btn = $(btnId);
      if (!btn) return;
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        try {
          await request(`/blend/records/${rec.id}/${path}`, { method: "POST" });
          IRMS.notify(`${label} 확인 처리했습니다.`, "success");
          await loadRecords();      // 요약 맵 갱신 포함 — 목록 배지 제거
          openDetail(rec.id);       // 모달 재렌더 — 미확인 태그·버튼 제거
        } catch (e) {
          btn.disabled = false;
          IRMS.notify(`확인 처리 실패: ${e.message || e}`, "error");
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
      <p class="muted small">제품 LOT·서명·생성 정보는 그대로 유지됩니다. 자재별 편차는 ±0.05g 이내여야 저장됩니다.</p>`;

    $("e-rows").addEventListener("click", (ev) => {
      const del = ev.target.closest(".e-del");
      if (del) del.closest("tr").remove();
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
      IRMS.notify("배합 기록을 수정했습니다.", "success");
      await openDetail(id);
      await loadRecords();
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

  $("status-rec-apply").addEventListener("click", loadRecords);
  // 미확인만 — 체크 즉시 다시 그린다(조회 버튼을 또 누르게 하지 않는다).
  if ($("status-rec-unacked")) $("status-rec-unacked").addEventListener("change", loadRecords);
  $("status-rec-all").addEventListener("change", (e) => {
    document.querySelectorAll("#status-rec-body .rec-chk").forEach((c) => { c.checked = e.target.checked; });
  });
  // 긴 출력 작업(일괄 PDF·ZIP·전체 Excel)은 새 탭 스트리밍이라 완료 신호가 없다.
  // 예전에는 누르면 빈 탭만 열리고 몇 분간 아무 일도 없어, 안 된 줄 알고 다시 눌러
  // 같은 변환을 큐에 하나 더 쌓았다(서버는 Excel 변환을 직렬로 처리한다).
  // 완료를 알 수 없으니 '무엇이 진행 중인지' 알리고 그동안 버튼을 잠근다.
  function startLongExport(btn, count, label) {
    IRMS.notify(
      `${label} ${count}건을 준비합니다 — 새 탭에서 다운로드됩니다. ` +
      "건수가 많으면 몇 분 걸릴 수 있으니 기다려 주세요.",
      "warn",
    );
    if (!btn) return;
    const orig = btn.textContent;
    btn.disabled = true;
    btn.textContent = "준비 중…";
    // 건당 여유를 두되 상한 90초 — 완료를 알 수 없으므로 재시도 자체를 막지는 않는다.
    const wait = Math.min(90000, 4000 + count * 700);
    setTimeout(() => { btn.disabled = false; btn.textContent = orig; }, wait);
  }

  $("status-rec-dhr-batch").addEventListener("click", () => {
    const ids = [...document.querySelectorAll("#status-rec-body .rec-chk:checked")].map((c) => c.value);
    if (!ids.length) { IRMS.notify("기록을 선택하세요(전체 선택 가능).", "warn"); return; }
    if (ids.length > 200) IRMS.notify("한 번에 최대 200건까지 출력합니다.", "warn");
    const sign = $("status-sign") && $("status-sign").checked ? "&sign=1" : "";
    startLongExport($("status-rec-dhr-batch"), Math.min(ids.length, 200), "배합일지");
    window.open(`/api/blend/records/dhr-batch?ids=${ids.slice(0, 200).join(",")}${sign}`, "_blank");
  });
  $("status-rec-dhr-zip").addEventListener("click", () => {
    const ids = [...document.querySelectorAll("#status-rec-body .rec-chk:checked")].map((c) => c.value);
    if (!ids.length) { IRMS.notify("기록을 선택하세요(전체 선택 가능).", "warn"); return; }
    if (ids.length > 200) IRMS.notify("한 번에 최대 200건까지 출력합니다.", "warn");
    const sign = $("status-sign") && $("status-sign").checked ? "&sign=1" : "";
    startLongExport($("status-rec-dhr-zip"), Math.min(ids.length, 200), "배합일지 ZIP");
    window.open(`/api/blend/records/dhr-zip?ids=${ids.slice(0, 200).join(",")}${sign}`, "_blank");
  });
  // 책임자에게만 렌더되므로 null 가드 필수 — 없으면 비책임자 화면에서 예외가 나
  // 이후 리스너(조회·상세 열기 등)가 전부 등록되지 않는다.
  const bulkCancelBtn = $("status-rec-delete-selected");
  if (bulkCancelBtn) bulkCancelBtn.addEventListener("click", async () => {
    const ids = [...document.querySelectorAll("#status-rec-body .rec-chk:checked")].map((c) => Number(c.value));
    if (!ids.length) { IRMS.notify("취소할 기록을 선택하세요.", "warn"); return; }
    const reason = window.prompt(
      `선택한 배합 기록 ${ids.length}건을 취소합니다. 사유를 입력하세요.\n` +
      "(기록은 지워지지 않고 목록·출력에서 빠집니다. 상세에서 복원할 수 있습니다.)"
    );
    if (reason === null) return;
    if (!reason.trim()) { IRMS.notify("사유를 입력해야 취소할 수 있습니다.", "error"); return; }
    // 순차 처리 중 실패해도 이미 처리된 건수를 반드시 알린다 — 예전에는 오류만 띄우고
    // 몇 건이 이미 지워졌는지 알려주지 않아 작업자가 상태를 알 수 없었다.
    let done = 0;
    // 100건이면 수십 초가 걸린다 — 그동안 버튼이 계속 눌리면 같은 취소가 겹쳐 돈다.
    const origLabel = bulkCancelBtn.textContent;
    bulkCancelBtn.disabled = true;
    try {
      for (const id of ids) {
        bulkCancelBtn.textContent = `취소 중 ${done + 1}/${ids.length}`;
        await cancelRecord(id, reason.trim());
        done += 1;
      }
      IRMS.notify(`${done}건을 취소했습니다.`, "success");
    } catch (e) {
      IRMS.notify(`${done}건 취소 후 실패했습니다 (${ids.length - done}건 남음): ${e.message || e}`, "error");
    } finally {
      bulkCancelBtn.disabled = false;
      bulkCancelBtn.textContent = origLabel;
    }
    await loadRecords();
  });
  $("status-rec-search").addEventListener("keydown", (e) => {
    if (e.key === "Enter") loadRecords();
  });
  // '취소 포함' 토글 — 켜면 취소된 기록까지 조회해 상세에서 복원할 수 있다.
  const canceledChk = $("status-rec-canceled");
  if (canceledChk) canceledChk.addEventListener("change", loadRecords);
  $("status-detail-close").addEventListener("click", () => detailModal.close());
  $("status-pdf").addEventListener("click", () => {
    if (!detailId) return;
    const sign = $("status-detail-sign") && $("status-detail-sign").checked ? "?sign=1" : "";
    window.open(`/api/blend/records/${detailId}/pdf${sign}`, "_blank");
  });
  $("status-excel").addEventListener("click", () => {
    if (detailId) window.location.assign(`/api/blend/records/${detailId}/export`);
  });
  // [취소] — 기본 경로. 기록은 남고 목록·출력·집계에서 빠지며 언제든 복원할 수 있다.
  const cancelBtn = $("status-cancel-rec");
  if (cancelBtn) cancelBtn.addEventListener("click", async () => {
    if (!detailId) return;
    const reason = window.prompt(
      "이 배합 기록을 취소합니다. 사유를 입력하세요.\n" +
      "(기록은 지워지지 않고 목록·출력에서 빠집니다. 나중에 복원할 수 있습니다.)"
    );
    if (reason === null) return;              // 취소 버튼
    if (!reason.trim()) { IRMS.notify("사유를 입력해야 취소할 수 있습니다.", "error"); return; }
    try {
      await cancelRecord(detailId, reason.trim());
      detailModal.close();
      detailId = null;
      IRMS.notify("배합 기록을 취소했습니다. 필요하면 상세에서 복원할 수 있습니다.", "success");
      await loadRecords();
    } catch (e) {
      IRMS.notify(`취소 실패: ${e.message || e}`, "error");
    }
  });

  // [복원] — 취소된 기록을 되돌린다.
  const restoreBtn = $("status-restore");
  if (restoreBtn) restoreBtn.addEventListener("click", async () => {
    if (!detailId) return;
    try {
      await restoreRecord(detailId);
      const id = detailId;
      IRMS.notify("배합 기록을 복원했습니다.", "success");
      await loadRecords();
      await openDetail(id);   // 버튼 상태를 새 상태로 다시 그린다
    } catch (e) {
      IRMS.notify(`복원 실패: ${e.message || e}`, "error");
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
    if (typed.trim() !== lot) { IRMS.notify("제품 LOT 이 일치하지 않아 중단했습니다.", "error"); return; }
    const reason = window.prompt("완전 삭제 사유를 입력하세요(감사 기록에 남습니다).");
    if (reason === null) return;
    if (!reason.trim()) { IRMS.notify("사유를 입력해야 삭제할 수 있습니다.", "error"); return; }
    try {
      await hardDeleteRecord(detailId, reason.trim());
      detailModal.close();
      detailId = null;
      IRMS.notify("배합 기록을 완전히 삭제했습니다.", "success");
      await loadRecords();
    } catch (e) {
      IRMS.notify(`삭제 실패: ${e.message || e}`, "error");
    }
  });
  $("status-rec-export-all").addEventListener("click", () => {
    const q = new URLSearchParams();
    const map = {
      start_date: $("status-rec-from").value,
      end_date: $("status-rec-to").value,
      worker: $("status-rec-worker").value,
      search: $("status-rec-search").value.trim(),
    };
    Object.entries(map).forEach(([k, val]) => {
      if (val) q.set(k, val);
    });
    // 화면의 '취소 포함' 체크와 같이 취소 기록까지 내려받는다 — 정합.
    if ($("status-rec-canceled") && $("status-rec-canceled").checked) {
      q.set("include_canceled", "1");
    }
    window.location.assign(`/api/blend/records/export-all?${q.toString()}`);
  });

  // URL ?search= 지원 — 배합 분석의 자재 LOT 추적 등 다른 화면에서 특정 LOT/제품으로
  // 바로 필터된 기록을 열 수 있다(딥링크). 값을 검색칸에 채우고 초기 조회에 반영.
  const urlSearch = new URLSearchParams(window.location.search).get("search");
  if (urlSearch) $("status-rec-search").value = urlSearch;

  loadWorkers();
  loadProducts();
  loadRecords();
});
