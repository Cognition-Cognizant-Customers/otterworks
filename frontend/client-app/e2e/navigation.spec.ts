import { test, expect } from "@playwright/test";

test.describe("Navigation & Routing", () => {
  test("landing page loads at /", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { name: "OtterWorks", exact: true })
    ).toBeVisible();
  });

  test("/login loads the login page", async ({ page }) => {
    await page.goto("/login");
    await expect(page.getByText("Sign in to your account")).toBeVisible();
  });

  test("/register loads the registration page", async ({ page }) => {
    await page.goto("/register");
    await expect(page.getByText("Create your account")).toBeVisible();
  });

  test("/dashboard loads or redirects to login", async ({ page }) => {
    await page.goto("/dashboard");
    const dashboard = page.getByRole("heading", { name: "Dashboard" });
    const login = page.getByText("Sign in to your account");
    await expect(dashboard.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/documents loads or redirects to login", async ({ page }) => {
    await page.goto("/documents");
    const docs = page.getByRole("heading", { name: "Documents" });
    const login = page.getByText("Sign in to your account");
    await expect(docs.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/files loads or redirects to login", async ({ page }) => {
    await page.goto("/files");
    const files = page.getByRole("heading", { name: /Files|My Files/i });
    const login = page.getByText("Sign in to your account");
    await expect(files.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/search loads or redirects to login", async ({ page }) => {
    await page.goto("/search");
    const search = page.getByRole("heading", { name: "Search", exact: true });
    const login = page.getByText("Sign in to your account");
    await expect(search.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/settings loads or redirects to login", async ({ page }) => {
    await page.goto("/settings");
    const settings = page.getByRole("heading", { name: "Settings" });
    const login = page.getByText("Sign in to your account");
    await expect(settings.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/notifications loads or redirects to login", async ({ page }) => {
    await page.goto("/notifications");
    const notifications = page.getByRole("heading", { name: /Notification/i });
    const login = page.getByText("Sign in to your account");
    await expect(notifications.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/shared loads or redirects to login", async ({ page }) => {
    await page.goto("/shared");
    const shared = page.getByRole("heading", { name: /Shared/i });
    const login = page.getByText("Sign in to your account");
    await expect(shared.or(login)).toBeVisible({ timeout: 10_000 });
  });

  test("/trash loads or redirects to login", async ({ page }) => {
    await page.goto("/trash");
    const trash = page.getByRole("heading", { name: /Trash/i });
    const login = page.getByText("Sign in to your account");
    await expect(trash.or(login)).toBeVisible({ timeout: 10_000 });
  });
});
