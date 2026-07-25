import { test, expect } from "@playwright/test";

// OTD-13 corporate rebrand — see docs/bdd/otd13-corporate-rebrand-bdd.md
test.describe("Landing Page", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
  });

  // AC-01 / BDD-01
  test("displays the corporate hero with the otter logo", async ({ page }) => {
    await expect(
      page.getByRole("heading", { name: "OtterWorks", exact: true })
    ).toBeVisible();
    await expect(page.getByAltText("OtterWorks logo").first()).toBeVisible();
    await expect(
      page.getByText("Enterprise retail products for otters").first()
    ).toBeVisible();
  });

  test("shows Sign In and Create Account CTAs", async ({ page }) => {
    await expect(page.getByRole("link", { name: "Sign In" }).first()).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Create Account" }).first()
    ).toBeVisible();
  });

  // AC-03 / BDD-07
  test("renders all corporate identity sections", async ({ page }) => {
    const sections = [
      "Our Story",
      "Leadership",
      "Departments",
      "Products",
      "Newsroom",
      "Careers",
    ];
    for (const title of sections) {
      await expect(
        page.getByRole("heading", { name: title, exact: true })
      ).toBeVisible();
    }
  });

  test("Sign In link navigates to /login", async ({ page }) => {
    await page.getByRole("link", { name: "Sign In" }).first().click();
    await expect(page).toHaveURL(/\/login/);
  });

  test("Create Account link navigates to /register", async ({ page }) => {
    await page.getByRole("link", { name: "Create Account" }).first().click();
    await expect(page).toHaveURL(/\/register/);
  });

  // AC-02c / BDD-06
  test("corporate footer shows company, version and legal links", async ({
    page,
  }) => {
    await expect(page.getByText("© OtterWorks, Inc.").first()).toBeVisible();
    await expect(page.getByText(/v\d+\.\d+\.\d+/).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Terms" }).first()).toBeVisible();
    await expect(page.getByRole("link", { name: "Privacy" }).first()).toBeVisible();
  });

  // AC-02c / BDD-06
  test("Terms and Privacy static pages are reachable", async ({ page }) => {
    await page.goto("/terms");
    await expect(
      page.getByRole("heading", { name: "Terms of Service" })
    ).toBeVisible();
    await page.goto("/privacy");
    await expect(
      page.getByRole("heading", { name: "Privacy Policy" })
    ).toBeVisible();
  });
});
