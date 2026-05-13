/**
 * core.js — HTTP·CSRF core and IRMS namespace bootstrap.
 *
 * Split from static/js/common.js during the split-common-js PDCA cycle
 * (2026-05). See docs/01-plan/features/split-common-js.plan.md.
 *
 * Internal namespace (IRMS._core):
 *   request, getCsrfToken, safeNextUrl, detailToText
 *
 * Side effects: window.IRMS = window.IRMS || {} (initializes namespace).
 * Dependencies: none (must load first).
 */
(function () {
  "use strict";

  const IRMS = window.IRMS = window.IRMS || {};

  function getCsrfToken() {
    const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function safeNextUrl(value, fallback) {
    const text = String(value || "").trim();
    if (
      !text.startsWith("/") ||
      text.startsWith("//") ||
      text.includes("\\") ||
      /[\u0000-\u001f]/.test(text)
    ) {
      return fallback;
    }
    return text;
  }

  function detailToText(value) {
    if (Array.isArray(value)) {
      return value.map(detailToText).filter(Boolean).join("\n");
    }
    if (value && typeof value === "object") {
      if (value.message) return String(value.message);
      if (value.msg) return String(value.msg);
      if (value.detail) return detailToText(value.detail);
      try {
        return JSON.stringify(value);
      } catch (_error) {
        return String(value);
      }
    }
    return value === undefined || value === null ? "" : String(value);
  }

  async function request(path, options) {
    const method = options?.method || "GET";
    const query = options?.query || null;
    const body = options?.body || null;
    const responseType = options?.responseType || "json";

    const endpoint = new URL(`/api${path}`, window.location.origin);
    if (query) {
      Object.entries(query).forEach(([key, value]) => {
        if (value === undefined || value === null || value === "") {
          return;
        }
        endpoint.searchParams.set(key, String(value));
      });
    }

    const headers = body ? { "Content-Type": "application/json" } : {};
    const isUnsafe = !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
    if (isUnsafe) {
      const token = getCsrfToken();
      if (token) headers["x-csrftoken"] = token;
    }

    const response = await fetch(endpoint, {
      method,
      credentials: "same-origin",
      headers: Object.keys(headers).length ? headers : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });

    if (!response.ok) {
      let payload = null;
      let detailText = "";
      const contentType = response.headers.get("content-type") || "";
      try {
        if (!contentType.includes("application/json")) {
          detailText = await response.text();
          payload = { detail: detailText || response.statusText };
        } else {
          payload = await response.json();
        }
      } catch (_error) {
        payload = { detail: response.statusText };
      }
      const detail = detailToText(
        payload?.detail?.message !== undefined
          ? payload.detail.message
          : payload?.detail !== undefined
            ? payload.detail
            : payload?.message !== undefined
              ? payload.message
              : `Request failed (${response.status})`,
      );
      const isCsrfFailure =
        response.status === 403 &&
        String(detail).toLowerCase().includes("csrf");
      if (isCsrfFailure) {
        document.cookie = "csrftoken=; Max-Age=0; path=/; SameSite=Lax";
        if (typeof window !== "undefined") {
          window.location.reload();
        }
      }
      if (
        (response.status === 401 || response.status === 403) &&
        !isCsrfFailure &&
        typeof window !== "undefined" &&
        !window.location.pathname.startsWith("/management/login") &&
        !window.location.pathname.startsWith("/weighing/select") &&
        !window.location.pathname.startsWith("/login")
      ) {
        const next = `${window.location.pathname}${window.location.search}`;
        const isManagementPath =
          window.location.pathname.startsWith("/management") ||
          window.location.pathname.startsWith("/insight") ||
          window.location.pathname.startsWith("/status") ||
          window.location.pathname.startsWith("/base") ||
          window.location.pathname.startsWith("/admin");
        const target = isManagementPath ? "/management/login" : "/weighing/select";
        window.location.assign(`${target}?next=${encodeURIComponent(next)}`);
      }
      throw new Error(String(detail));
    }

    if (responseType === "blob") {
      return response.blob();
    }
    if (responseType === "text") {
      return response.text();
    }
    return response.json();
  }

  IRMS._core = { request, getCsrfToken, safeNextUrl, detailToText };
})();
