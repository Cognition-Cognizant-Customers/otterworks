import { test, expect, type Page } from "@playwright/test";
import * as XLSX from "xlsx";
import { registerUser, expectDashboard } from "./fixtures/test-helpers";

// OTD-12 — file preview e2e. Uploads go through the real stack
// (gateway → file-service → S3/DynamoDB); previews fetch real presigned bytes.

const PNG_1PX = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==",
  "base64"
);

async function uploadFile(
  page: Page,
  file: { name: string; mimeType: string; buffer: Buffer }
) {
  await page.goto("/files");
  await expect(page.getByRole("heading", { name: "Files" })).toBeVisible({ timeout: 15_000 });
  await page.getByRole("button", { name: /Upload/i }).click();
  await page.locator('input[type="file"]').setInputFiles(file);
  // Wait for the uploaded file to appear in the listing
  await expect(page.getByText(file.name).first()).toBeVisible({ timeout: 20_000 });
}

async function openDetail(page: Page, fileName: string) {
  await page.goto("/files");
  await page.getByText(fileName).first().click();
  await expect(page.getByRole("heading", { name: fileName })).toBeVisible({ timeout: 15_000 });
}

test.describe("File Preview (OTD-12)", () => {
  test.beforeEach(async ({ page }) => {
    await registerUser(page);
    await expectDashboard(page);
  });

  test("AC-01/AC-05: CSV opens from the file list and renders as a table", async ({ page }) => {
    const csv = 'region,revenue\n"Pacific, NW",1200\nSoutheast,900\n';
    await uploadFile(page, {
      name: "preview-regions.csv",
      mimeType: "text/csv",
      buffer: Buffer.from(csv),
    });
    await openDetail(page, "preview-regions.csv");

    await expect(page.getByRole("heading", { name: "Preview", exact: true })).toBeVisible();
    const table = page.locator("table").filter({ has: page.getByRole("cell") });
    await expect(page.getByRole("columnheader", { name: "region" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("columnheader", { name: "revenue" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Pacific, NW" })).toBeVisible();
    expect(await table.count()).toBeGreaterThan(0);
  });

  test("AC-02: image renders inline", async ({ page }) => {
    await uploadFile(page, {
      name: "preview-pixel.png",
      mimeType: "image/png",
      buffer: PNG_1PX,
    });
    await openDetail(page, "preview-pixel.png");

    const img = page.getByRole("img", { name: "preview-pixel.png" });
    await expect(img).toBeVisible({ timeout: 15_000 });
  });

  test("AC-04: text file renders in the line-numbered viewer", async ({ page }) => {
    await uploadFile(page, {
      name: "preview-notes.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("first line\nsecond line\n"),
    });
    await openDetail(page, "preview-notes.txt");

    await expect(page.getByText("first line")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/\d+ lines/)).toBeVisible();
  });

  test("AC-06: xlsx renders as a table with sheet tabs", async ({ page }) => {
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(
      wb,
      XLSX.utils.aoa_to_sheet([
        ["quarter", "units"],
        ["Q1", 10],
        ["Q2", 20],
      ]),
      "Summary"
    );
    XLSX.utils.book_append_sheet(
      wb,
      XLSX.utils.aoa_to_sheet([
        ["note"],
        ["details here"],
      ]),
      "Details"
    );
    const buffer = XLSX.write(wb, { type: "buffer", bookType: "xlsx" }) as Buffer;

    await uploadFile(page, {
      name: "preview-report.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      buffer,
    });
    await openDetail(page, "preview-report.xlsx");

    await expect(page.getByRole("columnheader", { name: "quarter" })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("cell", { name: "Q1" })).toBeVisible();

    // Sheet tabs switch sheets
    await page.getByRole("button", { name: "Details" }).click();
    await expect(page.getByRole("columnheader", { name: "note" })).toBeVisible();
    await expect(page.getByRole("cell", { name: "details here" })).toBeVisible();
  });

  test("AC-10: unsupported type shows a graceful fallback card", async ({ page }) => {
    await uploadFile(page, {
      name: "preview-blob.bin",
      mimeType: "application/octet-stream",
      buffer: Buffer.from([0x00, 0x01, 0x02, 0x03]),
    });
    await openDetail(page, "preview-blob.bin");

    await expect(
      page.getByText("Inline preview isn't available for this file type.")
    ).toBeVisible({ timeout: 15_000 });
    // Fallback card has its own Download button (in addition to the page header's)
    await expect(page.getByRole("button", { name: "Download" })).toHaveCount(2);
  });

  test("AC-14: corrupt xlsx falls back gracefully", async ({ page }) => {
    await uploadFile(page, {
      name: "preview-corrupt.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      // A broken ZIP container (valid magic, garbage body) so workbook parsing fails
      buffer: Buffer.concat([Buffer.from([0x50, 0x4b, 0x03, 0x04]), Buffer.alloc(64, 0xff)]),
    });
    await openDetail(page, "preview-corrupt.xlsx");

    await expect(
      page.getByText("Could not render a preview of this file.")
    ).toBeVisible({ timeout: 20_000 });
  });

  test("AC-13: back/forward navigation shows the correct file's preview", async ({ page }) => {
    await uploadFile(page, {
      name: "preview-nav-a.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("content of file A"),
    });
    await uploadFile(page, {
      name: "preview-nav-b.txt",
      mimeType: "text/plain",
      buffer: Buffer.from("content of file B"),
    });

    await openDetail(page, "preview-nav-a.txt");
    await expect(page.getByText("content of file A")).toBeVisible({ timeout: 15_000 });

    await openDetail(page, "preview-nav-b.txt");
    await expect(page.getByText("content of file B")).toBeVisible({ timeout: 15_000 });

    await page.goBack();
    await page.goBack(); // back through /files to file A's detail
    await expect(page.getByRole("heading", { name: "preview-nav-a.txt" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("content of file A")).toBeVisible({ timeout: 15_000 });

    await page.goForward();
    await page.goForward();
    await expect(page.getByRole("heading", { name: "preview-nav-b.txt" })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("content of file B")).toBeVisible({ timeout: 15_000 });
  });
});
