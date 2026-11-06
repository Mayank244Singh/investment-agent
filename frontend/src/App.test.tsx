import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  getHealth,
  getLedger,
  getLatestEqClose,
  getPortfolioValuation,
  getPriceCoverage,
  getRealizedPnl,
  getRiskRules,
  postLedgerEntry,
  type LedgerResponse,
  type PortfolioValuationResponse,
  type PriceCoverageResponse,
  type RiskRulesResponse,
} from "./api/client";

vi.mock("./api/client", () => ({
  getHealth: vi.fn(),
  getLedger: vi.fn(),
  getLatestEqClose: vi.fn(),
  getPortfolioValuation: vi.fn(),
  getPriceCoverage: vi.fn(),
  getRealizedPnl: vi.fn(),
  getRiskRules: vi.fn(),
  postLedgerEntry: vi.fn(),
}));

const mockedGetHealth = vi.mocked(getHealth);
const mockedGetLedger = vi.mocked(getLedger);
const mockedGetPortfolioValuation = vi.mocked(getPortfolioValuation);
const mockedGetPriceCoverage = vi.mocked(getPriceCoverage);
const mockedGetRiskRules = vi.mocked(getRiskRules);
const mockedGetRealizedPnl = vi.mocked(getRealizedPnl);
const mockedGetLatestEqClose = vi.mocked(getLatestEqClose);
const mockedPostLedgerEntry = vi.mocked(postLedgerEntry);

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

const emptyValuation: PortfolioValuationResponse = {
  as_of_date: "2026-10-30",
  staleness_limit_days: 5,
  holdings: [],
  totals: {
    priced_holdings_count: 0,
    unpriced_holdings_count: 0,
    priced_cost_basis_paise: 0,
    priced_market_value_paise: 0,
    priced_unrealized_pnl_paise: 0,
    unpriced_cost_basis_paise: 0,
    total_open_cost_basis_paise: 0,
  },
};

const emptyCoverage: PriceCoverageResponse = {
  symbols: [],
  import_batches: [],
};

const pricedValuation: PortfolioValuationResponse = {
  ...emptyValuation,
  holdings: [{
    symbol: "TESTA",
    quantity: 1,
    cost_basis_paise: 10_000_000,
    status: "priced",
    unavailable_reason: null,
    last_price_date: "2026-10-30",
    price_age_days: 0,
    close_paise: 12_345_678,
    market_value_paise: 12_345_678,
    unrealized_pnl_paise: 2_345_678,
    source_label: "Synthetic source",
    file_name: "synthetic.zip",
  }],
  totals: {
    priced_holdings_count: 1,
    unpriced_holdings_count: 0,
    priced_cost_basis_paise: 10_000_000,
    priced_market_value_paise: 12_345_678,
    priced_unrealized_pnl_paise: 2_345_678,
    unpriced_cost_basis_paise: 0,
    total_open_cost_basis_paise: 10_000_000,
  },
};

const populatedCoverage: PriceCoverageResponse = {
  symbols: [{ symbol: "TESTA", series: "EQ", latest_price_date: "2026-10-30" }],
  import_batches: [{
    id: 1,
    file_name: "synthetic.zip",
    source_label: "Synthetic source",
    total_rows: 1,
    imported_rows: 1,
    skipped_rows: 0,
    rejected_rows: 0,
    status: "completed",
    imported_at_utc: "2026-10-30T17:00:00Z",
  }],
};

describe("App", () => {
  beforeEach(() => {
    mockedGetHealth.mockReset();
    mockedGetLedger.mockReset();
    mockedGetPortfolioValuation.mockReset();
    mockedGetPriceCoverage.mockReset();
    mockedGetRiskRules.mockReset();
    mockedGetRealizedPnl.mockReset();
    mockedGetLatestEqClose.mockReset();
    mockedPostLedgerEntry.mockReset();
    localStorage.clear();
    mockedGetPortfolioValuation.mockResolvedValue(emptyValuation);
    mockedGetPriceCoverage.mockResolvedValue(emptyCoverage);
    mockedGetRealizedPnl.mockResolvedValue({
      from_date: null, to_date: null, financial_year: null,
      symbols: [], matches: [], total_realized_pnl_paise: 0,
    });
  });

  it("renders all seven sections and loading states", () => {
    mockedGetHealth.mockReturnValue(new Promise(() => undefined));
    mockedGetLedger.mockReturnValue(new Promise(() => undefined));
    mockedGetPortfolioValuation.mockReturnValue(new Promise(() => undefined));
    mockedGetPriceCoverage.mockReturnValue(new Promise(() => undefined));
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
    expect(screen.getAllByText("Loading")).toHaveLength(4);
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
    expect(screen.getAllByText("No data")).toHaveLength(4);
    expect(document.querySelectorAll("dd")).toHaveLength(0);
    expect(screen.queryAllByTestId("price-figure")).toHaveLength(0);
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
    mockedGetPortfolioValuation.mockRejectedValue(new Error("valuation unavailable"));
    mockedGetPriceCoverage.mockRejectedValue(new Error("coverage unavailable"));
    mockedGetRiskRules.mockRejectedValue(new Error("rules unavailable"));
    render(<App />);

    await waitFor(() => {
      expect(screen.getByText("Backend: Unreachable")).toBeInTheDocument();
    });
    expect(screen.getAllByText("Unavailable")).toHaveLength(4);
    expect(screen.getByText("Error loading valuation")).toBeInTheDocument();
    expect(screen.getByText("Error loading price coverage")).toBeInTheDocument();
  });

  it("shows fully priced holdings with end-of-day dates and Indian grouping", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue(emptyLedger);
    mockedGetRiskRules.mockResolvedValue({ rules: [], daily_loss_timezone: "Asia/Kolkata" });
    mockedGetPortfolioValuation.mockResolvedValue(pricedValuation);
    mockedGetPriceCoverage.mockResolvedValue(populatedCoverage);
    render(<App />);

    expect(await screen.findAllByText("₹1,23,456.78")).toHaveLength(3);
    expect(screen.getAllByText("₹23,456.78")).toHaveLength(2);
    expect(screen.getByText(/Synthetic source/)).toBeInTheDocument();
    expect(screen.getByText(/synthetic.zip/)).toBeInTheDocument();
    for (const figure of screen.getAllByTestId("price-figure")) {
      expect(figure.textContent).toContain("End-of-day price date:");
      expect(figure.textContent).toContain("2026-10-30");
    }
  });

  it("keeps unpriced cost separate in a partially priced portfolio", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue(emptyLedger);
    mockedGetRiskRules.mockResolvedValue({ rules: [], daily_loss_timezone: "Asia/Kolkata" });
    mockedGetPortfolioValuation.mockResolvedValue({
      ...pricedValuation,
      holdings: [...pricedValuation.holdings, {
        symbol: "TESTB",
        quantity: 2,
        cost_basis_paise: 20_000,
        status: "unavailable",
        unavailable_reason: "no_eq_price",
        last_price_date: null,
        price_age_days: null,
        close_paise: null,
        market_value_paise: null,
        unrealized_pnl_paise: null,
        source_label: null,
        file_name: null,
      }],
      totals: {
        ...pricedValuation.totals,
        unpriced_holdings_count: 1,
        unpriced_cost_basis_paise: 20_000,
        total_open_cost_basis_paise: 10_020_000,
      },
    });
    render(<App />);

    expect(await screen.findByText("Unpriced cost basis")).toBeInTheDocument();
    expect(screen.getByText("₹200.00")).toBeInTheDocument();
    expect(screen.getByText(/Unavailable: no EQ end-of-day price/)).toBeInTheDocument();
    expect(screen.getByText(/Last price date: None/)).toBeInTheDocument();
    expect(screen.queryByText("₹0.00")).not.toBeInTheDocument();
  });

  it("shows stale prices as unavailable with a reason and last date", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue(emptyLedger);
    mockedGetRiskRules.mockResolvedValue({ rules: [], daily_loss_timezone: "Asia/Kolkata" });
    mockedGetPortfolioValuation.mockResolvedValue({
      ...emptyValuation,
      as_of_date: "2026-11-05",
      holdings: [{
        symbol: "TESTA",
        quantity: 1,
        cost_basis_paise: 10_000,
        status: "unavailable",
        unavailable_reason: "stale_price",
        last_price_date: "2026-10-30",
        price_age_days: 6,
        close_paise: null,
        market_value_paise: null,
        unrealized_pnl_paise: null,
        source_label: "Synthetic source",
        file_name: "synthetic.zip",
      }],
      totals: {
        ...emptyValuation.totals,
        unpriced_holdings_count: 1,
        unpriced_cost_basis_paise: 10_000,
        total_open_cost_basis_paise: 10_000,
      },
    });
    render(<App />);

    expect(await screen.findByText(/last end-of-day price is stale/)).toBeInTheDocument();
    expect(screen.getByText(/Last price date: 2026-10-30/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("price-figure")).toHaveLength(0);
  });

  it("shows an unavailable state for no price before the as-of date", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue(emptyLedger);
    mockedGetRiskRules.mockResolvedValue({ rules: [], daily_loss_timezone: "Asia/Kolkata" });
    mockedGetPortfolioValuation.mockResolvedValue({
      ...emptyValuation,
      holdings: [{
        symbol: "TESTA",
        quantity: 1,
        cost_basis_paise: 10_000,
        status: "unavailable",
        unavailable_reason: "no_price_on_or_before_as_of",
        last_price_date: null,
        price_age_days: null,
        close_paise: null,
        market_value_paise: null,
        unrealized_pnl_paise: null,
        source_label: null,
        file_name: null,
      }],
      totals: {
        ...emptyValuation.totals,
        unpriced_holdings_count: 1,
        unpriced_cost_basis_paise: 10_000,
        total_open_cost_basis_paise: 10_000,
      },
    });
    render(<App />);

    expect(await screen.findByText(/no end-of-day price on or before/)).toBeInTheDocument();
    expect(screen.queryAllByTestId("price-figure")).toHaveLength(0);
  });

  it("never shows a price figure without its end-of-day price date", async () => {
    mockedGetHealth.mockResolvedValue({ status: "ok", database: "ok" });
    mockedGetLedger.mockResolvedValue(emptyLedger);
    mockedGetRiskRules.mockResolvedValue({ rules: [], daily_loss_timezone: "Asia/Kolkata" });
    mockedGetPortfolioValuation.mockResolvedValue(pricedValuation);
    render(<App />);

    await screen.findAllByText("₹1,23,456.78");
    const heading = screen.getByRole("heading", { name: "End-of-day valuation" });
    const valuationCard = heading.closest("article");
    expect(valuationCard).not.toBeNull();
    const priceAmounts = valuationCard?.querySelectorAll("dd") ?? [];
    expect(priceAmounts).toHaveLength(7);
    for (const amount of priceAmounts) {
      if (amount.textContent?.includes("₹")) {
        expect(amount.parentElement?.textContent).toMatch(
          /End-of-day price date:.*2026-10-30/,
        );
      }
    }
  });
});
