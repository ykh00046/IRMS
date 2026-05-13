/**
 * api-users.js — Authentication, users, audit logs.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05).
 *
 * Exports (window.IRMS.*):
 *   login, loginManager, loginOperator, logout, getCurrentUser,
 *   listUsers, createUser, updateUser, resetUserPassword, deleteUser,
 *   listAuditLogs
 *
 * Side effects: none.
 * Dependencies: core.js, mappers.js.
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};
  const { request } = IRMS._core;
  const { mapUser, mapAdminUser, mapAuditLog } = IRMS._mappers;

  async function login(username, password) {
    const payload = await request("/auth/login", {
      method: "POST",
      body: { username, password },
    });
    return mapUser(payload.user);
  }

  async function loginManager(username, password) {
    const payload = await request("/auth/management-login", {
      method: "POST",
      body: { username, password },
    });
    return mapUser(payload.user);
  }

  async function loginOperator(username, password) {
    const payload = await request("/auth/operator-login", {
      method: "POST",
      body: { username, password },
    });
    return mapUser(payload.user);
  }

  async function logout() {
    return request("/auth/logout", { method: "POST" });
  }

  async function getCurrentUser() {
    const payload = await request("/auth/me");
    return mapUser(payload.user);
  }

  async function listUsers() {
    const payload = await request("/admin/users");
    return {
      items: (payload.items || []).map(mapAdminUser),
      summary: payload.summary || {},
      total: Number(payload.total || 0),
    };
  }

  async function createUser(user) {
    const payload = await request("/admin/users", {
      method: "POST",
      body: {
        username: user.username,
        display_name: user.displayName,
        access_level: user.accessLevel,
        password: user.password,
      },
    });
    return mapAdminUser(payload.user);
  }

  async function updateUser(userId, user) {
    const payload = await request(`/admin/users/${userId}`, {
      method: "PATCH",
      body: {
        display_name: user.displayName,
        access_level: user.accessLevel,
        is_active: user.isActive,
      },
    });
    return mapAdminUser(payload.user);
  }

  async function resetUserPassword(userId, password) {
    return request(`/admin/users/${userId}/password`, {
      method: "POST",
      body: { password },
    });
  }

  async function deleteUser(userId) {
    return request(`/admin/users/${userId}`, { method: "DELETE" });
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
    login,
    loginManager,
    loginOperator,
    logout,
    getCurrentUser,
    listUsers,
    createUser,
    updateUser,
    resetUserPassword,
    deleteUser,
    listAuditLogs,
  });
})();
