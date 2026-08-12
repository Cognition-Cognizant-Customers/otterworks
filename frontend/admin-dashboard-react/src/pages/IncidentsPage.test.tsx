import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { server } from "@/test/server";
import { renderWithProviders } from "@/test/utils";
import { IncidentsPage } from "./IncidentsPage";

const rawIncident = {
  id: "inc-1",
  title: "Search is down",
  description: "Autocomplete returns 500s",
  severity: "high",
  status: "open",
  affected_service: "search-service",
  devin_session_id: null,
  devin_session_url: null,
  devin_session_status: null,
  reporter_id: null,
  resolved_at: null,
  closed_at: null,
  active: true,
  created_at: "2026-08-12T07:00:00Z",
  updated_at: "2026-08-12T07:00:00Z",
};

function mockAutoInvestigate() {
  server.use(
    http.get("/api/v1/admin/settings/auto_investigate", () =>
      HttpResponse.json({ enabled: true })
    )
  );
}

describe("IncidentsPage", () => {
  it("renders incidents on success", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () =>
        HttpResponse.json({ incidents: [rawIncident] })
      )
    );

    renderWithProviders(<IncidentsPage />);

    expect(screen.getByRole("progressbar", { name: "Loading" })).toBeInTheDocument();
    // Empty-state copy must not render while the first request is in flight
    expect(screen.queryByText("Automated Incident Response")).not.toBeInTheDocument();

    expect(await screen.findByText("Search is down")).toBeInTheDocument();
    expect(screen.getByText("Autocomplete returns 500s")).toBeInTheDocument();
    expect(screen.getByText("search-service")).toBeInTheDocument();
    expect(screen.getByText("1 Active")).toBeInTheDocument();
    expect(screen.getByText("No Devin session")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Launch Devin/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Resolve/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Delete/ })).toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Loading" })).not.toBeInTheDocument();
  });

  it("shows the onboarding empty state when there are no incidents", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () => HttpResponse.json({ incidents: [] }))
    );

    renderWithProviders(<IncidentsPage />);

    expect(screen.queryByText("Automated Incident Response")).not.toBeInTheDocument();
    expect(await screen.findByText("Automated Incident Response")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Report Your First Incident/ })
    ).toBeInTheDocument();
  });

  it("shows an error state (not the empty state) when the list request fails", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () =>
        HttpResponse.json({ error: "boom" }, { status: 500 })
      )
    );

    renderWithProviders(<IncidentsPage />);

    expect(await screen.findByText("Failed to load incidents")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
    expect(screen.queryByText("Automated Incident Response")).not.toBeInTheDocument();
    expect(screen.queryByRole("progressbar", { name: "Loading" })).not.toBeInTheDocument();
  });

  it("creates an incident through the labeled form", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () => HttpResponse.json({ incidents: [] })),
      http.post("/api/v1/admin/incidents", () =>
        HttpResponse.json({
          incident: { ...rawIncident, id: "inc-2", title: "New incident" },
        })
      )
    );

    renderWithProviders(<IncidentsPage />);
    await screen.findByText("Automated Incident Response");

    await userEvent.click(screen.getByRole("button", { name: /Report Incident/ }));
    const createButton = screen.getByRole("button", {
      name: /Create Incident & Launch Devin/,
    });
    expect(createButton).toBeDisabled();

    await userEvent.type(screen.getByLabelText("Incident Title"), "New incident");
    await userEvent.type(screen.getByLabelText("Description"), "Something broke");
    await userEvent.selectOptions(screen.getByLabelText("Severity"), "critical");
    await userEvent.selectOptions(
      screen.getByLabelText("Affected Service"),
      "search-service"
    );
    expect(createButton).toBeEnabled();
    await userEvent.click(createButton);

    expect(await screen.findByText("New incident")).toBeInTheDocument();
    expect(await screen.findByText(/Incident created!/)).toBeInTheDocument();
  });

  it("resolves an incident after confirmation", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () =>
        HttpResponse.json({ incidents: [rawIncident] })
      ),
      http.patch("/api/v1/admin/incidents/inc-1", () =>
        HttpResponse.json({
          incident: {
            ...rawIncident,
            status: "resolved",
            active: false,
            resolved_at: "2026-08-12T08:00:00Z",
          },
        })
      )
    );

    renderWithProviders(<IncidentsPage />);
    await screen.findByText("Search is down");

    await userEvent.click(screen.getByRole("button", { name: /Resolve/ }));
    expect(
      screen.getByText('Mark "Search is down" as resolved?')
    ).toBeInTheDocument();
    await userEvent.click(
      screen.getByRole("dialog").querySelector(".btn-raised") as HTMLElement
    );

    expect(await screen.findByText("Incident resolved")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText("resolved")).toBeInTheDocument());
  });

  it("surfaces server error details when a delete fails", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () =>
        HttpResponse.json({ incidents: [rawIncident] })
      ),
      http.delete("/api/v1/admin/incidents/inc-1", () =>
        HttpResponse.json({ details: "Incident is locked" }, { status: 422 })
      )
    );

    renderWithProviders(<IncidentsPage />);
    await screen.findByText("Search is down");

    await userEvent.click(screen.getByRole("button", { name: /Delete/ }));
    await userEvent.click(
      screen.getByRole("dialog").querySelector(".btn-raised") as HTMLElement
    );

    expect(await screen.findByText("Incident is locked")).toBeInTheDocument();
    expect(screen.getByText("Search is down")).toBeInTheDocument();
  });

  it("filters to an empty state with clear-filter action", async () => {
    mockAutoInvestigate();
    server.use(
      http.get("/api/v1/admin/incidents", () =>
        HttpResponse.json({
          incidents: [{ ...rawIncident, status: "open", active: true }],
        })
      )
    );

    renderWithProviders(<IncidentsPage />);
    await screen.findByText("Search is down");

    await userEvent.click(screen.getByText("0 Resolved"));
    expect(screen.getByText("No resolved incidents")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Clear Filter" }));
    expect(screen.getByText("Search is down")).toBeInTheDocument();
  });
});
