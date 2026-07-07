/**
 * api-users.js — Authentication + audit logs.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). 2026-07-07: users CRUD 화면이 workers 명단으로 대체되면서
 * login/loginOperator/getCurrentUser/listUsers/createUser/updateUser/
 * resetUserPassword/deleteUser 데드코드 제거.
 *
 * Exports (window.IRMS.*):
 *   loginManager, logout, listAuditLogs
 *
 * Side effects: none.
 * Dependencies: core.js, mappers.js.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;
  const { mapUser, mapAuditLog } = IRMS._mappers;

  async function loginManager(username, password) {
    const payload = await request("/auth/management-login", {
      method: "POST",
      body: { username, password },
    });
    return mapUser(payload.user);
  }

  async function logout() {
    return request("/auth/logout", { method: "POST" });
  }

  async function listAuditLogs(filters) {
    const payload = await request("/admin/audit-logs", {
      query: {
        limit: filters?.limit,
        action: filters?.action,
      },
    });
    return {
      items: (payload.items || []).map(mapAuditLog),
      total: Number(payload.total || 0),
    };
  }

  Object.assign(IRMS, {
    loginManager,
    logout,
    listAuditLogs,
  });
})();
