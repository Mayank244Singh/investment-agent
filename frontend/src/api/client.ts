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

export async function getHealth(): Promise<HealthResponse> {
  return getJson<HealthResponse>("/api/v1/health");
}

export async function getLedger(): Promise<LedgerResponse> {
  return getJson<LedgerResponse>("/api/v1/portfolio/ledger");
}

export async function getRiskRules(): Promise<RiskRulesResponse> {
  return getJson<RiskRulesResponse>("/api/v1/risk/rules");
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url);

  if (!response.ok) {
    throw new Error(`Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}
