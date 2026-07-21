/**
 * bom-editor module — 레시피 등록·수정용 세로 BOM 편집기.
 *
 * jspreadsheet 가로 확장 시트(spreadsheet-editor.js)를 대체한다(2026-07-16, item-code P5).
 * 자재가 열로 옆으로 넓어지던 구조를 "자재를 행으로 쌓는" 목록 편집기로 전환.
 * 서버 계약(raw_text TSV — 첫 줄 헤더/탭 구분)은 그대로: 이 모듈이 편집기 상태를
 * 기존과 동일한 TSV 로 직렬화/역직렬화하므로 검증·등록·수정등록·설명 왕복 전 경로 무변경.
 *
 * Factory: IRMS.management.createBomEditor(ctx)
 * Returns(spreadsheet-editor 와 호환 + 확장):
 *   { getSpreadsheetFactory, setRawInputMode, destroySpreadsheet,
 *     getActiveWorksheet, initSpreadsheet, getSpreadsheetDataAsText,
 *     loadFromTsvRows, addMaterialRow, addStepRow }
 *
 * 데이터 모델(단일 레시피 — 설계 §8.1): { productName, rows[{type:'material',name,value}
 * |{type:'step',note}], remark }. 다중 반제품 TSV(값행 2개 이상)는 편집기로 못 담으므로
 * raw textarea 폴백으로 안내(대량 이관용 파서의 다중 블록 지원은 서버에 그대로).
 *
 * ctx dependencies: dom.spreadsheetContainer, dom.rawInput,
 *   state.suppressDirtyTracking, ctx.onDirty
 */
(function () {
  "use strict";
  const IRMS = (window.IRMS = window.IRMS || {});
  IRMS.management = IRMS.management || {};

  // 파서(import_parser.py)와 동일한 헤더 토큰·정규화(normalize_token: strip+upper+영숫자만)
  const normTok = (v) => String(v || "").trim().toUpperCase().replace(/[^0-9A-Z가-힣]/g, "");
  const PRODUCT_TOKENS = new Set(["반제품명", "제품명", "레시피명", "PRODUCTNAME", "PRODUCT"].map(normTok));
  const STEP_TOKENS = new Set(["설명", "공정", "STEP"].map(normTok));
  const REMARK_TOKENS = new Set(["비고", "REMARK", "NOTE"].map(normTok));
  // 옛 형식의 위치/잉크명 열은 폐기 개념 — 붙여넣기에서 만나면 조용히 버린다
  const IGNORED_TOKENS = new Set(["위치", "POSITION", "잉크명", "INKNAME"].map(normTok));

  IRMS.management.createBomEditor = function (ctx) {
    const { dom, state } = ctx;
    const esc = IRMS.escapeHtml;

    // 편집기 상태(단일 레시피)
    const bom = { productName: "", rows: [], remark: "" };
    let materialNames = [];        // 자재명 자동완성 소스
    let materialCodes = {};        // 자재명 → 품목코드(materials.code) — 행 옆 코드 배지용
    let materialCodesLower = {};   // 소문자 키 자재명 → 품목코드(대소문자 무시 매칭용)

    // 코드 배지 조회 — 입력된 자재명(공백 제거)으로 코드를 찾는다.
    // 1) 정확명(trim) 매칭, 2) 소문자 키 매칭 — 사용자가 대소문자를 다르게 입력해도
    // 등록 자재면 코드 배지가 보이도록. datalist 옵션 라벨은 정확명을 그대로 쓴다.
    function codeFor(name) {
      const trimmed = String(name || "").trim();
      if (!trimmed) return "";
      if (Object.prototype.hasOwnProperty.call(materialCodes, trimmed)) {
        return materialCodes[trimmed] || "";
      }
      return materialCodesLower[trimmed.toLowerCase()] || "";
    }

    function dirty() {
      if (!state.suppressDirtyTracking) ctx.onDirty();
    }

    // ── spreadsheet-editor 호환 인터페이스 ──────────────────────
    function getSpreadsheetFactory() {
      return createBomEditorDom;  // 자체 렌더러 — 항상 사용 가능(vendor 의존 없음)
    }

    function setRawInputMode(enabled) {
      if (dom.spreadsheetContainer) {
        dom.spreadsheetContainer.style.display = enabled ? "none" : "";
      }
      if (dom.rawInput) {
        dom.rawInput.hidden = !enabled;
        dom.rawInput.disabled = !enabled;
      }
    }

    function destroySpreadsheet() {
      if (dom.spreadsheetContainer) dom.spreadsheetContainer.innerHTML = "";
    }

    function getActiveWorksheet() {
      return null;  // jspreadsheet 개념 없음 — 행/설명 추가는 addMaterialRow/addStepRow 사용
    }

    function initSpreadsheet(materials) {
      state.suppressDirtyTracking = true;
      rebuildMaterialIndex(materials);
      bom.productName = "";
      bom.remark = "";
      bom.rows = [emptyMaterial(), emptyMaterial(), emptyMaterial()];
      setRawInputMode(false);
      render();
      state.suppressDirtyTracking = false;
    }

    // 자재명/코드 색인 재구축 — initSpreadsheet 와 updateMaterialIndex 공용.
    // materialCodes(정확명) 와 materialCodesLower(소문자 키)를 함께 채운다.
    function rebuildMaterialIndex(materials) {
      materialNames = (materials || []).map((m) => m.name).filter(Boolean);
      materialCodes = {};
      materialCodesLower = {};
      (materials || []).forEach((m) => {
        if (m.name && m.code) {
          materialCodes[m.name] = m.code;
          materialCodesLower[String(m.name).toLowerCase()] = m.code;
        }
      });
    }

    // 자재명/코드 색인만 갱신 — 편집 중인 BOM(productName/rows/remark)은 건드리지 않는다.
    // 품목코드 탭에서 코드를 새로 부여/해제한 뒤, 사용자가 편집 중이던 내용을 잃지 않고
    // 배지·datalist 만 최신화하기 위해 쓴다. initSpreadsheet(상태 리셋)와 다르게
    // 입력 요소를 재생성하지 않고 포커스도 빼앗지 않는다.
    function updateMaterialIndex(materials) {
      rebuildMaterialIndex(materials);
      const c = dom.spreadsheetContainer;
      if (!c) return;
      // datalist 옵션만 다시 그린다 — 라벨은 정확명 그대로.
      const dl = c.querySelector("#bom-material-names");
      if (dl) {
        dl.innerHTML = materialNames
          .map((n) => `<option value="${esc(n)}"${materialCodes[n] ? ` label="${esc(materialCodes[n])}"` : ""}></option>`)
          .join("");
      }
      // 현재 행들의 이름으로 배지만 최신화(입력값·포커스는 그대로).
      c.querySelectorAll(".bom-row").forEach((rowEl) => {
        const name = rowEl.querySelector(".bom-name");
        const badge = rowEl.querySelector(".bom-code");
        if (name && badge) {
          badge.textContent = codeFor(name.value);
        }
      });
    }

    function emptyMaterial() {
      return { type: "material", name: "", value: "" };
    }

    // ── TSV 직렬화 — 기존 시트와 동일한 형식 산출(계약 경계) ────
    // 헤더: [반제품명, 자재명..., (설명)..., 비고?] / 값행: [제품명, 값..., 노트..., 비고?]
    function getSpreadsheetDataAsText() {
      if (dom.rawInput && !dom.rawInput.hidden) {
        return String(dom.rawInput.value || "").trim();  // raw 폴백 모드
      }
      const header = ["반제품명"];
      const values = [bom.productName.trim()];
      bom.rows.forEach((row) => {
        if (row.type === "material") {
          if (!row.name.trim()) return;  // 이름 없는 빈 행은 제외
          header.push(row.name.trim());
          values.push(String(row.value).trim());
        } else if (row.type === "step") {
          if (!row.note.trim()) return;
          header.push("설명");
          values.push(row.note.trim());
        }
      });
      if (bom.remark.trim()) {
        header.push("비고");
        values.push(bom.remark.trim());
      }
      if (header.length === 1 && !values[0]) return "";  // 완전 빈 편집기
      return header.join("\t") + "\n" + values.join("\t");
    }

    // ── TSV 역직렬화 — 수정 등록 로드 + 엑셀 붙여넣기 공용 ──────
    // 반환: true=편집기에 로드됨, false=다중 반제품 등으로 raw 폴백 필요
    function loadFromTsvRows(tsvRows, tsvText) {
      const rows = (tsvRows || []).filter((r) => r.some((c) => String(c || "").trim() !== ""));
      if (rows.length < 1) return false;
      const header = rows[0].map((c) => String(c || "").trim());
      const valueRows = rows.slice(1);
      if (valueRows.length !== 1) {
        // 다중 반제품(값행 2+) — 편집기는 단일 레시피 모드. raw 폴백으로 안내.
        if (dom.rawInput) dom.rawInput.value = tsvText || "";
        setRawInputMode(true);
        IRMS.notify(
          "여러 반제품이 든 표는 텍스트 모드로 열었습니다 — 한 레시피씩 편집하려면 한 줄만 붙여넣으세요.",
          "warn",
        );
        return false;
      }
      const vals = valueRows[0].map((c) => String(c || "").trim());
      state.suppressDirtyTracking = true;
      bom.productName = "";
      bom.remark = "";
      bom.rows = [];
      header.forEach((h, i) => {
        const v = vals[i] || "";
        if (!h) return;
        const tok = normTok(h);
        if (PRODUCT_TOKENS.has(tok)) {
          bom.productName = v;
        } else if (STEP_TOKENS.has(tok)) {
          bom.rows.push({ type: "step", note: v });
        } else if (REMARK_TOKENS.has(tok)) {
          bom.remark = v;
        } else if (IGNORED_TOKENS.has(tok)) {
          // 위치/잉크명 — 폐기 개념, 버림
        } else {
          bom.rows.push({ type: "material", name: h, value: v });
        }
      });
      if (!bom.rows.length) bom.rows.push(emptyMaterial());
      setRawInputMode(false);
      render();
      state.suppressDirtyTracking = false;
      return true;
    }

    // ── 행 조작 ─────────────────────────────────────────────────
    function addMaterialRow() {
      bom.rows.push(emptyMaterial());
      render();
      focusRow(bom.rows.length - 1, ".bom-name");
      dirty();
    }

    function addStepRow() {
      bom.rows.push({ type: "step", note: "" });
      render();
      focusRow(bom.rows.length - 1, ".bom-step-note");
      dirty();
    }

    function removeRow(idx) {
      bom.rows.splice(idx, 1);
      if (!bom.rows.length) bom.rows.push(emptyMaterial());
      render();
      dirty();
    }

    function moveRow(idx, delta) {
      const j = idx + delta;
      if (j < 0 || j >= bom.rows.length) return;
      const [row] = bom.rows.splice(idx, 1);
      bom.rows.splice(j, 0, row);
      render();
      dirty();
    }

    function focusRow(idx, selector) {
      const el = dom.spreadsheetContainer.querySelector(`[data-idx="${idx}"] ${selector}`);
      if (el) el.focus();
    }

    // ── 렌더 ────────────────────────────────────────────────────
    function createBomEditorDom() { /* 호환용 자리 — 실제 렌더는 render() */ }

    function render() {
      const c = dom.spreadsheetContainer;
      if (!c) return;
      const matNo = (i) => bom.rows.slice(0, i).filter((r) => r.type === "material").length + 1;
      const rowsHtml = bom.rows.map((row, i) => {
        const move =
          `<button type="button" class="bom-move" data-act="up" title="위로">▲</button>` +
          `<button type="button" class="bom-move" data-act="down" title="아래로">▼</button>`;
        if (row.type === "step") {
          return `<div class="bom-row bom-row-step" data-idx="${i}">
            <span class="bom-no">▸</span>
            <input class="input bom-step-note" value="${esc(row.note)}" placeholder="공정 설명 (예: 개시제 교반 - 300rpm)" />
            <span class="bom-tools">${move}<button type="button" class="bom-del" title="삭제">✕</button></span>
          </div>`;
        }
        // 품목코드 배지 — 등록된 자재(materials.code 보유)의 코드를 상시 표시.
        // 마스터에만 있는 자재는 '검증' 시 자동 등록 안내 메시지에서 코드가 확인된다.
        const code = codeFor(row.name);
        return `<div class="bom-row" data-idx="${i}">
          <span class="bom-no">${matNo(i)}</span>
          <input class="input bom-name" list="bom-material-names" value="${esc(row.name)}" placeholder="자재명" autocomplete="off" />
          <span class="bom-code" title="품목코드">${esc(code)}</span>
          <input class="input bom-value" value="${esc(row.value)}" placeholder="배합량(g)" inputmode="decimal" />
          <span class="bom-tools">${move}<button type="button" class="bom-del" title="삭제">✕</button></span>
        </div>`;
      }).join("");

      c.innerHTML = `
        <div class="bom-editor">
          <div class="bom-head">
            <label class="filter-label" for="bom-product">반제품명</label>
            <input class="input bom-product" id="bom-product" value="${esc(bom.productName)}"
              placeholder="예: NPS" autocomplete="off" />
          </div>
          <div class="bom-rows">${rowsHtml}</div>
          <div class="bom-foot">
            <label class="filter-label" for="bom-remark">비고</label>
            <input class="input bom-remark" id="bom-remark" value="${esc(bom.remark)}" placeholder="선택" />
          </div>
          <datalist id="bom-material-names">${materialNames
            .map((n) => `<option value="${esc(n)}"${materialCodes[n] ? ` label="${esc(materialCodes[n])}"` : ""}></option>`).join("")}</datalist>
        </div>`;
      bind(c);
    }

    function bind(c) {
      const product = c.querySelector(".bom-product");
      product.addEventListener("input", () => { bom.productName = product.value; dirty(); });
      const remark = c.querySelector(".bom-remark");
      remark.addEventListener("input", () => { bom.remark = remark.value; dirty(); });

      c.querySelectorAll(".bom-row").forEach((rowEl) => {
        const idx = Number(rowEl.dataset.idx);
        const name = rowEl.querySelector(".bom-name");
        if (name) name.addEventListener("input", () => {
          bom.rows[idx].name = name.value;
          // 이름이 등록 자재와 일치하면 코드 배지 즉시 갱신
          const badge = rowEl.querySelector(".bom-code");
          if (badge) badge.textContent = codeFor(name.value);
          dirty();
        });
        const value = rowEl.querySelector(".bom-value");
        if (value) {
          value.addEventListener("input", () => { bom.rows[idx].value = value.value; dirty(); });
          // Enter → 다음 자재행(마지막이면 새 행 추가) — 연속 입력 흐름
          value.addEventListener("keydown", (e) => {
            if (e.key !== "Enter" || e.isComposing) return;
            e.preventDefault();
            const next = bom.rows.findIndex((r, j) => j > idx && r.type === "material");
            if (next >= 0) focusRow(next, ".bom-name");
            else addMaterialRow();
          });
        }
        const note = rowEl.querySelector(".bom-step-note");
        if (note) note.addEventListener("input", () => { bom.rows[idx].note = note.value; dirty(); });
        rowEl.querySelector(".bom-del").addEventListener("click", () => removeRow(idx));
        rowEl.querySelectorAll(".bom-move").forEach((b) =>
          b.addEventListener("click", () => moveRow(idx, b.dataset.act === "up" ? -1 : 1))
        );
      });

      // 엑셀(TSV) 붙여넣기 — 탭이 든 다중 셀 붙여넣기만 가로채 표 전체로 해석.
      // 일반 텍스트 붙여넣기(자재명 하나 등)는 브라우저 기본 동작 유지.
      c.querySelector(".bom-editor").addEventListener("paste", (e) => {
        const text = (e.clipboardData || window.clipboardData).getData("text") || "";
        if (!text.includes("\t")) return;
        e.preventDefault();
        const rows = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n")
          .split("\n").map((line) => line.split("\t"));
        if (loadFromTsvRows(rows, text)) {
          IRMS.notify("붙여넣은 표를 자재 목록으로 불러왔습니다.", "success");
        }
        dirty();
      });
    }

    return {
      getSpreadsheetFactory,
      setRawInputMode,
      destroySpreadsheet,
      getActiveWorksheet,
      initSpreadsheet,
      updateMaterialIndex,
      getSpreadsheetDataAsText,
      loadFromTsvRows,
      addMaterialRow,
      addStepRow,
    };
  };
})();
