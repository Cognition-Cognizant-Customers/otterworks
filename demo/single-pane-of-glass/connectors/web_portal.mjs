// System 2 — the UI-only web application (no API), retrieved through the browser.
//
// Connects Playwright to the Chrome that Devin drives (over CDP), logs into the
// OtterWorks web portal exactly like a person would using vaulted credentials,
// reads the figures that are only rendered on screen, and captures a screenshot.
// This is the "computer use / web app with no API" leg of the workflow.
//
// Emits a single JSON object on stdout. All diagnostics go to stderr so stdout
// stays parseable by the Python orchestrator.

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { mkdirSync } from "node:fs";
import { createRequire } from "node:module";

const __dirname = dirname(fileURLToPath(import.meta.url));

// Reuse the web client's Playwright install rather than adding a dependency here.
// ESM ignores NODE_PATH for bare specifiers, so resolve it explicitly via require.
const clientAppDir =
  process.env.OTTER_CLIENT_APP_DIR ||
  join(__dirname, "..", "..", "..", "frontend", "client-app");
const require = createRequire(join(clientAppDir, "package.json"));
const { chromium } = require("playwright");

const WEB_URL = process.env.OTTER_WEB_URL || "http://localhost:3000";
const CDP_URL = process.env.OTTER_CDP_URL || "http://localhost:29229";
const EMAIL = process.env.DRIVE_EMAIL || "";
const PASSWORD = process.env.DRIVE_PASSWORD || "";
const OUTPUT_DIR =
  process.env.SPOG_OUTPUT_DIR || join(__dirname, "..", "output");

const log = (...a) => console.error("[web_portal]", ...a);

async function main() {
  if (!EMAIL || !PASSWORD) {
    throw new Error("DRIVE_EMAIL / DRIVE_PASSWORD not set (provide from vault).");
  }
  mkdirSync(OUTPUT_DIR, { recursive: true });

  log(`connecting to Chrome over CDP at ${CDP_URL}`);
  const browser = await chromium.connectOverCDP(CDP_URL);
  const context = browser.contexts()[0] || (await browser.newContext());
  const page = await context.newPage();

  try {
    await page.setViewportSize({ width: 1440, height: 1024 });
    log(`opening ${WEB_URL}/login`);
    await page.goto(`${WEB_URL}/login`, { waitUntil: "domcontentloaded" });

    // Log in through the UI if the form is present (session may already exist).
    const emailField = page.locator("#email");
    if (await emailField.isVisible({ timeout: 8000 }).catch(() => false)) {
      log("filling login form");
      await emailField.fill(EMAIL);
      await page.locator("#password").fill(PASSWORD);
      await page.getByRole("button", { name: /sign in/i }).click();
    }

    log("waiting for dashboard");
    await page.waitForURL(/\/dashboard/, { timeout: 20000 }).catch(() => {});
    await page.goto(`${WEB_URL}/dashboard`, { waitUntil: "domcontentloaded" });
    // Wait for the recent-files cards to render (real content, not the skeleton).
    await page.locator('a[href^="/files/"]').first().waitFor({ timeout: 20000 });
    await page.waitForTimeout(1200);

    // Read the content the way a person sees it: the recent files/documents the
    // portal renders on screen (name + size/date shown on each card).
    const recentFiles = await page.$$eval('a[href^="/files/"]', (nodes) =>
      nodes.map((a) => {
        const name = (a.querySelector("p.font-medium")?.textContent || "").trim();
        const meta = (a.querySelector("p.text-xs")?.textContent || "").trim();
        return { name, meta };
      }).filter((f) => f.name)
    );
    const recentDocuments = await page.$$eval('a[href^="/documents/"]', (nodes) =>
      nodes.map((a) => (a.querySelector("p.font-medium")?.textContent || "").trim())
        .filter(Boolean)
    );
    const account = await page
      .locator("aside p.font-medium, nav p.font-medium")
      .last()
      .textContent()
      .catch(() => null);

    const screenshotPath = join(OUTPUT_DIR, "portal.png");
    await page.screenshot({ path: screenshotPath, fullPage: false });
    log(`captured screenshot -> ${screenshotPath}`);

    const result = {
      source: "OtterWorks Web Portal (UI only)",
      type: "Browser / computer use",
      logged_in_as: EMAIL,
      account: account ? account.trim() : EMAIL,
      url: `${WEB_URL}/dashboard`,
      recent_files: recentFiles,
      recent_documents: recentDocuments,
      screenshot: "portal.png",
    };
    log(`read ${recentFiles.length} files + ${recentDocuments.length} docs on screen`);
    process.stdout.write(JSON.stringify(result));
  } finally {
    await page.close().catch(() => {});
    await browser.close().catch(() => {});
  }
}

main().catch((err) => {
  log("ERROR", err && err.message ? err.message : err);
  process.exit(1);
});
