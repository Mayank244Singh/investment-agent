import { describe, expect, it } from "vitest";

import {
  indiaToday, normalizeSymbol, parseRupeesToPaise, prepareEntry,
  priceWarning, warningThreshold, type Draft,
} from "./ledger";

const buy: Draft = {
  kind: "buy", symbol: " infy ", quantity: "10", price: "1150.00",
  fees: "20.00", amount: "", date: "2026-10-30", note: "",
};

describe("ledger entry calculations", () => {
  it("converts rupees to integer paise without rounding or floats", () => {
    expect(parseRupeesToPaise("1,000")).toBeNull();
    expect(parseRupeesToPaise("1234.56")).toBe(123456);
    expect(parseRupeesToPaise("0.01")).toBe(1);
    expect(parseRupeesToPaise("0")).toBeNull();
    expect(parseRupeesToPaise("0", true)).toBe(0);
    expect(parseRupeesToPaise("1.234")).toBeNull();
    expect(parseRupeesToPaise("-1")).toBeNull();
    expect(parseRupeesToPaise("90071992547410.00")).toBeNull();
  });

  it("prepares exact buy and sell totals including fees", () => {
    const prepared = prepareEntry(buy, "2026-11-03").entry;
    expect(prepared?.payload).toEqual({
      symbol: "INFY", quantity: 10, price_paise: 115000, fees_paise: 2000,
      transacted_at_utc: "2026-10-30T00:00:00+05:30", note: null,
    });
    expect(prepared?.total_paise).toBe(1152000);
    expect(prepareEntry({ ...buy, kind: "sell" }, "2026-11-03").entry?.total_paise)
      .toBe(1148000);
  });

  it("validates whole quantity, symbol, amount, fees, and India dates", () => {
    expect(normalizeSymbol(" infy ")).toBe("INFY");
    expect(normalizeSymbol("BAD SYMBOL")).toBeNull();
    for (const changes of [
      { quantity: "1.5" }, { quantity: "0" }, { symbol: "bad symbol" },
      { price: "0" }, { fees: "-1" }, { date: "2026-11-04" },
    ]) {
      expect(prepareEntry({ ...buy, ...changes }, "2026-11-03").entry).toBeNull();
    }
    expect(prepareEntry({ ...buy, date: "2026-11-03" }, "2026-11-03").entry)
      .not.toBeNull();
  });

  it("resolves the day at midnight Asia/Kolkata", () => {
    expect(indiaToday(new Date("2026-11-02T18:29:00Z"))).toBe("2026-11-02");
    expect(indiaToday(new Date("2026-11-02T18:31:00Z"))).toBe("2026-11-03");
  });

  it("warns at the exact configurable 20% boundary and for stale closes", () => {
    const reference = { close_paise: 10000, price_date: "2026-10-30" };
    expect(priceWarning(11999, reference, "2026-11-03", 5, 20).far).toBe(false);
    expect(priceWarning(12000, reference, "2026-11-03", 5, 20).far).toBe(true);
    expect(priceWarning(8000, reference, "2026-11-03", 5, 20).far).toBe(true);
    expect(priceWarning(10000, reference, "2026-11-04", 5, 20).stale).toBe(false);
    expect(priceWarning(10000, reference, "2026-11-05", 5, 20).stale).toBe(true);
    expect(priceWarning(10000, null, "2026-11-05", 5, 20)).toEqual({
      far: false, stale: false,
    });
    expect(warningThreshold(undefined)).toBe(20);
    expect(warningThreshold("25")).toBe(25);
    expect(() => warningThreshold("0")).toThrow();
  });
});
