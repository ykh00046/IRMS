/**
 * api-spreadsheet.js — Spreadsheet product/sheet/column/row CRUD.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   ssListProducts, ssCreateProduct, ssUpdateProduct, ssDeleteProduct,
 *   ssLoadSheet, ssSaveSheet, ssAddColumn, ssDeleteColumn,
 *   ssAddRow, ssDeleteRow
 *
 * Side effects: none.
 * Dependencies: core.js (uses IRMS._core.request).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;

  async function ssListProducts() {
    const data = await request("/spreadsheet/products");
    return {
      items: (data.items || []).map((p) => ({
        id: p.id,
        name: p.name,
        description: p.description,
        recipeType: p.recipeType,
        columnCount: p.columnCount,
        rowCount: p.rowCount,
        updatedAt: p.updatedAt,
      })),
    };
  }

  async function ssCreateProduct(body) {
    return request("/spreadsheet/products", { method: "POST", body });
  }

  async function ssUpdateProduct(productId, body) {
    return request(`/spreadsheet/products/${productId}`, { method: "PATCH", body });
  }

  async function ssDeleteProduct(productId) {
    return request(`/spreadsheet/products/${productId}`, { method: "DELETE" });
  }

  async function ssLoadSheet(productId) {
    return request(`/spreadsheet/products/${productId}/sheet`);
  }

  async function ssSaveSheet(productId, rows) {
    return request(`/spreadsheet/products/${productId}/save`, { method: "POST", body: { rows } });
  }

  async function ssAddColumn(productId, body) {
    return request(`/spreadsheet/products/${productId}/columns`, { method: "POST", body });
  }

  async function ssDeleteColumn(columnId) {
    return request(`/spreadsheet/columns/${columnId}`, { method: "DELETE" });
  }

  async function ssAddRow(productId) {
    return request(`/spreadsheet/products/${productId}/rows`, { method: "POST" });
  }

  async function ssDeleteRow(rowId) {
    return request(`/spreadsheet/rows/${rowId}`, { method: "DELETE" });
  }

  Object.assign(IRMS, {
    ssListProducts,
    ssCreateProduct,
    ssUpdateProduct,
    ssDeleteProduct,
    ssLoadSheet,
    ssSaveSheet,
    ssAddColumn,
    ssDeleteColumn,
    ssAddRow,
    ssDeleteRow,
  });
})();
