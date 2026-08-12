import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it } from "vitest";
import { billingServer } from "../test-setup";
import { searchApi, starredApi, storageApi } from "./api";

describe("API adapters and local persistence", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("maps search wire fields and calculates pagination", async () => {
    billingServer.use(
      http.get("http://localhost:3000/api/v1/search", ({ request }) => {
        const url = new URL(request.url);
        expect(url.searchParams.get("q")).toBe("otter");
        expect(url.searchParams.get("type")).toBe("file");
        return HttpResponse.json({
          results: [
            {
              id: "file-1",
              type: "file",
              title: "Otter guide",
              content_snippet: "<em>otter</em> care",
              updated_at: "2026-03-01T12:00:00Z",
            },
          ],
          total: 21,
          page: 2,
          page_size: 10,
        });
      }),
    );

    await expect(searchApi.search({ query: "otter", type: "file" })).resolves.toEqual({
      data: [
        {
          id: "file-1",
          type: "file",
          name: "Otter guide",
          snippet: "<em>otter</em> care",
          path: "",
          updatedAt: "2026-03-01T12:00:00Z",
          ownerName: "",
        },
      ],
      total: 21,
      page: 2,
      pageSize: 10,
      hasMore: true,
    });
  });

  it("tracks starred items per user and separates item types", () => {
    expect(starredApi.toggle("user-a", "file-1", "file")).toBe(true);
    expect(starredApi.toggle("user-a", "folder-1", "folder")).toBe(true);
    expect(starredApi.toggle("user-b", "file-2", "file")).toBe(true);
    expect(starredApi.isStarred("user-a", "file-1")).toBe(true);
    expect(starredApi.getStarredIds("user-a")).toEqual({
      fileIds: ["file-1"],
      folderIds: ["folder-1"],
      documentIds: [],
    });
    expect(starredApi.toggle("user-a", "file-1", "file")).toBe(false);
    expect(starredApi.isStarred("user-a", "file-1")).toBe(false);
  });

  it("paginates files while aggregating storage usage", async () => {
    billingServer.use(
      http.get("http://localhost:3000/api/v1/files", ({ request }) => {
        const url = new URL(request.url);
        const page = url.searchParams.get("page");
        if (page === "1" && url.searchParams.get("page_size") === "1") {
          return HttpResponse.json({ files: [], total: 101 });
        }
        return page === "1"
          ? HttpResponse.json({
              files: [
                { size_bytes: 350 },
                ...Array.from({ length: 99 }, () => ({ size_bytes: 0 })),
              ],
            })
          : HttpResponse.json({ files: [{ size_bytes: 650 }] });
      }),
      http.get("http://localhost:3000/api/v1/documents", () =>
        HttpResponse.json({ total: 4 }),
      ),
    );

    await expect(storageApi.getUsage()).resolves.toEqual({
      used: 1000,
      total: 10 * 1024 * 1024 * 1024,
      fileCount: 101,
      documentCount: 4,
    });
  });
});
