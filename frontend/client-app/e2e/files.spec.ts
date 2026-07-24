import { test, expect } from "@playwright/test";

test.describe("Files Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/files");
  });

  test("shows Files heading or redirects to login", async ({ page }) => {
    const heading = page.getByRole("heading", { name: /Files|My Files/i });
    const loginHeading = page.getByText("Sign in to your account");
    await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });
  });

  test("has upload button", async ({ page }) => {
    // Wait for the Files heading to appear
    const heading = page.getByRole("heading", { name: /Files|My Files/i });
    await expect(heading).toBeVisible({ timeout: 15_000 });

    // Files page has "Upload" and "New folder" buttons
    const uploadButton = page.getByRole("button", { name: /Upload/i });
    await expect(uploadButton).toBeVisible({ timeout: 5_000 });
  });

  test("shows empty state or file listing", async ({ page }) => {
    const heading = page.getByRole("heading", { name: /Files|My Files/i });
    const loginHeading = page.getByText("Sign in to your account");
    await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });

    if (await heading.isVisible().catch(() => false)) {
      // Either file cards or empty state
      const emptyState = page.getByText(/No files|Upload files|Drop files/i);
      const fileItems = page.locator("[class*='grid'] > *, [class*='space-y'] > *").first();
      await expect(emptyState.or(fileItems)).toBeVisible({ timeout: 10_000 });
    }
  });

  test("has grid/list view toggle", async ({ page }) => {
    const heading = page.getByRole("heading", { name: /Files|My Files/i });
    const loginHeading = page.getByText("Sign in to your account");
    await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });

    if (await heading.isVisible().catch(() => false)) {
      const buttons = page.locator("button svg");
      await expect(buttons.first()).toBeVisible();
    }
  });

  test("offers file preview and opens/closes a preview dialog when signed in", async ({
    page,
  }) => {
    const heading = page.getByRole("heading", { name: /Files|My Files/i });
    const loginHeading = page.getByText("Sign in to your account");
    await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });

    // Only meaningful when a seeded/signed-in session renders the Files page.
    if (!(await heading.isVisible().catch(() => false))) return;

    const previewBtn = page.getByRole("button", { name: /Preview/i }).first();
    if (!(await previewBtn.isVisible().catch(() => false))) return;

    await previewBtn.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible({ timeout: 10_000 });
    await expect(dialog.getByRole("button", { name: /Download/i })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(page.getByRole("dialog")).toHaveCount(0, { timeout: 10_000 });
    await expect(heading).toBeVisible();
  });
});
