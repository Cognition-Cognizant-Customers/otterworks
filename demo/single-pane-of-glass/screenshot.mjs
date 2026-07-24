// Render dashboard.html to a full-page PNG (dashboard_full.png) — the shareable
// "visual" for the demo / scheduled session. Uses the Chrome that Devin drives
// over CDP (no local headless browser download required).
//
// Usage: node screenshot.mjs <dashboard.html path> <output png path>

import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { createRequire } from "node:module";

const __dirname = dirname(fileURLToPath(import.meta.url));
const clientAppDir =
  process.env.OTTER_CLIENT_APP_DIR ||
  join(__dirname, "..", "..", "frontend", "client-app");
const require = createRequire(join(clientAppDir, "package.json"));
const { chromium } = require("playwright");

const CDP_URL = process.env.OTTER_CDP_URL || "http://localhost:29229";
const htmlPath = process.argv[2];
const outPath = process.argv[3];

if (!htmlPath || !outPath) {
  console.error("usage: node screenshot.mjs <html> <png>");
  process.exit(2);
}

const browser = await chromium.connectOverCDP(CDP_URL);
const ctx = browser.contexts()[0] || (await browser.newContext());
const page = await ctx.newPage();
try {
  await page.setViewportSize({ width: 1240, height: 1000 });
  // Renders a local demo artifact we just wrote; path is an internal argument.
  // nosemgrep: javascript.playwright.security.audit.playwright-goto-injection.playwright-goto-injection
  await page.goto("file://" + htmlPath, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  await page.screenshot({ path: outPath, fullPage: true });
  console.error(`[screenshot] wrote ${outPath}`);
} finally {
  await page.close().catch(() => {});
  await browser.close().catch(() => {});
}
