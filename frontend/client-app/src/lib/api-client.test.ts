import { http, HttpResponse } from "msw";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { apiClient } from "./api-client";
import { billingServer } from "../test-setup";

describe("apiClient interceptors", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    localStorage.clear();
  });

  it("injects the bearer token and recursively camel-cases response keys", async () => {
    localStorage.setItem("otter_access_token", "secret-token");
    let authorization = "";
    billingServer.use(
      http.get("http://localhost:3000/api/v1/profile", ({ request }) => {
        authorization = request.headers.get("authorization") ?? "";
        return HttpResponse.json({
          user_name: "Otter",
          nested_value: { file_count: 2 },
        });
      }),
    );

    const response = await apiClient.get("/profile");

    expect(authorization).toBe("Bearer secret-token");
    expect(response.data).toEqual({
      userName: "Otter",
      nestedValue: { fileCount: 2 },
    });
  });

  it("verifies a non-auth 401 without clearing tokens when the profile remains valid", async () => {
    localStorage.setItem("otter_access_token", "access-token");
    localStorage.setItem("otter_refresh_token", "refresh-token");
    let profileChecks = 0;
    billingServer.use(
      http.get("http://localhost:3000/api/v1/files", () =>
        HttpResponse.json({ message: "expired request" }, { status: 401 }),
      ),
      http.get("http://localhost:3000/api/v1/auth/profile", () => {
        profileChecks += 1;
        return HttpResponse.json({ id: "user-1" });
      }),
    );

    await expect(apiClient.get("/files")).rejects.toMatchObject({
      response: { status: 401 },
    });
    expect(profileChecks).toBe(1);
    expect(localStorage.getItem("otter_access_token")).toBe("access-token");
  });

  it("clears both tokens when the profile verification also returns 401", async () => {
    localStorage.setItem("otter_access_token", "access-token");
    localStorage.setItem("otter_refresh_token", "refresh-token");
    billingServer.use(
      http.get("http://localhost:3000/api/v1/files", () =>
        HttpResponse.json({ message: "expired request" }, { status: 401 }),
      ),
      http.get("http://localhost:3000/api/v1/auth/profile", () =>
        HttpResponse.json({ message: "expired session" }, { status: 401 }),
      ),
    );

    await expect(apiClient.get("/files")).rejects.toMatchObject({
      response: { status: 401 },
    });
    expect(localStorage.getItem("otter_access_token")).toBeNull();
    expect(localStorage.getItem("otter_refresh_token")).toBeNull();
  });
});
