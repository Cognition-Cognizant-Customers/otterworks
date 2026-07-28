import { test, expect } from "@playwright/test";
import { registerUser, expectDashboard } from "./fixtures/test-helpers";

// OTD-15 margins dashboard (AC-08…AC-13). Uses a freshly registered user so
// every request flows through the real gateway JWT + analytics-service.
test.describe("Margins dashboard", () => {
  test.beforeEach(async ({ page }) => {
    await registerUser(page);
    await expectDashboard(page);
  });

  test("renders KPI tiles, charts, caption, badge and grid (AC-08/AC-13)", async ({ page }) => {
    await page.goto("/margins");
    await expect(page.getByRole("heading", { name: "Margins" })).toBeVisible();

    await expect(page.getByText("Gross Margin %")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("COGS / unit")).toBeVisible();
    await expect(page.getByText("Salmon Index")).toBeVisible();
    await expect(page.getByText("Freight Index")).toBeVisible();

    const caption = page.getByTestId("margins-caption");
    await expect(caption).toContainText(/Data as of \d{4}-\d{2}-\d{2}/);
    await expect(caption).toContainText("Source: Trading Economics (manual pull)");

    // AC-13: fresh stack default is synthetic (badge may read live after a
    // manual pull has been pushed to the shared stack)
    await expect(page.getByTestId("source-badge")).toHaveText(/synthetic|live/);

    await expect(page.getByTestId("commodity-chart")).toBeVisible();
    await expect(page.getByTestId("margin-chart")).toBeVisible();

    const rows = page.getByTestId("margins-grid").locator("tbody tr");
    expect(await rows.count()).toBeGreaterThanOrEqual(40);
  });

  test("grid sorting and filtering work (AC-09)", async ({ page }) => {
    await page.goto("/margins");
    const grid = page.getByTestId("margins-grid");
    await expect(grid.locator("tbody tr").first()).toBeVisible({ timeout: 15_000 });

    // Sort by Margin % ascending, then descending
    await page.getByRole("button", { name: "Margin %" }).click();
    const firstAsc = await grid.locator("tbody tr").first().locator("td").last().innerText();
    await page.getByRole("button", { name: "Margin %" }).click();
    const firstDesc = await grid.locator("tbody tr").first().locator("td").last().innerText();
    expect(parseFloat(firstDesc)).toBeGreaterThanOrEqual(parseFloat(firstAsc));

    // Text filter narrows rows
    const totalRows = await grid.locator("tbody tr").count();
    await page.getByTestId("grid-filter").fill("SLM-");
    const filteredRows = await grid.locator("tbody tr").count();
    expect(filteredRows).toBeLessThan(totalRows);
    expect(filteredRows).toBeGreaterThan(0);
    await page.getByTestId("grid-filter").fill("");

    // Category filter
    await page.getByTestId("category-filter").selectOption("Seafood");
    const categories = await grid.locator("tbody tr td:nth-child(3)").allInnerTexts();
    expect(categories.length).toBeGreaterThan(0);
    for (const c of categories) expect(c).toBe("Seafood");
  });

  test("Export CSV downloads backend-generated CSV (AC-10)", async ({ page }) => {
    await page.goto("/margins");
    await expect(page.getByTestId("margins-grid").locator("tbody tr").first()).toBeVisible({
      timeout: 15_000,
    });

    const downloadPromise = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export CSV" }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/sku-margins-.*\.csv/);

    const stream = await download.createReadStream();
    const chunks: Buffer[] = [];
    for await (const chunk of stream) chunks.push(chunk as Buffer);
    const csv = Buffer.concat(chunks).toString("utf-8");
    expect(csv.split("\n")[0]).toBe(
      "sku,name,category,supplier,list_price_usd,commodity_cost_usd,freight_cost_usd,overhead_cost_usd,cogs_usd,margin_pct"
    );
    expect(csv).toContain("SLM-001");
  });

  test("shows friendly error state when margins API fails (AC-11)", async ({ page }) => {
    await page.route("**/api/v1/analytics/margins", (route) =>
      route.fulfill({ status: 500, body: "boom" })
    );
    await page.goto("/margins");
    await expect(page.getByTestId("margins-error")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("Margins data is unavailable")).toBeVisible();
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  });

  test("sidebar navigation and direct URL work (AC-12)", async ({ page }) => {
    // Sidebar entry from dashboard
    await page.goto("/dashboard");
    await page.getByRole("link", { name: "Margins" }).click();
    await expect(page).toHaveURL(/\/margins$/);
    await expect(page.getByRole("heading", { name: "Margins" })).toBeVisible();

    // Existing routes still work
    await page.getByRole("link", { name: "Files", exact: true }).click();
    await expect(page).toHaveURL(/\/files$/);

    // Browser back returns to margins
    await page.goBack();
    await expect(page).toHaveURL(/\/margins$/);
    await expect(page.getByRole("heading", { name: "Margins" })).toBeVisible();
  });
});
