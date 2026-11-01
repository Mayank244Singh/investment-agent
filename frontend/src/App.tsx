import { useEffect, useState } from "react";

import {
  getHealth,
  getLedger,
  getRiskRules,
  type LedgerResponse,
  type RiskRulesResponse,
} from "./api/client";
import "./App.css";

type LoadState<T> =
  | { state: "loading" }
  | { state: "ready"; data: T }
  | { state: "error" };

type BackendStatus = "loading" | "connected" | "unreachable";

function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>("loading");
  const [ledger, setLedger] = useState<LoadState<LedgerResponse>>({
    state: "loading",
  });
  const [riskRules, setRiskRules] = useState<LoadState<RiskRulesResponse>>({
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

    return () => {
      isMounted = false;
    };
  }, []);

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
        <StatusCard title="Market overview" status="Unavailable" />
        <PortfolioCard ledger={ledger} />
        <StatusCard title="Intraday engine" status="Not implemented" />
        <StatusCard title="Swing engine" status="Not implemented" />
        <StatusCard title="Long-term engine" status="Not implemented" />
        <RiskRulesCard riskRules={riskRules} />
        <StatusCard title="Recent activity" status="Unavailable" />
      </section>
    </main>
  );
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

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function formatPaise(paise: number): string {
  const sign = paise < 0 ? "-" : "";
  const absolute = Math.abs(paise);
  const rupees = Math.trunc(absolute / 100).toLocaleString("en-IN");
  const remainder = (absolute % 100).toString().padStart(2, "0");
  return `${sign}₹${rupees}.${remainder}`;
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
