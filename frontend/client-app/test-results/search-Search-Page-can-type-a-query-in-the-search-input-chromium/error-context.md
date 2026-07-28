# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: search.spec.ts >> Search Page >> can type a query in the search input
- Location: e2e/search.spec.ts:36:7

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: getByPlaceholder('Search files, documents, and folders...')
Expected: visible
Timeout: 10000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 10000ms
  - waiting for getByPlaceholder('Search files, documents, and folders...')

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
  3  | test.describe("Search Page", () => {
  4  |   test.beforeEach(async ({ page }) => {
  5  |     await page.goto("/search");
  6  |     await page.waitForLoadState("networkidle");
  7  |   });
  8  | 
  9  |   test("shows Search heading", async ({ page }) => {
  10 |     await expect(
  11 |       page.getByRole("heading", { name: "Search", exact: true })
  12 |     ).toBeVisible({ timeout: 10_000 });
  13 |   });
  14 | 
  15 |   test("displays search input with placeholder", async ({ page }) => {
  16 |     await expect(
  17 |       page.getByPlaceholder("Search files, documents, and folders...")
  18 |     ).toBeVisible({ timeout: 10_000 });
  19 |   });
  20 | 
  21 |   test("shows empty state before searching", async ({ page }) => {
  22 |     await expect(page.getByText("Search OtterWorks")).toBeVisible({
  23 |       timeout: 10_000,
  24 |     });
  25 |     await expect(
  26 |       page.getByText("Find files, documents, and folders across your workspace")
  27 |     ).toBeVisible();
  28 |   });
  29 | 
  30 |   test("has filter toggle button", async ({ page }) => {
  31 |     // The filter button is inside the search form
  32 |     const filterButton = page.locator("form button[type='button']").first();
  33 |     await expect(filterButton).toBeVisible({ timeout: 10_000 });
  34 |   });
  35 | 
  36 |   test("can type a query in the search input", async ({ page }) => {
  37 |     const searchInput = page.getByPlaceholder(
  38 |       "Search files, documents, and folders..."
  39 |     );
> 40 |     await expect(searchInput).toBeVisible({ timeout: 10_000 });
     |                               ^ Error: expect(locator).toBeVisible() failed
  41 |     await searchInput.fill("test query");
  42 |     await expect(searchInput).toHaveValue("test query");
  43 |   });
  44 | 
  45 |   test("shows no results for gibberish query", async ({ page }) => {
  46 |     const searchInput = page.getByPlaceholder(
  47 |       "Search files, documents, and folders..."
  48 |     );
  49 |     await expect(searchInput).toBeVisible({ timeout: 10_000 });
  50 |     await searchInput.fill("xyznonexistent9999");
  51 |     await searchInput.press("Enter");
  52 | 
  53 |     // Wait for either results or no-results state
  54 |     const noResults = page.getByText("No results found");
  55 |     const spinner = page.locator("[class*='animate-spin']");
  56 |     await expect(noResults.or(spinner)).toBeVisible({ timeout: 15_000 });
  57 |   });
  58 | 
  59 |   test("displays breadcrumb navigation", async ({ page }) => {
  60 |     // Breadcrumb nav (aria-label="Breadcrumb") should contain "Search"
  61 |     const breadcrumb = page.getByLabel("Breadcrumb").getByText("Search");
  62 |     await expect(breadcrumb).toBeVisible({ timeout: 10_000 });
  63 |   });
  64 | });
  65 | 
```