# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: notifications.spec.ts >> Notifications Page >> shows empty state or notification list
- Location: e2e/notifications.spec.ts:14:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByText(/No notifications/i).or(locator('[class*=\'divide-y\'] > *').first())
Expected: visible
Timeout: 15000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 15000ms
  - waiting for getByText(/No notifications/i).or(locator('[class*=\'divide-y\'] > *').first())

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
  3  | test.describe("Notifications Page", () => {
  4  |   test.beforeEach(async ({ page }) => {
  5  |     await page.goto("/notifications");
  6  |   });
  7  | 
  8  |   test("shows Notifications heading or redirects to login", async ({ page }) => {
  9  |     const heading = page.getByRole("heading", { name: /Notification/i });
  10 |     const loginHeading = page.getByText("Sign in to your account");
  11 |     await expect(heading.or(loginHeading)).toBeVisible({ timeout: 10_000 });
  12 |   });
  13 | 
  14 |   test("shows empty state or notification list", async ({ page }) => {
  15 |     // Wait for the Notifications heading to appear
  16 |     const heading = page.getByRole("heading", { name: /Notification/i });
  17 |     await expect(heading).toBeVisible({ timeout: 15_000 });
  18 | 
  19 |     // Wait for loading to finish, then check for empty state or notification items
  20 |     const emptyState = page.getByText(/No notifications/i);
  21 |     const notifItems = page.locator("[class*='divide-y'] > *").first();
> 22 |     await expect(emptyState.or(notifItems)).toBeVisible({ timeout: 15_000 });
     |                                             ^ Error: expect(locator).toBeVisible() failed
  23 |   });
  24 | });
  25 | 
```