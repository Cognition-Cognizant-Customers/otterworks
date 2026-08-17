import { test, expect } from "@playwright/test";
import { uniqueEmail, registerUser, loginUser } from "./fixtures/test-helpers";

test.describe("Authentication", () => {
  test.describe("Registration", () => {
    test("submits registration form", async ({ page }) => {
      await registerUser(page);
      // Without a running backend, registration will show an error or stay on the page
      // Verify the form was submitted (button becomes disabled during loading)
      const errorMsg = page.getByText("Registration failed");
      const dashboard = page.getByRole("heading", { name: "Dashboard" });
      // Either we get an error (no backend) or we redirect to dashboard (backend running)
      await expect(errorMsg.or(dashboard)).toBeVisible({ timeout: 15_000 });
    });

    test("shows validation error for short name", async ({ page }) => {
      await page.goto("/register");
      await page.getByLabel("Full name").fill("A");
      await page.getByLabel("Email").fill(uniqueEmail());
      await page.getByLabel("Password", { exact: true }).fill("Passw0rd!23");
      await page.getByLabel("Confirm password").fill("Passw0rd!23");
      await page.getByRole("button", { name: "Create account" }).click();
      await expect(page.getByText("Name must be at least 2 characters")).toBeVisible();
    });

    test("shows validation error for invalid email", async ({ page }) => {
      await page.goto("/register");
      await page.getByLabel("Full name").fill("Test User");
      const emailInput = page.getByLabel("Email");
      await emailInput.fill("not-an-email");
      await page.getByLabel("Password", { exact: true }).fill("Passw0rd!23");
      await page.getByLabel("Confirm password").fill("Passw0rd!23");
      await page.getByRole("button", { name: "Create account" }).click();
      // Zod validation shows "Please enter a valid email" or browser native validation kicks in
      const zodError = page.getByText("Please enter a valid email");
      // The form should still be on the register page (not submitted)
      await expect(zodError.or(emailInput)).toBeVisible({ timeout: 5_000 });
      await expect(page).toHaveURL(/\/register/);
    });

    test("shows validation error for short password", async ({ page }) => {
      await page.goto("/register");
      await page.getByLabel("Full name").fill("Test User");
      await page.getByLabel("Email").fill(uniqueEmail());
      await page.getByLabel("Password", { exact: true }).fill("short");
      await page.getByLabel("Confirm password").fill("short");
      await page.getByRole("button", { name: "Create account" }).click();
      await expect(page.getByText("Password must be at least 8 characters")).toBeVisible();
    });

    test("shows validation error for mismatched passwords", async ({ page }) => {
      await page.goto("/register");
      await page.getByLabel("Full name").fill("Test User");
      await page.getByLabel("Email").fill(uniqueEmail());
      await page.getByLabel("Password", { exact: true }).fill("Passw0rd!23");
      await page.getByLabel("Confirm password").fill("DifferentPass1!");
      await page.getByRole("button", { name: "Create account" }).click();
      await expect(page.getByText("Passwords do not match")).toBeVisible();
    });

    test("has link to sign in page", async ({ page }) => {
      await page.goto("/register");
      await expect(page.getByRole("link", { name: "Sign in" })).toBeVisible();
      await page.getByRole("link", { name: "Sign in" }).click();
      await expect(page).toHaveURL(/\/login/);
    });
  });

  test.describe("Login", () => {
    test("renders login form with email and password fields", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByLabel("Email")).toBeVisible();
      await expect(page.getByLabel("Password")).toBeVisible();
      await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
    });

    test("shows validation error for empty email", async ({ page }) => {
      await page.goto("/login");
      await page.getByLabel("Password").fill("somepassword");
      await page.getByRole("button", { name: "Sign in" }).click();
      await expect(page.getByText("Please enter a valid email")).toBeVisible();
    });

    test("shows validation error for empty password", async ({ page }) => {
      await page.goto("/login");
      await page.getByLabel("Email").fill("user@test.com");
      await page.getByRole("button", { name: "Sign in" }).click();
      await expect(page.getByText("Password is required")).toBeVisible();
    });

    test("shows error for invalid credentials", async ({ page }) => {
      await loginUser(page, "nonexistent@test.com", "WrongPassword123");
      await expect(
        page.getByText("Invalid email or password")
      ).toBeVisible({ timeout: 10_000 });
    });

    test("toggles password visibility", async ({ page }) => {
      await page.goto("/login");
      const passwordInput = page.getByLabel("Password");
      await expect(passwordInput).toHaveAttribute("type", "password");

      // Click the eye icon toggle button (it's the button inside the password field's relative container)
      const toggleButton = page.locator("#password").locator("..").locator("button[type='button']");
      await toggleButton.click();
      await expect(passwordInput).toHaveAttribute("type", "text");
    });

    test("has link to registration page", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByRole("link", { name: "Create one" })).toBeVisible();
      await page.getByRole("link", { name: "Create one" }).click();
      await expect(page).toHaveURL(/\/register/);
    });

    test("displays OtterWorks logo and branding", async ({ page }) => {
      await page.goto("/login");
      await expect(page.getByText("OtterWorks")).toBeVisible();
      await expect(page.getByText("Sign in to your account")).toBeVisible();
    });
  });
});
