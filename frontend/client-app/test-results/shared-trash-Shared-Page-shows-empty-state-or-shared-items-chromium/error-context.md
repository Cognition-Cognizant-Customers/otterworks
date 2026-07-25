# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: shared-trash.spec.ts >> Shared Page >> shows empty state or shared items
- Location: e2e/shared-trash.spec.ts:11:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/No shared|Nothing shared/i).or(locator('[class*=\'grid\'] > *, [class*=\'divide\'] > *').first())
Expected: visible
Timeout: 10000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 10000ms
  - waiting for getByText(/No shared|Nothing shared/i).or(locator('[class*=\'grid\'] > *, [class*=\'divide\'] > *').first())
    - waiting for" http://localhost:3000/login" navigation to finish...
    - navigated to "http://localhost:3000/login"

```

# Page snapshot

```yaml
- generic [ref=e4]:
  - generic [ref=e5]:
    - link "OtterWorks logo OtterWorks" [ref=e6] [cursor=pointer]:
      - /url: /
      - img "OtterWorks logo" [ref=e7]
      - generic [ref=e8]: OtterWorks
    - paragraph [ref=e9]: Sign in to your account
  - generic [ref=e11]:
    - generic [ref=e12]:
      - generic [ref=e13]: Email
      - textbox "Email" [ref=e14]:
        - /placeholder: you@example.com
    - generic [ref=e15]:
      - generic [ref=e16]: Password
      - generic [ref=e17]:
        - textbox "Password" [ref=e18]:
          - /placeholder: Enter your password
        - button [ref=e19] [cursor=pointer]:
          - img [ref=e20]
    - button "Sign in" [ref=e23] [cursor=pointer]:
      - img [ref=e24]
      - text: Sign in
  - paragraph [ref=e27]:
    - text: Don't have an account?
    - link "Create one" [ref=e28] [cursor=pointer]:
      - /url: /register
```

# Test source

```ts
  1  | import { test, expect } from "@playwright/test";
  2  | 
  3  | test.describe("Shared Page", () => {
  4  |   test("shows Shared heading or redirects to login", async ({ page }) => {
  5  |     await page.goto("/shared");
  6  |     const heading = page.getByRole("heading", { name: /Shared/i });
  7  |     const loginHeading = page.getByText("Sign in to your account");
  8  |     await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });
  9  |   });
  10 | 
  11 |   test("shows empty state or shared items", async ({ page }) => {
  12 |     await page.goto("/shared");
  13 |     const heading = page.getByRole("heading", { name: /Shared/i });
  14 |     const loginHeading = page.getByText("Sign in to your account");
  15 |     await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });
  16 | 
  17 |     if (await heading.isVisible().catch(() => false)) {
  18 |       const emptyState = page.getByText(/No shared|Nothing shared/i);
  19 |       const items = page.locator("[class*='grid'] > *, [class*='divide'] > *").first();
> 20 |       await expect(emptyState.or(items)).toBeVisible({ timeout: 10_000 });
     |                                          ^ Error: expect(locator).toBeVisible() failed
  21 |     }
  22 |   });
  23 | });
  24 | 
  25 | test.describe("Trash Page", () => {
  26 |   test("shows Trash heading or redirects to login", async ({ page }) => {
  27 |     await page.goto("/trash");
  28 |     const heading = page.getByRole("heading", { name: /Trash/i });
  29 |     const loginHeading = page.getByText("Sign in to your account");
  30 |     await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });
  31 |   });
  32 | 
  33 |   test("shows empty state or trashed items", async ({ page }) => {
  34 |     await page.goto("/trash");
  35 |     const heading = page.getByRole("heading", { name: /Trash/i });
  36 |     const loginHeading = page.getByText("Sign in to your account");
  37 |     await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });
  38 | 
  39 |     if (await heading.isVisible().catch(() => false)) {
  40 |       const emptyState = page.getByText(/No deleted|Trash is empty|No items/i);
  41 |       const items = page.locator("[class*='grid'] > *, [class*='divide'] > *").first();
  42 |       await expect(emptyState.or(items)).toBeVisible({ timeout: 10_000 });
  43 |     }
  44 |   });
  45 | });
  46 | 
```