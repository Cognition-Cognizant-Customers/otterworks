// @vitest-environment jsdom
import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { DocumentCard } from "./document-card";
import type { Document } from "@/types";

function makeDocument(overrides: Partial<Document> = {}): Document {
  return {
    id: "doc-1",
    title: "Quarterly Report",
    content: "Some content",
    ownerId: "user-1",
    ownerName: "Olive Otter",
    parentId: null,
    sharedWith: [],
    collaborators: [],
    tags: [],
    wordCount: 42,
    createdAt: "2026-01-01T00:00:00Z",
    updatedAt: "2026-01-02T00:00:00Z",
    ...overrides,
  };
}

function renderCard(document: Document, view: "grid" | "list" = "grid") {
  return render(
    <MemoryRouter>
      <DocumentCard document={document} view={view} />
    </MemoryRouter>
  );
}

afterEach(cleanup);

describe("DocumentCard archived badge", () => {
  const archivedAt = "2026-07-15T10:30:00Z";
  const expectedDate = new Date(archivedAt).toLocaleDateString();

  it("shows the badge with the archived date for an archived document in grid view", () => {
    renderCard(makeDocument({ isArchived: true, archivedAt }));
    expect(screen.getByText(`Archived ${expectedDate}`)).toBeTruthy();
  });

  it("shows the badge with the archived date in list view", () => {
    renderCard(makeDocument({ isArchived: true, archivedAt }), "list");
    expect(screen.getByText(`Archived ${expectedDate}`)).toBeTruthy();
  });

  it("shows the badge without a date when archivedAt is missing", () => {
    renderCard(makeDocument({ isArchived: true, archivedAt: null }));
    expect(screen.getByText("Archived")).toBeTruthy();
  });

  it("does not show the badge for an active document", () => {
    renderCard(makeDocument());
    expect(screen.queryByText(/^Archived/)).toBeNull();
  });
});
