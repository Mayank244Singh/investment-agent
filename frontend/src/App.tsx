import { useEffect, useState } from "react";
import LedgerEntryPanel from "./components/LedgerEntryPanel";
import RealizedPnlPanel from "./components/RealizedPnlPanel";
import VirtualTradingPanel from "./components/VirtualTradingPanel";
import { formatPaise } from "./lib/ledger";

import {
  getHealth,
  getLedger,
  getPortfolioValuation,
  getPriceCoverage,
  getRiskRules,
  type LedgerResponse,
  type PortfolioValuationResponse,
  type PriceCoverageResponse,
  type RiskRulesResponse,
} from "./api/client";
import "./App.css";

type LoadState<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error" };

type BackendStatus = "loading" | "connected" | "unreachable";

function App() {
  const [revision, setRevision] = useState(0);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("loading");
  const [ledger, setLedger] = useState<LoadState<LedgerResponse>>({
    state: "loading",
  });
  const [riskRules, setRiskRules] = useState<LoadState<RiskRulesResponse>>({
    state: "loading",
  });
  const [valuation, setValuation] = useState<LoadState<PortfolioValuationResponse>>({
    state: "loading",
  });
  const [priceCoverage, setPriceCoverage] = useState<LoadState<PriceCoverageResponse>>({
    state: "loading",
  });

  useEffect(() => {
    let isMounted = true;

    void getHealth().then(
      () => isMounted && setBackendStatus("connected"),
      () => isMounted && setBackendStatus("unreachable"),
    );
    void getLedger().then(
      (data) => isMounted && setLedger({ state: "ready", data }),
      () => isMounted && setLedger({ state: "error" }),
    );
    void getRiskRules().then(
      (data) => isMounted && setRiskRules({ state: "ready", data }),
      () => isMounted && setRiskRules({ state: "error" }),
    );
    void getPortfolioValuation().then(
      (data) => isMounted && setValuation({ state: "ready", data }),
      () => isMounted && setValuation({ state: "error" }),
    );
    void getPriceCoverage().then(
      (data) => isMounted && setPriceCoverage({ state: "ready", data }),
      () => isMounted && setPriceCoverage({ state: "error" }),
    );

    return () => {
      isMounted = false;
    };
  }, [revision]);

  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">Portfolio management foundation</p>
          <h1>Investment Agent</h1>
        </div>
        <BackendStatusIndicator status={backendStatus} />
      </header>

      <section className="dashboard-grid" aria-label="Dashboard">
        <VirtualTradingPanel />
        <StatusCard title="Market overview" status="Unavailable" />
        <PortfolioCard ledger={ledger} />
        <LedgerEntryPanel
          symbols={priceCoverage.state === "ready" ? priceCoverage.data.symbols.map((item) => item.symbol) : []}
          stalenessDays={valuation.state === "ready" ? valuation.data.staleness_limit_days : 5}
          onRecorded={() => setRevision((current) => current + 1)}
        />
        <RealizedPnlPanel revision={revision} />
        <ValuationCard valuation={valuation} />
        <PriceCoverageCard coverage={priceCoverage} />
        <StatusCard title="Intraday engine" status="Not implemented" />
        <StatusCard title="Swing engine" status="Not implemented" />
        <StatusCard title="Long-term engine" status="Not implemented" />
        <RiskRulesCard riskRules={riskRules} />
        <StatusCard title="Recent activity" status="Unavailable" />
      </section>
    </main>
  );
}

function ValuationCard({
  valuation,
}: {
  valuation: LoadState<PortfolioValuationResponse>;
}) {
  if (valuation.state === "loading") {
    return <StatusCard title="End-of-day valuation" status="Loading" />;
  }
  if (valuation.state === "error") {
    return <StatusCard title="End-of-day valuation" status="Error loading valuation" />;
  }
  if (valuation.data.holdings.length === 0) {
    return <StatusCard title="End-of-day valuation" status="No data" />;
  }

  const { holdings, totals, as_of_date } = valuation.data;
  const pricedDates = holdings
    .filter((item) => item.status === "priced")
    .map((item) => `${item.symbol} ${item.last_price_date}`)
    .join(", ");
  return (
    <article className="dashboard-card dashboard-card-wide">
      <h2>End-of-day valuation</h2>
      <p className="timezone-note">As of {as_of_date} · end-of-day prices only</p>
      <ul className="valuation-list">
        {holdings.map((holding) => (
          <li key={holding.symbol}>
            <h3>{holding.symbol}</h3>
            {holding.status === "priced" ? (
              <dl className="metric-list">
                <Metric
                  label="Close"
                  value={formatPaise(holding.close_paise)}
                  priceDates={holding.last_price_date}
                />
                <Metric
                  label="Market value"
                  value={formatPaise(holding.market_value_paise)}
                  priceDates={holding.last_price_date}
                />
                <Metric
                  label="Unrealised P&L"
                  value={formatPaise(holding.unrealized_pnl_paise)}
                  priceDates={holding.last_price_date}
                />
              </dl>
            ) : (
              <p>
                Unavailable: {unavailableReason(holding.unavailable_reason)}.
                Last price date: {holding.last_price_date ?? "None"}.
              </p>
            )}
          </li>
        ))}
      </ul>
      <h3>Totals</h3>
      <dl className="metric-list">
        <Metric label="Priced holdings" value={String(totals.priced_holdings_count)} />
        {totals.priced_holdings_count > 0 && (
          <>
            <Metric
              label="Priced holdings market value"
              value={formatPaise(totals.priced_market_value_paise)}
              priceDates={pricedDates}
            />
            <Metric
              label="Priced holdings unrealised P&L"
              value={formatPaise(totals.priced_unrealized_pnl_paise)}
              priceDates={pricedDates}
            />
          </>
        )}
        <Metric label="Unpriced holdings" value={String(totals.unpriced_holdings_count)} />
        {totals.unpriced_holdings_count > 0 && (
          <Metric
            label="Unpriced cost basis"
            value={formatPaise(totals.unpriced_cost_basis_paise)}
          />
        )}
      </dl>
    </article>
  );
}

function PriceCoverageCard({
  coverage,
}: {
  coverage: LoadState<PriceCoverageResponse>;
}) {
  if (coverage.state === "loading") {
    return <StatusCard title="Price coverage" status="Loading" />;
  }
  if (coverage.state === "error") {
    return <StatusCard title="Price coverage" status="Error loading price coverage" />;
  }
  if (coverage.data.symbols.length === 0 && coverage.data.import_batches.length === 0) {
    return <StatusCard title="Price coverage" status="No data" />;
  }
  return (
    <article className="dashboard-card dashboard-card-wide">
      <h2>Price coverage</h2>
      <p className="timezone-note">End-of-day EQ prices</p>
      {coverage.data.symbols.length === 0 ? (
        <p>No EQ prices</p>
      ) : (
        <details>
          <summary>Symbols with prices: {coverage.data.symbols.length}</summary>
          <ul className="coverage-list">
            {coverage.data.symbols.map((item) => (
              <li key={item.symbol}>
                {item.symbol}: End-of-day {item.latest_price_date ?? "Unavailable"}
              </li>
            ))}
          </ul>
        </details>
      )}
      <h3>Import batches</h3>
      <ul className="coverage-list">
        {coverage.data.import_batches.map((batch) => (
          <li key={batch.id}>
            <strong>{batch.source_label}</strong> · {batch.file_name} ·
            imported {batch.imported_rows}, skipped {batch.skipped_rows},
            rejected {batch.rejected_rows}
          </li>
        ))}
      </ul>
    </article>
  );
}

function unavailableReason(reason: string): string {
  switch (reason) {
    case "stale_price":
      return "last end-of-day price is stale";
    case "no_price_on_or_before_as_of":
      return "no end-of-day price on or before the as-of date";
    default:
      return "no EQ end-of-day price";
  }
}

function PortfolioCard({ ledger }: { ledger: LoadState<LedgerResponse> }) {
  if (ledger.state === "loading") {
    return <StatusCard title="Portfolio summary" status="Loading" />;
  }
  if (ledger.state === "error") {
    return <StatusCard title="Portfolio summary" status="Unavailable" />;
  }
  const hasData =
    ledger.data.capital_movements.length > 0 || ledger.data.transactions.length > 0;
  if (!hasData) {
    return <StatusCard title="Portfolio summary" status="No data" />;
  }

  const summary = ledger.data.summary;
  return (
    <article className="dashboard-card dashboard-card-wide">
      <h2>Portfolio summary</h2>
      <dl className="metric-list">
        <Metric label="Cash" value={formatPaise(summary.cash_balance_paise)} />
        <Metric
          label="Deployable cash"
          value={formatPaise(summary.deployable_cash_paise)}
        />
        <Metric
          label="Open cost basis"
          value={formatPaise(summary.open_cost_basis_paise)}
        />
        <Metric
          label="Realised P&L"
          value={formatPaise(summary.realized_pnl_paise)}
        />
        <Metric
          label="Profit Vault"
          value={formatPaise(summary.profit_vault_balance_paise)}
        />
      </dl>
    </article>
  );
}

function RiskRulesCard({
  riskRules,
}: {
  riskRules: LoadState<RiskRulesResponse>;
}) {
  if (riskRules.state === "loading") {
    return <StatusCard title="Risk manager" status="Loading" />;
  }
  if (riskRules.state === "error") {
    return <StatusCard title="Risk manager" status="Unavailable" />;
  }
  if (riskRules.data.rules.length === 0) {
    return <StatusCard title="Risk manager" status="No data" />;
  }

  return (
    <article className="dashboard-card dashboard-card-wide">
      <h2>Risk manager</h2>
      <ul className="rule-list">
        {riskRules.data.rules.map((rule) => (
          <li key={rule.code}>
            <span>{rule.label}</span>
            <strong>
              {rule.limit_percent === null
                ? "Configured"
                : `${rule.limit_percent}%`}
            </strong>
          </li>
        ))}
      </ul>
      <p className="timezone-note">
        Daily reset: {riskRules.data.daily_loss_timezone}
      </p>
    </article>
  );
}

function StatusCard({ title, status }: { title: string; status: string }) {
  return (
    <article className="dashboard-card">
      <h2>{title}</h2>
      <p>{status}</p>
    </article>
  );
}

function Metric({
  label,
  value,
  priceDates,
}: {
  label: string;
  value: string;
  priceDates?: string;
}) {
  return (
    <div data-testid={priceDates ? "price-figure" : undefined}>
      <dt>
        {label}
        {priceDates && <span className="price-context">End-of-day price date: {priceDates}</span>}
      </dt>
      <dd>{value}</dd>
    </div>
  );
}

function BackendStatusIndicator({ status }: { status: BackendStatus }) {
  if (status === "loading") {
    return <p className="connection-status pending">Backend: Loading</p>;
  }
  if (status === "connected") {
    return <p className="connection-status connected">Backend: Connected</p>;
  }
  return <p className="connection-status unreachable">Backend: Unreachable</p>;
}

export default App;
