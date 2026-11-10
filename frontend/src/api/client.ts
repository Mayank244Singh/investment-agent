export type HealthResponse = {
  status: "ok";
  database: "ok";
};

export type LedgerSummary = {
  cash_balance_paise: number;
  deployable_cash_paise: number;
  open_cost_basis_paise: number;
  realized_pnl_paise: number;
  profit_vault_balance_paise: number;
  risk_capital_paise: number;
};

export type Holding = {
  symbol: string;
  quantity: number;
  cost_basis_paise: number;
};

export type LedgerResponse = {
  summary: LedgerSummary;
  holdings: Holding[];
  capital_movements: unknown[];
  transactions: unknown[];
  lots: unknown[];
  realizations: unknown[];
  profit_vault_entries: unknown[];
};

export type RiskRuleStatus = {
  code: string;
  label: string;
  status: "configured";
  limit_percent: string | null;
};

export type RiskRulesResponse = {
  rules: RiskRuleStatus[];
  daily_loss_timezone: string;
};

export type PricedHolding = {
  symbol: string;
  quantity: number;
  cost_basis_paise: number;
  status: "priced";
  unavailable_reason: null;
  last_price_date: string;
  price_age_days: number;
  close_paise: number;
  market_value_paise: number;
  unrealized_pnl_paise: number;
  source_label: string;
  file_name: string;
};

export type UnavailableHolding = {
  symbol: string;
  quantity: number;
  cost_basis_paise: number;
  status: "unavailable";
  unavailable_reason:
    | "no_eq_price"
    | "no_price_on_or_before_as_of"
    | "stale_price";
  last_price_date: string | null;
  price_age_days: number | null;
  close_paise: null;
  market_value_paise: null;
  unrealized_pnl_paise: null;
  source_label: string | null;
  file_name: string | null;
};

export type HoldingValuation = PricedHolding | UnavailableHolding;

export type PortfolioValuationResponse = {
  as_of_date: string;
  staleness_limit_days: number;
  holdings: HoldingValuation[];
  totals: {
    priced_holdings_count: number;
    unpriced_holdings_count: number;
    priced_cost_basis_paise: number;
    priced_market_value_paise: number;
    priced_unrealized_pnl_paise: number;
    unpriced_cost_basis_paise: number;
    total_open_cost_basis_paise: number;
  };
};

export type PriceCoverageResponse = {
  symbols: {
    symbol: string;
    series: "EQ";
    latest_price_date: string | null;
  }[];
  import_batches: {
    id: number;
    file_name: string;
    source_label: string;
    total_rows: number;
    imported_rows: number;
    skipped_rows: number;
    rejected_rows: number;
    status: string;
    imported_at_utc: string;
  }[];
};

export type LatestEqCloseResponse = {
  symbol: string;
  status: "available" | "no_eq_price";
  price_date: string | null;
  close_paise: number | null;
  source_label: string | null;
  file_name: string | null;
  staleness_limit_days: number;
};

export type RealizedMatch = {
  sell_transaction_id: number;
  buy_transaction_id: number;
  holding_lot_id: number;
  symbol: string;
  quantity: number;
  acquired_at_utc: string;
  realized_at_utc: string;
  holding_period_days: number;
  cost_basis_paise: number;
  gross_proceeds_paise: number;
  sell_fees_paise: number;
  net_proceeds_paise: number;
  realized_pnl_paise: number;
};

export type SymbolRealizedPnl = {
  symbol: string;
  quantity_sold: number;
  cost_basis_paise: number;
  gross_proceeds_paise: number;
  sell_fees_paise: number;
  net_proceeds_paise: number;
  realized_pnl_paise: number;
  shortest_holding_period_days: number;
  longest_holding_period_days: number;
};

export type RealizedPnlReportResponse = {
  from_date: string | null;
  to_date: string | null;
  financial_year: number | null;
  symbols: SymbolRealizedPnl[];
  matches: RealizedMatch[];
  total_realized_pnl_paise: number;
};

export type ReportFilters = { from?: string; to?: string; financial_year?: number };

export type VirtualOrder = {
  side: "buy" | "sell";
  symbol: string;
  quantity: number;
  stop_loss_paise: number | null;
  order_type: "market";
};

export type VirtualReason = { code: string; message: string };
export type VirtualPreview = {
  approved: boolean;
  reasons: VirtualReason[];
  symbol: string;
  side: "buy" | "sell";
  quantity: number;
  quote_date: string | null;
  source_label: string;
  reference_paise: number | null;
  fill_paise: number | null;
  fees_paise: number;
  charges: Record<string, number> | null;
  planned_loss_paise: number | null;
  cash_impact_paise: number | null;
  stop_warning: string;
};
export type VirtualHolding = {
  symbol: string;
  quantity: number;
  cost_basis_paise: number;
  market_value_paise: number | null;
  unrealized_pnl_paise: number | null;
  price_date: string | null;
  status: "priced" | "unavailable";
};
export type VirtualAccount = {
  mode: string;
  currency: "INR";
  initial_capital_paise: number;
  cash_balance_paise: number;
  deployable_cash_paise: number;
  realized_pnl_paise: number;
  profit_vault_balance_paise: number;
  holdings: VirtualHolding[];
};
export type VirtualAttempt = {
  id: number;
  status: "filled" | "rejected";
  side: string;
  symbol: string;
  quantity: number;
  attempted_at_utc: string;
  quote_date: string | null;
  fill_paise: number | null;
  fees_paise: number;
  reasons: VirtualReason[];
  ledger_transaction_id: number | null;
  source_label: string;
};
export type VirtualSubmission = VirtualAttempt & { account: VirtualAccount };
export type VirtualQuote = {
  symbol: string;
  price_paise: number;
  price_date: string;
  source_label: string;
  fixture_sha256: string;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/api/v1/health");
}

export async function getLedger(): Promise<LedgerResponse> {
  return getJson<LedgerResponse>("/api/v1/portfolio/ledger");
}

export async function getRiskRules(): Promise<RiskRulesResponse> {
  return getJson<RiskRulesResponse>("/api/v1/risk/rules");
}

export async function getPortfolioValuation(): Promise<PortfolioValuationResponse> {
  return getJson<PortfolioValuationResponse>("/api/v1/portfolio/valuation");
}

export async function getPriceCoverage(): Promise<PriceCoverageResponse> {
  return getJson<PriceCoverageResponse>("/api/v1/prices/coverage");
}

export async function getLatestEqClose(symbol: string): Promise<LatestEqCloseResponse> {
  return getJson<LatestEqCloseResponse>(
    `/api/v1/prices/latest?symbol=${encodeURIComponent(symbol)}`,
  );
}

export async function getRealizedPnl(
  filters: ReportFilters = {},
): Promise<RealizedPnlReportResponse> {
  const query = new URLSearchParams();
  if (filters.from) query.set("from", filters.from);
  if (filters.to) query.set("to", filters.to);
  if (filters.financial_year !== undefined) {
    query.set("financial_year", String(filters.financial_year));
  }
  const suffix = query.size ? `?${query.toString()}` : "";
  return getJson<RealizedPnlReportResponse>(`/api/v1/portfolio/realized-pnl${suffix}`);
}

export async function getVirtualAccount(): Promise<VirtualAccount> {
  return getJson<VirtualAccount>("/api/v1/virtual/account");
}

export async function getVirtualQuotes(): Promise<VirtualQuote[]> {
  return getJson<VirtualQuote[]>("/api/v1/virtual/quotes");
}

export async function getVirtualOrders(): Promise<VirtualAttempt[]> {
  return getJson<VirtualAttempt[]>("/api/v1/virtual/orders");
}

export async function previewVirtualOrder(order: VirtualOrder): Promise<VirtualPreview> {
  return postVirtual<VirtualPreview>("/api/v1/virtual/orders/preview", order);
}

export async function submitVirtualOrder(
  order: VirtualOrder, key: string,
): Promise<VirtualSubmission> {
  return postVirtual<VirtualSubmission>("/api/v1/virtual/orders", order, key);
}

async function postVirtual<T>(url: string, body: VirtualOrder, key?: string): Promise<T> {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new ApiError(response.status, `Virtual request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export async function postLedgerEntry(
  kind: "deposit" | "withdrawal" | "buy" | "sell",
  payload: object,
  idempotencyKey: string,
): Promise<unknown> {
  const route = { deposit: "deposits", withdrawal: "withdrawals", buy: "buys", sell: "sells" }[kind];
  const response = await fetch(`/api/v1/portfolio/${route}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`;
    try {
      const body: unknown = await response.json();
      if (typeof body === "object" && body !== null && "detail" in body) {
        const value = body.detail;
        if (typeof value === "string") detail = value;
        else if (Array.isArray(value)) {
          detail = value.map((item: unknown) => {
            if (typeof item !== "object" || item === null || !("msg" in item)) {
              return "Invalid field";
            }
            const location = "loc" in item && Array.isArray(item.loc)
              ? item.loc.slice(1).join(".") : "field";
            return `${location}: ${String(item.msg)}`;
          }).join("; ");
        }
      }
    } catch { /* Keep the status when the error body is unavailable. */ }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<unknown>;
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}
