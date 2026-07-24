import { test, expect, type Page } from "@playwright/test";
import { registerUser, clearAuth } from "./fixtures/test-helpers";

// Uploads a file through the real API (gateway -> file-service -> S3) using the
// logged-in user's token, then returns the created file id.
async function uploadFile(
  page: Page,
  name: string,
  mime: string,
  content: Buffer | string
): Promise<string> {
  const token = await page.evaluate(() =>
    localStorage.getItem("otter_access_token")
  );
  const ownerId = JSON.parse(
    Buffer.from(token!.split(".")[1], "base64").toString()
  ).sub as string;
  const res = await page.request.post("/api/v1/files/upload", {
    headers: { Authorization: `Bearer ${token}` },
    multipart: {
      file: {
        name,
        mimeType: mime,
        buffer: typeof content === "string" ? Buffer.from(content) : content,
      },
      owner_id: ownerId,
    },
  });
  expect(res.ok()).toBeTruthy();
  const body = await res.json();
  return body.file.id;
}

test.describe("File Preview (OTD-12)", () => {
  test.beforeEach(async ({ page }) => {
    await registerUser(page);
    await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
  });

  // AC-01/BDD-01 + AC-04/BDD-04: preview reachable from list, text renders
  test("opens a text preview from the file list without downloading", async ({
    page,
  }) => {
    await uploadFile(page, "notes.md", "text/markdown", "# Hello\npreview line two");
    await page.goto("/files");
    await page.getByText("notes.md").first().click();
    await expect(page.getByText("Preview")).toBeVisible();
    await expect(page.getByText("preview line two")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("2 lines")).toBeVisible();
  });

  // AC-06/BDD-06: CSV renders as a table
  test("renders a CSV file as a table", async ({ page }) => {
    const csv = "region,revenue\nNortheast,1200\nSouthwest,3400";
    const id = await uploadFile(page, "kpis.csv", "text/csv", csv);
    await page.goto(`/files/${id}`);
    await expect(page.getByRole("cell", { name: "Northeast" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("cell", { name: "3400" })).toBeVisible();
  });

  // AC-13/BDD-13: large spreadsheet truncation notice
  test("caps large CSVs and shows a truncation notice", async ({ page }) => {
    const rows = ["col_a,col_b"];
    for (let i = 0; i < 600; i++) rows.push(`row${i},${i}`);
    const id = await uploadFile(page, "big.csv", "text/csv", rows.join("\n"));
    await page.goto(`/files/${id}`);
    await expect(page.getByText(/Showing first 500 rows/)).toBeVisible({
      timeout: 20_000,
    });
  });

  // AC-10/BDD-10: graceful fallback for unsupported types
  test("shows a graceful fallback for unsupported file types", async ({
    page,
  }) => {
    const id = await uploadFile(
      page,
      "deck.pptx",
      "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "not really a deck"
    );
    await page.goto(`/files/${id}`);
    await expect(
      page.getByText(/No inline preview for presentations|Preview not available/)
    ).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("button", { name: /Download/ })).toBeVisible();
  });

  // AC-15/BDD-15: corrupt office file shows error state, page stays usable
  test("shows an error state for a corrupt spreadsheet", async ({ page }) => {
    const id = await uploadFile(
      page,
      "broken.xlsx",
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      "these are not xlsx bytes"
    );
    await page.goto(`/files/${id}`);
    await expect(page.getByText("Could not load preview")).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText("Details")).toBeVisible();
  });

  // AC-17/BDD-17: preview requires authentication
  test("redirects unauthenticated visitors to login", async ({ page }) => {
    const id = await uploadFile(page, "secret.md", "text/markdown", "shh");
    await clearAuth(page);
    await page.goto(`/files/${id}`);
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  });
});
