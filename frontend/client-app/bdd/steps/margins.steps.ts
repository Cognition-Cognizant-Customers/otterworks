import { Given, Then } from "@cucumber/cucumber";
import { expect } from "@playwright/test";
import { OtterWorld } from "../support/world";

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

Given("I am logged in as a new user", async function (this: OtterWorld) {
  const email = `bdd-margins-${Date.now()}-${Math.random().toString(36).slice(2, 7)}@otterworks.test`;
  const password = "Passw0rd!23";
  await this.page.goto(`${BASE_URL}/register`);
  await this.page.getByLabel("Full name").fill("BDD Margins User");
  await this.page.getByLabel("Email").fill(email);
  await this.page.getByLabel("Password", { exact: true }).fill(password);
  await this.page.getByLabel("Confirm password").fill(password);
  await this.page.getByRole("button", { name: "Create account" }).click();
  await expect(this.page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
});

Then("I should be on the {string} page", async function (this: OtterWorld, path: string) {
  await this.page.waitForURL(`**${path}`, { timeout: 10_000 });
  await expect(this.page).toHaveURL(`${BASE_URL}${path}`);
});

Then("I should see the margins data caption", async function (this: OtterWorld) {
  const caption = this.page.getByTestId("margins-caption");
  await expect(caption).toBeVisible({ timeout: 15_000 });
  await expect(caption).toContainText(/Data as of \d{4}-\d{2}-\d{2}/);
  await expect(caption).toContainText("Source: Trading Economics (manual pull)");
});

Then("I should see the margins grid with rows", async function (this: OtterWorld) {
  const grid = this.page.getByTestId("margins-grid");
  await expect(grid).toBeVisible({ timeout: 15_000 });
  const rows = grid.locator("tbody tr");
  expect(await rows.count()).toBeGreaterThan(10);
});

Then("I should see the margins source badge", async function (this: OtterWorld) {
  const badge = this.page.getByTestId("source-badge");
  await expect(badge).toBeVisible({ timeout: 15_000 });
  await expect(badge).toHaveText(/synthetic|live/);
});
