import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  getHealth,
  getLedger,
  getRiskRules,
  type LedgerResponse,
  type RiskRulesResponse,
} from "./api/client";

vi.mock("./api/client", () => ({
  getHealth: vi.fn(),
  getLedger: vi.fn(),
  getRiskRules: vi.fn(),
}));

const mockedGetHealth = vi.mocked(getHealth);
const mockedGetLedger = vi.mocked(getLedger);
const mockedGetRiskRules = vi.mocked(getRiskRules);

const emptyLedger: LedgerResponse = {
  summary: {
    cash_balance_paise: 0,
    deployable_cash_paise: 0,
    open_cost_basis_paise: 0,
    realized_pnl_paise: 0,
    profit_vault_balance_paise: 0,
    risk_capital_paise: 0,
  },
  holdings: [],
  capital_movements: [],
  transactions: [],
  lots: [],
  realizations: [],
  profit_vault_entries: [],
};

const configuredRules: RiskRulesResponse = {
  rules: [
    {
      code: "max_order_size",
      label: "Maximum order size",
      status: "configured",
      limit_percent: "10",
    },
    {
      code: "stop_loss_required",
      label: "Stop-loss required for buys",
      status: "configured",
      limit_percent: null,
    },
  ],
  daily_loss_timezone: "Asia/Kolkata",
};

describe("App", () => {
  beforeEach(() => {
    mockedGetHealth.mockReset();
    mockedGetLedger.mockReset();
    mockedGetRiskRules.mockReset();
  });

  it("renders all seven sections and loading states", () => {
    mockedGetHealth.mockReturnValue(new Promise(() => undefined));
    mockedGetLedger.mockReturnValue(new Promise(() => undefined));
    mockedGetRiskRules.mockReturnValue(new Promise(() => undefined));
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
    expect(screen.getAllByText("Loading")).toHaveLength(2);
  });

  it("shows No data for empty API responses without numeric portfolio figures", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue(emptyLedger);
    mockedGetRiskRules.mockResolvedValue({
      rules: [],
      daily_loss_timezone: "Asia/Kolkata",
    });
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Backend: Connected")).toBeInTheDocument();
    });
    expect(screen.getAllByText("No data")).toHaveLength(2);
    expect(document.body.textContent).not.toMatch(/₹|\b\d+(?:\.\d+)?%/);
  });

  it("shows populated portfolio totals and configured risk rules", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue({
      ...emptyLedger,
      summary: {
        cash_balance_paise: 95_860,
        deployable_cash_paise: 93_940,
        open_cost_basis_paise: 6_060,
        realized_pnl_paise: 1_920,
        profit_vault_balance_paise: 1_920,
        risk_capital_paise: 98_080,
      },
      capital_movements: [{}],
      transactions: [{}, {}],
    });
    mockedGetRiskRules.mockResolvedValue(configuredRules);
    render(<App />);

    expect(await screen.findByText("₹958.60")).toBeInTheDocument();
    expect(screen.getByText("₹939.40")).toBeInTheDocument();
    expect(screen.getByText("₹60.60")).toBeInTheDocument();
    expect(screen.getAllByText("₹19.20")).toHaveLength(2);
    expect(screen.getByText("Maximum order size")).toBeInTheDocument();
    expect(screen.getByText("10%")).toBeInTheDocument();
    expect(screen.getByText("Configured")).toBeInTheDocument();
    expect(screen.getByText("Daily reset: Asia/Kolkata")).toBeInTheDocument();
  });

  it("shows unreachable and unavailable states when APIs fail", async () => {
    mockedGetHealth.mockRejectedValue(new Error("backend unavailable"));
    mockedGetLedger.mockRejectedValue(new Error("ledger unavailable"));
    mockedGetRiskRules.mockRejectedValue(new Error("rules unavailable"));
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Backend: Unreachable")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Unavailable")).toHaveLength(4);
  });
});
