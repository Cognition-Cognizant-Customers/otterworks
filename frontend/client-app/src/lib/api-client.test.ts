import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import type { AxiosRequestConfig, AxiosResponse } from "axios";
import { apiClient, API_BASE_URL } from "./api-client";

// The interceptors read window/localStorage lazily, so a plain stub is enough and
// the suite avoids pulling in a DOM implementation as a new dependency.
interface StubWindow {
  location: { href: string };
}

const tokenStore = new Map<string, string>();

const localStorageStub = {
  getItem: (key: string) => tokenStore.get(key) ?? null,
  setItem: (key: string, value: string) => void tokenStore.set(key, value),
  removeItem: (key: string) => void tokenStore.delete(key),
  clear: () => tokenStore.clear(),
};

/**
 * Replaces the transport underneath apiClient so a test can decide what the
 * "server" returns without any network or timing involved. Returns the configs
 * the client actually sent, which is what the request interceptor is judged on.
 */
function stubTransport(
  respond: (config: AxiosRequestConfig) => Promise<AxiosResponse> | AxiosResponse,
): AxiosRequestConfig[] {
  const sent: AxiosRequestConfig[] = [];
  apiClient.defaults.adapter = async (config) => {
    sent.push(config);
    return respond(config);
  };
  return sent;
}

function ok(data: unknown, config: AxiosRequestConfig): AxiosResponse {
  return {
    data,
    status: 200,
    statusText: "OK",
    headers: {},
    config: config as AxiosResponse["config"],
  };
}

function fail(status: number, config: AxiosRequestConfig) {
  return Promise.reject(
    Object.assign(new Error(`Request failed with status code ${status}`), {
      isAxiosError: true,
      config,
      response: { status, data: {}, statusText: "", headers: {}, config },
    }),
  );
}

let originalAdapter: unknown;

beforeEach(() => {
  originalAdapter = apiClient.defaults.adapter;
  tokenStore.clear();
  const stubWindow: StubWindow = { location: { href: "/" } };
  vi.stubGlobal("window", stubWindow);
  vi.stubGlobal("localStorage", localStorageStub);
});

afterEach(() => {
  apiClient.defaults.adapter = originalAdapter as never;
  vi.unstubAllGlobals();
  tokenStore.clear();
});

describe("apiClient base URL", () => {
  it("test_apiClient_webBuild_targetsTheSameOriginProxyPrefix", () => {
    // A same-origin prefix is what keeps the browser off a CORS preflight; an
    // absolute gateway URL leaking in here would break every request in prod.
    expect(API_BASE_URL).toBe("/api/v1");
  });
});

describe("apiClient request interceptor", () => {
  it("test_apiClient_storedAccessToken_isSentAsABearerHeader", async () => {
    localStorage.setItem("otter_access_token", "token-abc");
    const sent = stubTransport((config) => ok({}, config));

    await apiClient.get("/files");

    expect(sent[0].headers?.Authorization).toBe("Bearer token-abc");
  });

  it("test_apiClient_noStoredToken_sendsNoAuthorizationHeader", async () => {
    const sent = stubTransport((config) => ok({}, config));

    await apiClient.get("/files");

    expect(sent[0].headers?.Authorization).toBeUndefined();
  });

  it("test_apiClient_emptyStringToken_sendsNoAuthorizationHeader", async () => {
    // localStorage stores "" rather than removing the key when a login response
    // omits the token; an empty Bearer header would 401 with a confusing message.
    localStorage.setItem("otter_access_token", "");
    const sent = stubTransport((config) => ok({}, config));

    await apiClient.get("/files");

    expect(sent[0].headers?.Authorization).toBeUndefined();
  });

  it("test_apiClient_tokenRotatedBetweenRequests_usesTheLatestValue", async () => {
    localStorage.setItem("otter_access_token", "first");
    const sent = stubTransport((config) => ok({}, config));

    await apiClient.get("/files");
    localStorage.setItem("otter_access_token", "second");
    await apiClient.get("/files");

    expect(sent[0].headers?.Authorization).toBe("Bearer first");
    expect(sent[1].headers?.Authorization).toBe("Bearer second");
  });
});

describe("apiClient response key transform", () => {
  it("test_apiClient_snakeCaseResponseKeys_areCamelCasedForCallers", async () => {
    stubTransport((config) => ok({ user_id: "u1", display_name: "Ada" }, config));

    const { data } = await apiClient.get("/auth/profile");

    expect(data).toEqual({ userId: "u1", displayName: "Ada" });
  });

  it("test_apiClient_nestedAndArrayPayloads_areTransformedRecursively", async () => {
    stubTransport((config) =>
      ok({ page_info: { has_more: true }, items: [{ file_name: "a.txt" }] }, config),
    );

    const { data } = await apiClient.get("/files");

    expect(data).toEqual({ pageInfo: { hasMore: true }, items: [{ fileName: "a.txt" }] });
  });

  it("test_apiClient_alreadyCamelCaseKeys_areLeftAlone", async () => {
    stubTransport((config) => ok({ userId: "u1", createdAt: "2026-01-01" }, config));

    const { data } = await apiClient.get("/auth/profile");

    expect(data).toEqual({ userId: "u1", createdAt: "2026-01-01" });
  });

  it("test_apiClient_screamingSnakeCaseKeys_areLeftAlone", async () => {
    // The regex only matches _ followed by a lowercase letter, so enum-style keys
    // survive verbatim. Worth pinning: a backend renaming FILE_TYPE to file_type
    // silently changes the shape the UI receives.
    stubTransport((config) => ok({ FILE_TYPE: "pdf" }, config));

    const { data } = await apiClient.get("/files");

    expect(data).toEqual({ FILE_TYPE: "pdf" });
  });

  it("test_apiClient_keyWithALeadingUnderscore_losesTheUnderscoreAndIsCapitalised", async () => {
    // A trailing underscore has no following letter so it survives, but a leading
    // one matches the regex and becomes a capital: "_leading" -> "Leading". Any
    // backend field prefixed with _ therefore arrives under a different name.
    stubTransport((config) => ok({ weird_: 1, _leading: 2 }, config));

    const { data } = await apiClient.get("/files");

    expect(data).toEqual({ weird_: 1, Leading: 2 });
  });

  it("test_apiClient_nullAndEmptyValues_surviveTheTransform", async () => {
    // null is typeof "object", so a naive recursion would turn it into {}.
    stubTransport((config) =>
      ok({ deleted_at: null, tag_list: [], note_text: "" }, config),
    );

    const { data } = await apiClient.get("/files");

    expect(data).toEqual({ deletedAt: null, tagList: [], noteText: "" });
  });

  it("test_apiClient_emptyResponseBody_isPassedThroughUntouched", async () => {
    stubTransport((config) => ok("", config));

    const { data } = await apiClient.get("/files");

    expect(data).toBe("");
  });

  it("test_apiClient_topLevelArrayResponse_isTransformedElementwise", async () => {
    stubTransport((config) => ok([{ file_id: "1" }, { file_id: "2" }], config));

    const { data } = await apiClient.get("/files");

    expect(data).toEqual([{ fileId: "1" }, { fileId: "2" }]);
  });
});

describe("apiClient 401 handling", () => {
  it("test_apiClient_401OnAnAuthEndpoint_doesNotClearTheSession", async () => {
    // A failed login must surface as a rejected promise the form can render, not
    // as a redirect that throws the user's half-typed credentials away.
    localStorage.setItem("otter_access_token", "token-abc");
    stubTransport((config) => fail(401, config));

    await expect(apiClient.post("/auth/login", {})).rejects.toThrow();

    expect(localStorage.getItem("otter_access_token")).toBe("token-abc");
    expect((globalThis.window as unknown as StubWindow).location.href).toBe("/");
  });

  it("test_apiClient_401OnAResourceWithAStillValidToken_keepsTheSession", async () => {
    // The gateway 401s a request the user is simply not entitled to make. The
    // profile probe succeeds, so the session is intact and must not be dropped.
    localStorage.setItem("otter_access_token", "token-abc");
    localStorage.setItem("otter_refresh_token", "refresh-abc");
    stubTransport((config) =>
      String(config.url).includes("/auth/profile") ? ok({}, config) : fail(401, config),
    );

    await expect(apiClient.get("/files/someone-elses")).rejects.toThrow();

    expect(localStorage.getItem("otter_access_token")).toBe("token-abc");
    expect(localStorage.getItem("otter_refresh_token")).toBe("refresh-abc");
  });

  it("test_apiClient_401OnAResourceWithARejectedToken_stillRejectsTheCaller", async () => {
    localStorage.setItem("otter_access_token", "token-abc");
    stubTransport((config) => fail(401, config));

    await expect(apiClient.get("/files")).rejects.toThrow();
  });

  it("test_apiClient_403Response_isPassedStraightThrough", async () => {
    localStorage.setItem("otter_access_token", "token-abc");
    stubTransport((config) => fail(403, config));

    await expect(apiClient.get("/files")).rejects.toThrow();

    expect(localStorage.getItem("otter_access_token")).toBe("token-abc");
  });

  it("test_apiClient_500Response_isPassedStraightThrough", async () => {
    localStorage.setItem("otter_access_token", "token-abc");
    stubTransport((config) => fail(500, config));

    await expect(apiClient.get("/files")).rejects.toThrow();

    expect(localStorage.getItem("otter_access_token")).toBe("token-abc");
  });

  it("test_apiClient_networkErrorWithNoResponse_isPassedStraightThrough", async () => {
    localStorage.setItem("otter_access_token", "token-abc");
    apiClient.defaults.adapter = async () => {
      throw Object.assign(new Error("Network Error"), { isAxiosError: true });
    };

    await expect(apiClient.get("/files")).rejects.toThrow("Network Error");

    expect(localStorage.getItem("otter_access_token")).toBe("token-abc");
  });
});
