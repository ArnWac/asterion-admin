// @vitest-environment jsdom
//
// DOM/browser-API coverage for api.js (Review R11). The pure logic in
// logic.js is covered by logic.test.js on the node environment; this file
// uses jsdom so the localStorage-backed token store and the envelope-parsing
// APIError can be exercised the way the browser uses them. fetch() itself is
// not driven here — only the synchronous, browser-API-touching surface.

import { afterEach, describe, expect, it } from "vitest";

import { APIError, tokenStore } from "../../asterion/ui/static/admin/api.js";

afterEach(() => {
  localStorage.clear();
});

describe("tokenStore", () => {
  it("round-trips the access token and reports login state", () => {
    expect(tokenStore.isLoggedIn()).toBe(false);
    tokenStore.set("abc.def.ghi");
    expect(tokenStore.get()).toBe("abc.def.ghi");
    expect(tokenStore.isLoggedIn()).toBe(true);
  });

  it("clear() drops both access and refresh tokens", () => {
    tokenStore.set("access-tok");
    tokenStore.setRefresh("refresh-tok");
    expect(tokenStore.getRefresh()).toBe("refresh-tok");

    tokenStore.clear();
    expect(tokenStore.get()).toBe(null);
    expect(tokenStore.getRefresh()).toBe(null);
    expect(tokenStore.isLoggedIn()).toBe(false);
  });
});

describe("APIError", () => {
  it("parses the asterion error envelope", () => {
    const err = new APIError(422, {
      error: {
        code: "validation_error",
        message: "Payload contains non-writable fields.",
        fields: [{ name: "password", message: "Invalid field." }],
        request_id: "8e1f",
      },
    });
    expect(err.status).toBe(422);
    expect(err.code).toBe("validation_error");
    expect(err.message).toBe("Payload contains non-writable fields.");
    expect(err.requestId).toBe("8e1f");
    expect(err.fieldErrors()).toEqual({ password: "Invalid field." });
  });

  it("falls back sensibly when no envelope is present", () => {
    const err = new APIError(500, null);
    expect(err.status).toBe(500);
    expect(err.code).toBe("http_500");
    expect(err.message).toBe("HTTP 500");
    expect(err.fields).toEqual([]);
    expect(err.fieldErrors()).toEqual({});
  });
});
