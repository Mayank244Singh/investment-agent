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

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}
