import { type Page, expect } from "@playwright/test";

/**
 * Local-development e2e fixture value, not a real credential.
 * Override with E2E_TEST_PASSWORD when running against a non-local environment.
 */
const DEFAULT_TEST_CREDENTIAL = process.env.E2E_TEST_PASSWORD ?? "Passw0rd!23";

/** Generate a unique email for test isolation */
export function uniqueEmail(): string {
  return `test-${Date.now()}-${Math.random().toString(36).slice(2, 7)}@otterworks.test`;
}

/** Fill and submit the registration form */
export async function registerUser(
  page: Page,
  opts: { name?: string; email?: string; password?: string } = {}
) {
  const name = opts.name ?? "Test User";
  const email = opts.email ?? uniqueEmail();
  const password = opts.password ?? DEFAULT_TEST_CREDENTIAL;

  await page.goto("/register");
  await page.getByLabel("Full name").fill(name);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page.getByLabel("Confirm password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();

  return { name, email, password };
}

/** Fill and submit the login form */
export async function loginUser(
  page: Page,
  email: string,
  password: string
) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
}

/** Assert the page navigated to the dashboard after auth */
export async function expectDashboard(page: Page) {
  await expect(page).toHaveURL(/\/dashboard/, { timeout: 15_000 });
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible({
    timeout: 10_000,
  });
}

/** Clear auth tokens from localStorage so the next test starts logged-out */
export async function clearAuth(page: Page) {
  await page.evaluate(() => {
    localStorage.removeItem("otter_access_token");
    localStorage.removeItem("otter_refresh_token");
  });
}
