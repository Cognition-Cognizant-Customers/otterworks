import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useSearchParams } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { SearchBar } from "./search-bar";

function renderSearchBar() {
  return render(
    <MemoryRouter initialEntries={["/files"]}>
      <SearchBar />
      <Routes>
        <Route path="/search" element={<SearchDestination />} />
      </Routes>
    </MemoryRouter>,
  );
}

function SearchDestination() {
  const [params] = useSearchParams();
  return <p>Search results for {params.get("q")}</p>;
}

describe("SearchBar", () => {
  it("does not navigate for whitespace-only queries", () => {
    renderSearchBar();
    const input = screen.getByPlaceholderText("Search files, documents...");
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form")!);
    expect(screen.queryByText("Search results page")).not.toBeInTheDocument();
  });

  it("trims and encodes a query before navigating", () => {
    renderSearchBar();
    const input = screen.getByPlaceholderText("Search files, documents...");
    fireEvent.change(input, { target: { value: "  otter care & feeding  " } });
    fireEvent.submit(input.closest("form")!);
    expect(screen.getByText("Search results for otter care & feeding")).toBeInTheDocument();
  });

  it("clears a non-empty query", () => {
    renderSearchBar();
    const input = screen.getByPlaceholderText("Search files, documents...");
    fireEvent.change(input, { target: { value: "otter" } });
    fireEvent.click(screen.getByRole("button"));
    expect(input).toHaveValue("");
  });
});
