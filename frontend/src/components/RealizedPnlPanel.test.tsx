import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import { getRealizedPnl, type RealizedPnlReportResponse } from "../api/client";
import RealizedPnlPanel from "./RealizedPnlPanel";

vi.mock("../api/client", () => ({ getRealizedPnl: vi.fn() }));
const mockedReport = vi.mocked(getRealizedPnl);

const report: RealizedPnlReportResponse = {
  from_date: null, to_date: null, financial_year: null,
  total_realized_pnl_paise: 480,
  symbols: [{
    symbol: "TESTA", quantity_sold: 1, cost_basis_paise: 1010,
    gross_proceeds_paise: 1500, sell_fees_paise: 10,
    net_proceeds_paise: 1490, realized_pnl_paise: 480,
    shortest_holding_period_days: 1, longest_holding_period_days: 1,
  }],
  matches: [{
    sell_transaction_id: 2, buy_transaction_id: 1, holding_lot_id: 1,
    symbol: "TESTA", quantity: 1,
    acquired_at_utc: "2026-03-31T04:30:00Z",
    realized_at_utc: "2026-04-01T04:30:00Z", holding_period_days: 1,
    cost_basis_paise: 1010, gross_proceeds_paise: 1500,
    sell_fees_paise: 10, net_proceeds_paise: 1490, realized_pnl_paise: 480,
  }],
};

beforeEach(() => mockedReport.mockReset());

it("shows dated FIFO basis, holding days, filters, and proxy CSV links", async () => {
  mockedReport.mockResolvedValue(report);
  render(<RealizedPnlPanel revision={0} />);
  const summary = await screen.findByRole("table", { name: /Realised P&L by symbol/ });
  expect(within(summary).getByText("FIFO cost")).toBeInTheDocument();
  expect(within(summary).getByText("Net proceeds")).toBeInTheDocument();
  expect(screen.getByText(/Total realised P&L from FIFO matches: ₹4.80/))
    .toBeInTheDocument();
  expect(screen.getByText(/No tax calculation or tax claim/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Download ledger CSV" }))
    .toHaveAttribute("href", "/api/v1/portfolio/ledger.csv");
  expect(screen.getByRole("link", { name: "Download FIFO matches CSV" }))
    .toHaveAttribute("href", "/api/v1/portfolio/fifo-matches.csv");
  fireEvent.change(screen.getByLabelText("Financial year starting April"), {
    target: { value: "2026" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  await waitFor(() => expect(mockedReport).toHaveBeenLastCalledWith({ financial_year: 2026 }));
  expect(screen.getByRole("link", { name: "Download FIFO matches CSV" }))
    .toHaveAttribute("href", "/api/v1/portfolio/fifo-matches.csv?financial_year=2026");
});

it("shows no realised-money figure without its FIFO basis and sale dates", async () => {
  mockedReport.mockResolvedValue(report);
  render(<RealizedPnlPanel revision={0} />);
  const summary = await screen.findByRole("table", { name: /Realised P&L by symbol/ });
  const headers = within(summary).getAllByRole("columnheader").map((item) => item.textContent);
  expect(headers).toEqual(expect.arrayContaining([
    "FIFO cost", "Gross proceeds", "Sell fees", "Net proceeds", "Realised P&L",
  ]));
  const details = screen.getByRole("table", { name: /Each matched buy lot and sell/ });
  expect(within(details).getByText("2026-03-31")).toBeInTheDocument();
  expect(within(details).getByText("2026-04-01")).toBeInTheDocument();
  expect(within(details).getByText("₹10.10")).toBeInTheDocument();
  expect(within(details).getByText("₹14.90")).toBeInTheDocument();
});

it("shows empty and error report states", async () => {
  mockedReport.mockResolvedValueOnce({ ...report, symbols: [], matches: [], total_realized_pnl_paise: 0 })
    .mockRejectedValueOnce(new Error("offline"));
  render(<RealizedPnlPanel revision={0} />);
  expect(await screen.findByText(/No realised sales for this period/)).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("From · Asia/Kolkata"), {
    target: { value: "2026-04-01" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  expect(await screen.findByText(/Error loading realised P&L report/)).toBeInTheDocument();
});

it("rejects mixed financial year and date filters before calling the API", async () => {
  mockedReport.mockResolvedValue(report);
  render(<RealizedPnlPanel revision={0} />);
  await screen.findByRole("table", { name: /Realised P&L by symbol/ });
  fireEvent.change(screen.getByLabelText("From · Asia/Kolkata"), {
    target: { value: "2026-04-01" },
  });
  fireEvent.change(screen.getByLabelText("Financial year starting April"), {
    target: { value: "2026" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  expect(screen.getByText(/Choose a financial year or a date range/)).toBeInTheDocument();
  expect(mockedReport).toHaveBeenCalledTimes(1);
});

it("applies an inclusive date range to the report and FIFO export", async () => {
  mockedReport.mockResolvedValue(report);
  render(<RealizedPnlPanel revision={0} />);
  await screen.findByRole("table", { name: /Realised P&L by symbol/ });
  fireEvent.change(screen.getByLabelText("From · Asia/Kolkata"), {
    target: { value: "2026-04-01" },
  });
  fireEvent.change(screen.getByLabelText("To · Asia/Kolkata"), {
    target: { value: "2026-04-30" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply filters" }));
  await waitFor(() => expect(mockedReport).toHaveBeenLastCalledWith({
    from: "2026-04-01", to: "2026-04-30",
  }));
  expect(screen.getByRole("link", { name: "Download FIFO matches CSV" }))
    .toHaveAttribute("href", "/api/v1/portfolio/fifo-matches.csv?from=2026-04-01&to=2026-04-30");
});
