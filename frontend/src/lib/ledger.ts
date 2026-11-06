export type EntryKind = "deposit" | "withdrawal" | "buy" | "sell";

export type CapitalPayload = {
  amount_paise: number;
  occurred_at_utc: string;
  note: string | null;
};

export type TradePayload = {
  symbol: string;
  quantity: number;
  price_paise: number;
  fees_paise: number;
  transacted_at_utc: string;
  note: string | null;
};

export type EntryPayload = CapitalPayload | TradePayload;

export type Draft = {
  kind: EntryKind;
  amount: string;
  symbol: string;
  quantity: string;
  price: string;
  fees: string;
  date: string;
  note: string;
};

export type PreparedEntry = {
  kind: EntryKind;
  payload: EntryPayload;
  gross_paise: number;
  fees_paise: number;
  total_paise: number;
};

export function parseRupeesToPaise(value: string, allowZero = false): number | null {
  const trimmed = value.trim();
  if (!/^(0|[1-9]\d*)(?:\.(\d{1,2}))?$/.test(trimmed)) return null;
  const [rupees, fraction = ""] = trimmed.split(".");
  const paise = BigInt(rupees) * 100n + BigInt(fraction.padEnd(2, "0") || "0");
  if ((!allowZero && paise === 0n) || paise > BigInt(Number.MAX_SAFE_INTEGER)) return null;
  return Number(paise);
}

export function formatPaise(paise: number): string {
  if (!Number.isSafeInteger(paise)) return "Unavailable";
  const value = BigInt(paise);
  const negative = value < 0n;
  const absolute = negative ? -value : value;
  const digits = (absolute / 100n).toString();
  const grouped = digits.length <= 3
    ? digits
    : `${digits.slice(0, -3).replace(/\B(?=(\d{2})+(?!\d))/g, ",")},${digits.slice(-3)}`;
  const paiseDigits = (absolute % 100n).toString().padStart(2, "0");
  return `${negative ? "-" : ""}₹${grouped}.${paiseDigits}`;
}

export function normalizeSymbol(value: string): string | null {
  const symbol = value.trim().toUpperCase();
  return symbol.length <= 20 && /^[A-Z0-9][A-Z0-9.&-]*$/.test(symbol)
    ? symbol
    : null;
}

export function indiaToday(now = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(now);
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function validDate(value: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toISOString().slice(0, 10) === value;
}

export function prepareEntry(draft: Draft, today = indiaToday()):
  | { entry: PreparedEntry; errors: [] }
  | { entry: null; errors: string[] } {
  const errors: string[] = [];
  if (!validDate(draft.date) || draft.date > today) {
    errors.push("Choose a valid date on or before today in Asia/Kolkata.");
  }
  const timestamp = `${draft.date}T00:00:00+05:30`;
  const note = draft.note || null;
  if (draft.kind === "deposit" || draft.kind === "withdrawal") {
    const amount = parseRupeesToPaise(draft.amount);
    if (amount === null) errors.push("Amount must be positive rupees with at most two decimals.");
    if (errors.length || amount === null) return { entry: null, errors };
    return {
      entry: {
        kind: draft.kind,
        payload: { amount_paise: amount, occurred_at_utc: timestamp, note },
        gross_paise: amount, fees_paise: 0, total_paise: amount,
      },
      errors: [],
    };
  }
  const symbol = normalizeSymbol(draft.symbol);
  if (symbol === null) errors.push("Enter a valid NSE symbol.");
  const quantity = /^\d+$/.test(draft.quantity) ? Number(draft.quantity) : NaN;
  if (!Number.isSafeInteger(quantity) || quantity <= 0) {
    errors.push("Quantity must be a positive whole number.");
  }
  const price = parseRupeesToPaise(draft.price);
  if (price === null) errors.push("Price must be positive rupees with at most two decimals.");
  const fees = parseRupeesToPaise(draft.fees || "0", true);
  if (fees === null) errors.push("Fees must be non-negative rupees with at most two decimals.");
  if (errors.length || symbol === null || price === null || fees === null) {
    return { entry: null, errors };
  }
  const gross = BigInt(quantity) * BigInt(price);
  const total = draft.kind === "buy" ? gross + BigInt(fees) : gross - BigInt(fees);
  if (gross > BigInt(Number.MAX_SAFE_INTEGER) || total < 0n ||
      total > BigInt(Number.MAX_SAFE_INTEGER)) {
    return { entry: null, errors: ["Trade total is outside the supported paise range."] };
  }
  return {
    entry: {
      kind: draft.kind,
      payload: {
        symbol, quantity, price_paise: price, fees_paise: fees,
        transacted_at_utc: timestamp, note,
      },
      gross_paise: Number(gross), fees_paise: fees, total_paise: Number(total),
    },
    errors: [],
  };
}

export type PriceReference = { close_paise: number; price_date: string };

export function priceWarning(
  entryPricePaise: number,
  reference: PriceReference | null,
  asOfDate: string,
  stalenessDays: number,
  thresholdPercent: number,
): { far: boolean; stale: boolean } {
  if (!Number.isInteger(thresholdPercent) || thresholdPercent <= 0 || thresholdPercent > 100) {
    throw new Error("Price warning threshold must be a whole percent from 1 to 100");
  }
  if (reference === null) return { far: false, stale: false };
  const difference = BigInt(Math.abs(entryPricePaise - reference.close_paise));
  const far = difference * 100n >= BigInt(reference.close_paise) * BigInt(thresholdPercent);
  const age = (Date.parse(`${asOfDate}T00:00:00Z`) -
    Date.parse(`${reference.price_date}T00:00:00Z`)) / 86_400_000;
  return { far, stale: age > stalenessDays };
}

export function warningThreshold(raw: string | undefined): number {
  const value = raw === undefined || raw === "" ? 20 : Number(raw);
  if (!Number.isInteger(value) || value <= 0 || value > 100) {
    throw new Error("VITE_PRICE_WARNING_PERCENT must be a whole percent from 1 to 100");
  }
  return value;
}
