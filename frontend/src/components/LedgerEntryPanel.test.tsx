import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getLatestEqClose, postLedgerEntry } from "../api/client";
import { prepareEntry, type Draft } from "../lib/ledger";
import LedgerEntryPanel from "./LedgerEntryPanel";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getLatestEqClose: vi.fn(), postLedgerEntry: vi.fn() };
});

const mockedLatest = vi.mocked(getLatestEqClose);
const mockedPost = vi.mocked(postLedgerEntry);
const recorded = vi.fn();

function panel() {
  return render(<LedgerEntryPanel symbols={["INFY", "ICICIBANK"]}
    stalenessDays={5} onRecorded={recorded} />);
}

function fillBuy(price = "120.00") {
  fireEvent.change(screen.getByLabelText("Entry type"), { target: { value: "buy" } });
  fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: " infy " } });
  fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "2" } });
  fireEvent.change(screen.getByLabelText("Price per share · ₹"), { target: { value: price } });
  fireEvent.change(screen.getByLabelText("Fees · ₹"), { target: { value: "1.50" } });
  fireEvent.change(screen.getByLabelText("Trade date · Asia/Kolkata"), {
    target: { value: "2026-10-30" },
  });
}

describe("manual ledger entry", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.stubGlobal("crypto", { randomUUID: () => "stable-test-key" });
    mockedLatest.mockReset();
    mockedPost.mockReset();
    recorded.mockReset();
    mockedLatest.mockResolvedValue({
      symbol: "INFY", status: "available", price_date: "2026-10-30",
      close_paise: 10000, source_label: "Synthetic test", file_name: "test.zip",
      staleness_limit_days: 5,
    });
  });

  it("validates form values and shows confirmation with exact fees and totals", async () => {
    panel();
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    expect(screen.getByText(/Amount must be positive/)).toBeInTheDocument();
    fillBuy();
    expect(document.querySelector('datalist option[value="INFY"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    expect(await screen.findByRole("heading", { name: "Confirm entry" })).toBeInTheDocument();
    expect(screen.getByText("Total debit including fees")).toBeInTheDocument();
    expect(screen.getByText("₹241.50")).toBeInTheDocument();
    expect(screen.getByText("₹1.50")).toBeInTheDocument();
    expect(screen.getByText(/Recorded entries cannot be undone/)).toBeInTheDocument();
    expect(screen.getByText(/Latest stored EQ close: ₹100.00.*2026-10-30/)).toBeInTheDocument();
  });

  it("shows a non-blocking warning at 20% and for a stale reference", async () => {
    mockedLatest.mockResolvedValue({
      symbol: "INFY", status: "available", price_date: "2026-10-20",
      close_paise: 10000, source_label: "Synthetic test", file_name: "test.zip",
      staleness_limit_days: 5,
    });
    panel();
    fillBuy();
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    expect(await screen.findByText(/differs from the latest stored close by at least 20%/))
      .toBeInTheDocument();
    expect(screen.getByText(/older than the 5-day staleness limit/)).toBeInTheDocument();
    expect(screen.getByText(/end-of-day price date: 2026-10-20/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm and record" })).toBeEnabled();
  });

  it("warns for a symbol with no stored EQ price without blocking", async () => {
    mockedLatest.mockResolvedValue({
      symbol: "INFY", status: "no_eq_price", price_date: null,
      close_paise: null, source_label: null, file_name: null,
      staleness_limit_days: 5,
    });
    panel();
    fillBuy();
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    expect(await screen.findByText(/symbol has no stored EQ price/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Confirm and record" })).toBeEnabled();
  });

  it("persists a pending key and payload, prevents double-click, and retries after reload", async () => {
    mockedPost.mockReturnValueOnce(new Promise(() => undefined));
    const first = panel();
    fillBuy("100.00");
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    await screen.findByRole("heading", { name: "Confirm entry" });
    const confirm = screen.getByRole("button", { name: "Confirm and record" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockedPost).toHaveBeenCalledTimes(1);
    const stored = JSON.parse(localStorage.getItem("investment-agent.pending-ledger-v1") ?? "null");
    expect(stored.key).toBe("stable-test-key");
    expect(stored.entry.payload).toMatchObject({
      symbol: "INFY", quantity: 2, price_paise: 10000, fees_paise: 150,
    });
    first.unmount();
    mockedPost.mockResolvedValueOnce({ id: 1 });
    panel();
    expect(screen.getByText(/Unconfirmed submission recovered/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry same submission" }));
    await waitFor(() => expect(recorded).toHaveBeenCalledTimes(1));
    expect(mockedPost).toHaveBeenLastCalledWith("buy", stored.entry.payload, "stable-test-key");
    expect(localStorage.getItem("investment-agent.pending-ledger-v1")).toBeNull();
  });

  it("keeps a network-uncertain submission for retry", async () => {
    mockedPost.mockRejectedValue(new TypeError("network lost"));
    panel();
    fireEvent.change(screen.getByLabelText("Amount · ₹"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and record" }));
    expect(await screen.findByText(/Outcome unknown/)).toBeInTheDocument();
    expect(localStorage.getItem("investment-agent.pending-ledger-v1")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Retry same submission" })).toBeEnabled();
  });

  it("treats an HTTP timeout as uncertain and keeps the same key", async () => {
    mockedPost.mockRejectedValue(new ApiError(408, "request timeout"));
    panel();
    fireEvent.change(screen.getByLabelText("Amount · ₹"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and record" }));
    expect(await screen.findByText(/Outcome unknown/)).toBeInTheDocument();
    expect(localStorage.getItem("investment-agent.pending-ledger-v1")).not.toBeNull();
  });

  it.each([409, 422])("shows definite %i errors and clears pending state", async (status) => {
    mockedPost.mockRejectedValue(new ApiError(status, "rejected detail"));
    panel();
    fireEvent.change(screen.getByLabelText("Amount · ₹"), { target: { value: "100" } });
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    fireEvent.click(screen.getByRole("button", { name: "Confirm and record" }));
    expect(await screen.findByText(/rejected detail/)).toBeInTheDocument();
    expect(localStorage.getItem("investment-agent.pending-ledger-v1")).toBeNull();
  });

  it("uses the same confirmation basis for a restored payload", () => {
    const draft: Draft = {
      kind: "withdrawal", amount: "125.25", symbol: "", quantity: "",
      price: "", fees: "0", date: "2026-10-30", note: "",
    };
    const entry = prepareEntry(draft, "2026-11-03").entry;
    localStorage.setItem("investment-agent.pending-ledger-v1", JSON.stringify({
      key: "prior-key", entry,
    }));
    panel();
    expect(screen.getByText("Amount")).toBeInTheDocument();
    expect(screen.getByText("₹125.25")).toBeInTheDocument();
    expect(screen.getByText(/Recorded entries cannot be undone/)).toBeInTheDocument();
  });

  it("confirms a withdrawal and exact net sell proceeds", async () => {
    const first = panel();
    fireEvent.change(screen.getByLabelText("Entry type"), {
      target: { value: "withdrawal" },
    });
    fireEvent.change(screen.getByLabelText("Amount · ₹"), {
      target: { value: "125.25" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    expect(screen.getByText("₹125.25")).toBeInTheDocument();
    first.unmount();
    panel();
    fireEvent.change(screen.getByLabelText("Entry type"), { target: { value: "sell" } });
    fireEvent.change(screen.getByLabelText("Symbol"), { target: { value: "INFY" } });
    fireEvent.change(screen.getByLabelText("Quantity"), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Price per share · ₹"), {
      target: { value: "120.00" },
    });
    fireEvent.change(screen.getByLabelText("Fees · ₹"), { target: { value: "1.50" } });
    fireEvent.click(screen.getByRole("button", { name: "Review entry" }));
    expect(await screen.findByText("Net sale proceeds after fees")).toBeInTheDocument();
    expect(screen.getByText("₹238.50")).toBeInTheDocument();
  });
});
