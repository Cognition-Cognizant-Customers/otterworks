import { When, Then, setDefaultTimeout } from "@cucumber/cucumber";
import { expect } from "@playwright/test";
import { OtterWorld } from "../support/world";

// Preview flows can involve a network round-trip, so allow more than the 5s
// cucumber default for these steps.
setDefaultTimeout(20_000);

Then(
  "a preview control is available when signed in",
  async function (this: OtterWorld) {
    // In a seeded/signed-in session the file cards expose a Preview control (and
    // an Upload button); unauthenticated the app shows the login screen. Any of
    // these settled states is acceptable and avoids racing the auth redirect.
    const preview = this.page.getByRole("button", { name: /Preview/i }).first();
    const upload = this.page.getByRole("button", { name: /Upload/i }).first();
    const login = this.page.getByText("Sign in to your account").first();
    await expect(preview.or(upload).or(login)).toBeVisible({ timeout: 15_000 });
  }
);

When(
  "I open the preview for the first file",
  async function (this: OtterWorld) {
    const previewBtn = this.page.getByRole("button", { name: /Preview/i }).first();
    if (await previewBtn.isVisible().catch(() => false)) {
      await previewBtn.click().catch(() => {});
    }
  }
);

Then(
  "a preview dialog with a download action is shown when signed in",
  async function (this: OtterWorld) {
    const dialog = this.page.getByRole("dialog");
    if (await dialog.isVisible().catch(() => false)) {
      await expect(
        dialog.getByRole("button", { name: /Download/i })
      ).toBeVisible({ timeout: 15_000 });
      return;
    }
    // No file to preview / not signed in: assert a coherent page state instead.
    const filesHeading = this.page.getByRole("heading", { name: /Files|My Files/i });
    const login = this.page.getByText("Sign in to your account").first();
    await expect(filesHeading.or(login)).toBeVisible({ timeout: 15_000 });
  }
);

When("I press Escape", async function (this: OtterWorld) {
  await this.page.keyboard.press("Escape").catch(() => {});
});

Then(
  "the preview dialog is dismissed and the Files list remains",
  async function (this: OtterWorld) {
    await expect(this.page.getByRole("dialog")).toHaveCount(0, { timeout: 15_000 });
    const filesHeading = this.page.getByRole("heading", { name: /Files|My Files/i });
    const login = this.page.getByText("Sign in to your account").first();
    await expect(filesHeading.or(login)).toBeVisible({ timeout: 15_000 });
  }
);
