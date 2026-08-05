import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@testing-library/jest-dom/vitest";
import type { FileItem } from "@/types";
import FilesPage from "./files";

vi.mock("@/components/layout/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

function makeFile(name: string, overrides: Partial<FileItem> = {}): FileItem {
  return {
    id: `id-${name}`,
    name,
    mimeType: "application/octet-stream",
    size: 1024,
    parentId: null,
    ownerId: "user-1",
    ownerName: "Owner",
    isFolder: false,
    path: `/${name}`,
    sharedWith: [],
    tags: [],
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-02T00:00:00Z",
    versions: [],
    ...overrides,
  };
}

const files = [
  makeFile("report.pdf"),
  makeFile("notes.txt"),
  makeFile("photo.png"),
  makeFile("diagram.jpg"),
  makeFile("archive.zip"),
];

const folders = [
  makeFile("Projects", { id: "folder-1", isFolder: true }),
  makeFile("Invoices", { id: "folder-2", isFolder: true }),
];

vi.mock("@/lib/api", () => ({
  filesApi: {
    list: vi.fn(() => Promise.resolve({ data: files })),
    listFolders: vi.fn(() => Promise.resolve(folders)),
    getFolder: vi.fn(() => Promise.resolve(null)),
    delete: vi.fn(),
    deleteFolder: vi.fn(),
    createFolder: vi.fn(),
    renameFile: vi.fn(),
    renameFolder: vi.fn(),
    upload: vi.fn(),
    getDownloadUrl: vi.fn(),
    share: vi.fn(),
    updateSharePermission: vi.fn(),
    removeShare: vi.fn(),
  },
  starredApi: {
    isStarred: vi.fn(() => false),
    toggle: vi.fn(() => false),
  },
}));

function renderFilesPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/files"]}>
        <FilesPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

async function waitForListing() {
  await waitFor(() => expect(screen.getByText("report.pdf")).toBeInTheDocument());
}

const allFileNames = files.map((f) => f.name);
const folderNames = folders.map((f) => f.name);

function expectVisible(names: string[]) {
  for (const name of names) {
    expect(screen.getByText(name)).toBeInTheDocument();
  }
}

function expectHidden(names: string[]) {
  for (const name of names) {
    expect(screen.queryByText(name)).not.toBeInTheDocument();
  }
}

describe("Files page file-type filter", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    cleanup();
  });

  it("shows all files and folders by default with the All chip active", async () => {
    renderFilesPage();
    await waitForListing();
    expectVisible([...allFileNames, ...folderNames]);
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "Documents" })).toHaveAttribute("aria-pressed", "false");
  });

  it("Documents chip shows only document files", async () => {
    renderFilesPage();
    await waitForListing();
    fireEvent.click(screen.getByRole("button", { name: "Documents" }));
    expectVisible(["report.pdf", "notes.txt"]);
    expectHidden(["photo.png", "diagram.jpg", "archive.zip"]);
    expect(screen.getByRole("button", { name: "Documents" })).toHaveAttribute("aria-pressed", "true");
  });

  it("Images chip shows only image files", async () => {
    renderFilesPage();
    await waitForListing();
    fireEvent.click(screen.getByRole("button", { name: "Images" }));
    expectVisible(["photo.png", "diagram.jpg"]);
    expectHidden(["report.pdf", "notes.txt", "archive.zip"]);
  });

  it("Other chip shows only files that are neither documents nor images", async () => {
    renderFilesPage();
    await waitForListing();
    fireEvent.click(screen.getByRole("button", { name: "Other" }));
    expectVisible(["archive.zip"]);
    expectHidden(["report.pdf", "notes.txt", "photo.png", "diagram.jpg"]);
  });

  it("keeps folders visible under every chip", async () => {
    renderFilesPage();
    await waitForListing();
    for (const chip of ["Documents", "Images", "Other", "All"]) {
      fireEvent.click(screen.getByRole("button", { name: chip }));
      expectVisible(folderNames);
    }
  });

  it("switching back to All restores every file", async () => {
    renderFilesPage();
    await waitForListing();
    fireEvent.click(screen.getByRole("button", { name: "Images" }));
    expectHidden(["report.pdf"]);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    expectVisible(allFileNames);
  });

  it("shows an empty message when the filter matches no files", async () => {
    const { filesApi } = await import("@/lib/api");
    vi.mocked(filesApi.list).mockResolvedValueOnce({
      data: [makeFile("archive.zip")],
    } as Awaited<ReturnType<typeof filesApi.list>>);
    renderFilesPage();
    await waitFor(() => expect(screen.getByText("archive.zip")).toBeInTheDocument());
    fireEvent.click(screen.getByRole("button", { name: "Images" }));
    expect(screen.getByText("No matching files")).toBeInTheDocument();
    expectVisible(folderNames);
  });
});
