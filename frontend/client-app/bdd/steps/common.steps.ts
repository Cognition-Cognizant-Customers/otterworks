import { Given, When, Then } from "@cucumber/cucumber";
import { expect } from "@playwright/test";
import { OtterWorld } from "../support/world";

const BASE_URL = process.env.BASE_URL || "http://localhost:3000";

Given("I am on the landing page", async function (this: OtterWorld) {
  await this.page.goto(`${BASE_URL}/`);
});

Given("I am on the login page", async function (this: OtterWorld) {
  await this.page.goto(`${BASE_URL}/login`);
});

Given("I am on the registration page", async function (this: OtterWorld) {
  await this.page.goto(`${BASE_URL}/register`);
});

Given("I navigate to {string}", async function (this: OtterWorld, path: string) {
  await this.page.goto(`${BASE_URL}${path}`);
});

When("I click the link {string}", async function (this: OtterWorld, linkText: string) {
  await this.page.getByRole("link", { name: linkText }).first().click();
  await this.page.waitForLoadState("networkidle").catch(() => {});
});

When("I click the {string} button", async function (this: OtterWorld, buttonText: string) {
  await this.page.getByRole("button", { name: buttonText }).click();
});

When("I fill in {string} with {string}", async function (this: OtterWorld, label: string, value: string) {
  if (label === "Password") {
    await this.page.getByLabel(label, { exact: true }).fill(value);
  } else {
    await this.page.getByLabel(label).fill(value);
  }
});

Then("I should see the heading {string}", async function (this: OtterWorld, heading: string) {
  await expect(
    this.page.getByRole("heading", { name: heading }).first()
  ).toBeVisible({
    timeout: 10_000,
  });
});

Then("I should see the text {string}", async function (this: OtterWorld, text: string) {
  await expect(this.page.getByText(text).first()).toBeVisible({ timeout: 10_000 });
});

Then("I should see the otter logo", async function (this: OtterWorld) {
  await expect(this.page.getByAltText("OtterWorks logo").first()).toBeVisible({
    timeout: 10_000,
  });
});

Then("I should see a link {string}", async function (this: OtterWorld, linkText: string) {
  await expect(
    this.page.getByRole("link", { name: linkText }).first()
  ).toBeVisible({
    timeout: 10_000,
  });
});

Then("I should see a {string} input field", async function (this: OtterWorld, label: string) {
  await expect(this.page.getByLabel(label).first()).toBeVisible({ timeout: 10_000 });
});

Then("I should see an {string} input field", async function (this: OtterWorld, label: string) {
  await expect(this.page.getByLabel(label).first()).toBeVisible({ timeout: 10_000 });
});

Then("I should see a {string} button", async function (this: OtterWorld, buttonText: string) {
  await expect(
    this.page.getByRole("button", { name: buttonText })
  ).toBeVisible({ timeout: 10_000 });
});

Then("the URL should contain {string}", async function (this: OtterWorld, path: string) {
  await this.page.waitForTimeout(1_000);
  const currentUrl = this.page.url();
  expect(currentUrl).toContain(path);
});

Then(
  "I should see the text {string} or {string}",
  async function (this: OtterWorld, text1: string, text2: string) {
    const el1 = this.page.getByText(text1).first();
    const el2 = this.page.getByText(text2).first();
    await expect(el1.or(el2)).toBeVisible({ timeout: 10_000 });
  }
);
