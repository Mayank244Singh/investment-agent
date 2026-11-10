import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

import {
  getVirtualAccount, getVirtualOrders, getVirtualQuotes,
  previewVirtualOrder, submitVirtualOrder,
  type VirtualAccount, type VirtualPreview,
} from "../api/client";
import VirtualTradingPanel from "./VirtualTradingPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getVirtualAccount: vi.fn(), getVirtualOrders: vi.fn(),
    getVirtualQuotes: vi.fn(), previewVirtualOrder: vi.fn(), submitVirtualOrder: vi.fn() };
});

const account: VirtualAccount = {
  mode: "VIRTUAL TRADING — SYNTHETIC DEMO — NOT MARKET DATA",
  currency: "INR", initial_capital_paise: 1_000_000,
  cash_balance_paise: 1_000_000, deployable_cash_paise: 1_000_000,
  realized_pnl_paise: 0, profit_vault_balance_paise: 0, holdings: [],
};
const preview: VirtualPreview = {
  approved: true, reasons: [], side: "buy", symbol: "INFY", quantity: 1,
  quote_date: "2026-11-09", source_label: "SYNTHETIC DEMO — NOT MARKET DATA",
  reference_paise: 8_000, fill_paise: 8_008, fees_paise: 20,
  charges: { brokerage_paise: 10 }, planned_loss_paise: 200,
  cash_impact_paise: -8_028,
  stop_warning: "Stop price does not guarantee the maximum realised loss.",
};

beforeEach(() => {
  localStorage.clear();
  vi.stubGlobal("crypto", { randomUUID: () => "virtual-test-key" });
  vi.mocked(getVirtualAccount).mockReset().mockResolvedValue(account);
  vi.mocked(getVirtualQuotes).mockReset().mockResolvedValue([{
    symbol: "INFY", price_paise: 8_000, price_date: "2026-11-09",
    source_label: "SYNTHETIC DEMO — NOT MARKET DATA", fixture_sha256: "fixture",
  }]);
  vi.mocked(getVirtualOrders).mockReset().mockResolvedValue([]);
  vi.mocked(previewVirtualOrder).mockReset().mockResolvedValue(preview);
  vi.mocked(submitVirtualOrder).mockReset();
});

function fillTicket() {
  fireEvent.change(screen.getByLabelText("Planned stop · ₹"), { target: { value: "79.00" } });
}

it("labels synthetic cash and dated prices, and preview does not submit", async () => {
  render(<VirtualTradingPanel />);
  expect(await screen.findByText(/Cash ₹10,000.00/)).toBeInTheDocument();
  expect(screen.getByText(/SYNTHETIC DEMO — NOT MARKET DATA/)).toBeInTheDocument();
  expect(screen.getByText(/Synthetic reference ₹80.00 · price date 2026-11-09/)).toBeInTheDocument();
  fillTicket();
  fireEvent.click(screen.getByRole("button", { name: "Preview simulated order" }));
  expect(await screen.findByText(/Estimated fill ₹80.08 · fees ₹0.20/)).toBeInTheDocument();
  expect(screen.getByText(/Stop price does not guarantee/)).toBeInTheDocument();
  expect(submitVirtualOrder).not.toHaveBeenCalled();
});

it("shows risk reasons and prevents rejected preview submission", async () => {
  vi.mocked(previewVirtualOrder).mockResolvedValue({
    ...preview, approved: false,
    reasons: [{ code: "virtual_planned_risk", message: "Planned loss too high" }],
  });
  render(<VirtualTradingPanel />);
  fillTicket();
  fireEvent.click(screen.getByRole("button", { name: "Preview simulated order" }));
  expect(await screen.findByText(/virtual_planned_risk: Planned loss too high/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Confirm simulated fill" })).not.toBeInTheDocument();
});

it("confirms once with persisted key and refreshes the virtual account", async () => {
  vi.mocked(submitVirtualOrder).mockResolvedValue({
    id: 1, status: "filled", side: "buy", symbol: "INFY", quantity: 1,
    attempted_at_utc: "2026-11-09T07:00:00Z", quote_date: "2026-11-09",
    fill_paise: 8_008, fees_paise: 20, reasons: [], ledger_transaction_id: 1,
    source_label: "SYNTHETIC DEMO — NOT MARKET DATA", account,
  });
  render(<VirtualTradingPanel />);
  fillTicket();
  fireEvent.click(screen.getByRole("button", { name: "Preview simulated order" }));
  fireEvent.click(await screen.findByRole("button", { name: "Confirm simulated fill" }));
  await waitFor(() => expect(submitVirtualOrder).toHaveBeenCalledTimes(1));
  expect(vi.mocked(submitVirtualOrder).mock.calls[0][1]).toBe("virtual-test-key");
  await waitFor(() => expect(localStorage.getItem("investment-agent.pending-virtual-v1")).toBeNull());
  expect(await screen.findByText(/Simulated buy filled once/)).toBeInTheDocument();
});

it("recovers an uncertain submission and retries the same payload and key", async () => {
  const order = { side: "buy", symbol: "INFY", quantity: 1,
    stop_loss_paise: 7_900, order_type: "market" };
  localStorage.setItem("investment-agent.pending-virtual-v1", JSON.stringify({ key: "saved-key", order }));
  vi.mocked(submitVirtualOrder).mockRejectedValue(new TypeError("network failed"));
  render(<VirtualTradingPanel />);
  fireEvent.click(screen.getByRole("button", { name: "Retry same key" }));
  await waitFor(() => expect(submitVirtualOrder).toHaveBeenCalledWith(order, "saved-key"));
  expect(await screen.findByText(/Outcome unknown/)).toBeInTheDocument();
  expect(localStorage.getItem("investment-agent.pending-virtual-v1")).not.toBeNull();
});

it("shows unavailable state when the isolated virtual database is not seeded", async () => {
  vi.mocked(getVirtualAccount).mockRejectedValue(new Error("not seeded"));
  render(<VirtualTradingPanel />);
  expect(await screen.findByText(/Virtual account unavailable/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Preview simulated order" })).toBeDisabled();
});
