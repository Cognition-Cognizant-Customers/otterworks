import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { QuotasPage, formatBytes } from "./QuotasPage";

const GB = 1024 * 1024 * 1024;

const rawUsers = [
  {
    id: "u1",
    email: "alice@otterworks.dev",
    display_name: "Alice",
    role: "admin",
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    storage_quota: { used_bytes: 4.8 * GB, quota_bytes: 5 * GB },
  },
  {
    id: "u2",
    email: "bob@otterworks.dev",
    display_name: "Bob",
    role: "viewer",
    status: "active",
    created_at: "2026-01-01T00:00:00Z",
    storage_quota: { used_bytes: 1 * GB, quota_bytes: 10 * GB },
  },
];

describe("QuotasPage", () => {
  it("renders the quota table on success", async () => {
    server.use(
      http.get("/api/v1/admin/users", () => HttpResponse.json({ users: rawUsers }))
    );

    renderWithProviders(<QuotasPage />);

    expect(screen.getByRole("progressbar", { name: "Loading" })).toBeInTheDocument();
    // Empty-state copy must not render while the first request is in flight
    expect(screen.queryByText("No users found")).not.toBeInTheDocument();

    expect(await screen.findByText("Alice")).toBeInTheDocument();
    expect(screen.getByText("alice@otterworks.dev")).toBeInTheDocument();
    const aliceRow = screen.getByText("Alice").closest("tr") as HTMLElement;
    const aliceCells = within(aliceRow).getAllByRole("cell");
    expect(aliceCells[1]).toHaveTextContent("4.8 GB");
    expect(aliceCells[2]).toHaveTextContent("5 GB");
    expect(screen.getByText("96%")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    // Column headers
    for (const col of ["User", "Used", "Quota", "Usage", "Update Quota"]) {
      expect(
        screen.getAllByRole("columnheader", { name: new RegExp(`^${col}`) }).length
      ).toBeGreaterThan(0);
    }
    // Paginator
    expect(screen.getByLabelText("Items per page:")).toBeInTheDocument();
    expect(screen.getByText("1 – 2 of 2")).toBeInTheDocument();
  });

  it("shows the empty state only after loading finishes", async () => {
    server.use(http.get("/api/v1/admin/users", () => HttpResponse.json({ users: [] })));

    renderWithProviders(<QuotasPage />);

    expect(screen.queryByText("No users found")).not.toBeInTheDocument();
    expect(await screen.findByText("No users found")).toBeInTheDocument();
  });

  it("shows an error state with retry when the users request fails", async () => {
    server.use(
      http.get("/api/v1/admin/users", () =>
        HttpResponse.json({ error: "boom" }, { status: 500 })
      )
    );

    renderWithProviders(<QuotasPage />);

    expect(await screen.findByText("Failed to load users")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("No users found")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Loading" })).not.toBeInTheDocument();
  });

  it("filters users with the labeled search field", async () => {
    server.use(
      http.get("/api/v1/admin/users", () => HttpResponse.json({ users: rawUsers }))
    );

    renderWithProviders(<QuotasPage />);
    await screen.findByText("Alice");

    await userEvent.type(screen.getByLabelText("Search users"), "bob");
    expect(screen.getByText("Bob")).toBeInTheDocument();
    expect(screen.queryByText("Alice")).not.toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText("Search users"));
    await userEvent.type(screen.getByLabelText("Search users"), "nobody");
    expect(screen.getByText("No users match your search")).toBeInTheDocument();
  });

  it("updates a quota via the per-row select and shows a success toast", async () => {
    server.use(
      http.get("/api/v1/admin/users", () => HttpResponse.json({ users: rawUsers })),
      http.put("/api/v1/admin/quotas/u1", () =>
        HttpResponse.json({ used_bytes: 4.8 * GB, quota_bytes: 20 * GB })
      )
    );

    renderWithProviders(<QuotasPage />);
    await screen.findByText("Alice");

    const select = screen.getByLabelText("Update quota for Alice");
    await userEvent.selectOptions(select, String(20 * GB));

    expect(await screen.findByText("Quota updated for Alice")).toBeInTheDocument();
    const aliceRow = screen.getByText("Alice").closest("tr") as HTMLElement;
    expect(within(aliceRow).getAllByRole("cell")[2]).toHaveTextContent("20 GB");
  });

  it("shows an error toast when a quota update fails", async () => {
    server.use(
      http.get("/api/v1/admin/users", () => HttpResponse.json({ users: rawUsers })),
      http.put("/api/v1/admin/quotas/u1", () =>
        HttpResponse.json({ error: "nope" }, { status: 500 })
      )
    );

    renderWithProviders(<QuotasPage />);
    await screen.findByText("Alice");

    await userEvent.selectOptions(
      screen.getByLabelText("Update quota for Alice"),
      String(20 * GB)
    );

    expect(await screen.findByText("Failed to update quota for Alice")).toBeInTheDocument();
  });

  it("sorts and paginates", async () => {
    const many = Array.from({ length: 7 }, (_, i) => ({
      id: `u${i}`,
      email: `user${i}@otterworks.dev`,
      display_name: `User ${i}`,
      role: "viewer",
      status: "active",
      created_at: "2026-01-01T00:00:00Z",
      storage_quota: { used_bytes: i * GB, quota_bytes: 10 * GB },
    }));
    server.use(
      http.get("/api/v1/admin/users", () => HttpResponse.json({ users: many }))
    );

    renderWithProviders(<QuotasPage />);
    await screen.findByText("User 0");

    // Default page size 5 → 5 rows shown
    expect(screen.getByText("1 – 5 of 7")).toBeInTheDocument();
    expect(screen.queryByText("User 6")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText("6 – 7 of 7")).toBeInTheDocument();
    expect(screen.getByText("User 6")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "First page" }));
    // Sort by Used descending
    const usedHeader = screen.getByRole("button", { name: /^Used/ });
    await userEvent.click(usedHeader);
    await userEvent.click(usedHeader);
    const firstDataRow = screen.getAllByRole("row")[1];
    expect(within(firstDataRow).getByText("User 6")).toBeInTheDocument();
  });
});

describe("formatBytes", () => {
  it("matches the Angular formatting", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1024)).toBe("1 KB");
    expect(formatBytes(1.5 * GB)).toBe("1.5 GB");
    expect(formatBytes(5 * GB)).toBe("5 GB");
  });
});
