import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShareDialog } from "./share-dialog";

const sharedUser = {
  userId: "user-2",
  name: "Second Otter",
  email: "second@example.com",
  permission: "view" as const,
};

function renderDialog(overrides: Partial<React.ComponentProps<typeof ShareDialog>> = {}) {
  const props: React.ComponentProps<typeof ShareDialog> = {
    fileId: "file-1",
    fileName: "notes.txt",
    ownerId: "owner-1",
    ownerName: "Owner Otter",
    ownerEmail: "owner@example.com",
    sharedWith: [sharedUser],
    onShare: vi.fn().mockResolvedValue(undefined),
    onClose: vi.fn(),
    onPermissionChange: vi.fn().mockResolvedValue(undefined),
    onRemoveAccess: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  return { ...render(<ShareDialog {...props} />), props };
}

describe("ShareDialog", () => {
  it("disables sharing until an invitee is entered and sends the selected permission", async () => {
    const { props } = renderDialog();
    const shareButton = screen.getByRole("button", { name: "Share" });
    expect(shareButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText("Add people by email"), {
      target: { value: " new@example.com " },
    });
    fireEvent.change(screen.getAllByRole("combobox")[0], {
      target: { value: "edit" },
    });
    fireEvent.click(shareButton);

    await waitFor(() =>
      expect(props.onShare).toHaveBeenCalledWith("new@example.com", "edit"),
    );
    expect(screen.getByPlaceholderText("Add people by email")).toHaveValue("");
  });

  it("surfaces a failed share and keeps the invitee for retry", async () => {
    const onShare = vi.fn().mockRejectedValue(new Error("network"));
    renderDialog({ onShare });
    const input = screen.getByPlaceholderText("Add people by email");
    fireEvent.change(input, { target: { value: "retry@example.com" } });
    fireEvent.click(screen.getByRole("button", { name: "Share" }));

    await waitFor(() => expect(onShare).toHaveBeenCalledOnce());
    expect(input).toHaveValue("retry@example.com");
  });

  it("shows a disabled loading state while sharing is pending", async () => {
    let resolveShare!: () => void;
    const onShare = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          resolveShare = resolve;
        }),
    );
    renderDialog({ onShare });
    fireEvent.change(screen.getByPlaceholderText("Add people by email"), {
      target: { value: "pending@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Share" }));

    expect(screen.getByRole("button", { name: "Sharing..." })).toBeDisabled();
    resolveShare();
    await waitFor(() => expect(onShare).toHaveBeenCalledOnce());
  });

  it("updates permissions and removes access for an existing collaborator", async () => {
    const { props } = renderDialog();
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[1], { target: { value: "edit" } });
    await waitFor(() =>
      expect(props.onPermissionChange).toHaveBeenCalledWith("user-2", "edit"),
    );

    fireEvent.click(screen.getByTitle("Remove access"));
    await waitFor(() => expect(props.onRemoveAccess).toHaveBeenCalledWith("user-2"));
  });

  it("switches to link access and copies the file URL", async () => {
    const clipboard = { writeText: vi.fn().mockResolvedValue(undefined) };
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: clipboard,
    });
    renderDialog();
    fireEvent.click(screen.getByRole("button", { name: /Get link/i }));
    expect(screen.getByText("Anyone with the link")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Anyone with the link"));
    fireEvent.click(screen.getByRole("button", { name: /Copy link/i }));

    await waitFor(() =>
      expect(clipboard.writeText).toHaveBeenCalledWith(`${window.location.origin}/files/file-1`),
    );
  });
});
