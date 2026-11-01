import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { getHealth } from "./api/client";

vi.mock("./api/client", () => ({
  getHealth: vi.fn(),
}));

const mockedGetHealth = vi.mocked(getHealth);

function expectNoNumericFinancialFigures() {
  expect(document.body.textContent).not.toMatch(/\d/);
}

describe("App", () => {
  beforeEach(() => {
    mockedGetHealth.mockReset();
  });

  it("renders all seven dashboard sections without market or portfolio figures", () => {
    mockedGetHealth.mockReturnValue(new Promise(() => undefined));
    render(<App />);

    for (const section of [
      "Market overview",
      "Portfolio summary",
      "Intraday engine",
      "Swing engine",
      "Long-term engine",
      "Risk manager",
      "Recent activity",
    ]) {
      expect(screen.getByRole("heading", { name: section })).toBeInTheDocument();
    }

    expect(screen.getByText("Backend: Loading")).toBeInTheDocument();
    expectNoNumericFinancialFigures();
  });

  it("shows connected when the health API responds", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Backend: Connected")).toBeInTheDocument();
    });
    expectNoNumericFinancialFigures();
  });

  it("shows unreachable when the health API fails", async () => {
    mockedGetHealth.mockRejectedValue(new Error("backend unavailable"));
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Backend: Unreachable")).toBeInTheDocument();
    });
    expectNoNumericFinancialFigures();
  });
});
