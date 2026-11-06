import { useEffect, useState } from "react";

import {
  getRealizedPnl,
  type RealizedPnlReportResponse,
  type ReportFilters,
} from "../api/client";
import { formatPaise } from "../lib/ledger";

type ReportState =
  | { state: "loading" }
  | { state: "error" }
  | { state: "ready"; data: RealizedPnlReportResponse };

export default function RealizedPnlPanel({ revision }: { revision: number }) {
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [financialYear, setFinancialYear] = useState("");
  const [filters, setFilters] = useState<ReportFilters>({});
  const [state, setState] = useState<ReportState>({ state: "loading" });
  const [filterError, setFilterError] = useState("");

  useEffect(() => {
    let active = true;
    void getRealizedPnl(filters).then(
      (data) => active && setState({ state: "ready", data }),
      () => active && setState({ state: "error" }),
    );
    return () => { active = false; };
  }, [filters, revision]);

  const applyFilters = () => {
    if (financialYear && (from || to)) {
      setFilterError("Choose a financial year or a date range, not both.");
      return;
    }
    if (from && to && from > to) {
      setFilterError("From date must be on or before to date.");
      return;
    }
    if (financialYear && (!/^\d{4}$/.test(financialYear) ||
      Number(financialYear) < 1900 || Number(financialYear) > 9998)) {
      setFilterError("Enter a valid financial-year starting year.");
      return;
    }
    setFilterError("");
    setState({ state: "loading" });
    setFilters(financialYear ? { financial_year: Number(financialYear) } : {
      from: from || undefined, to: to || undefined,
    });
  };

  const query = new URLSearchParams();
  if (filters.from) query.set("from", filters.from);
  if (filters.to) query.set("to", filters.to);
  if (filters.financial_year !== undefined) {
    query.set("financial_year", String(filters.financial_year));
  }
  const matchHref = `/api/v1/portfolio/fifo-matches.csv${query.size ? `?${query}` : ""}`;

  return (
    <article className="dashboard-card dashboard-card-wide">
      <h2>Realised P&amp;L · FIFO ledger</h2>
      <p className="timezone-note">Recorded sales only. No tax calculation or tax claim.</p>
      <div className="report-filters">
        <label>From · Asia/Kolkata
          <input type="date" value={from} onChange={(event) => setFrom(event.target.value)} />
        </label>
        <label>To · Asia/Kolkata
          <input type="date" value={to} onChange={(event) => setTo(event.target.value)} />
        </label>
        <label>Financial year starting April
          <input type="number" min="1900" max="9998" placeholder="e.g. 2026"
            value={financialYear} onChange={(event) => setFinancialYear(event.target.value)} />
        </label>
        <button type="button" onClick={applyFilters}>Apply filters</button>
      </div>
      {filterError && <p role="alert">{filterError}</p>}
      <div className="export-links">
        <a href="/api/v1/portfolio/ledger.csv" download>Download ledger CSV</a>
        <a href={matchHref} download>Download FIFO matches CSV</a>
      </div>
      {state.state === "loading" && <p>Loading realised P&amp;L</p>}
      {state.state === "error" && <p role="alert">Error loading realised P&amp;L report.</p>}
      {state.state === "ready" && (state.data.matches.length === 0 ? (
        <p>No realised sales for this period.</p>
      ) : (
        <>
          <p>Period: {state.data.from_date ?? "all dates"} to {state.data.to_date ?? "all dates"}</p>
          <div className="table-scroll">
            <table>
              <caption>Realised P&amp;L by symbol, based on matched FIFO lots</caption>
              <thead><tr>
                <th>Symbol</th><th>Quantity sold</th><th>FIFO cost</th>
                <th>Gross proceeds</th><th>Sell fees</th><th>Net proceeds</th>
                <th>Realised P&amp;L</th><th>Holding period · days, shortest–longest</th>
              </tr></thead>
              <tbody>{state.data.symbols.map((row) => <tr key={row.symbol}>
                <th>{row.symbol}</th><td>{row.quantity_sold}</td>
                <td>{formatPaise(row.cost_basis_paise)}</td>
                <td>{formatPaise(row.gross_proceeds_paise)}</td>
                <td>{formatPaise(row.sell_fees_paise)}</td>
                <td>{formatPaise(row.net_proceeds_paise)}</td>
                <td>{formatPaise(row.realized_pnl_paise)}</td>
                <td>{row.shortest_holding_period_days}–{row.longest_holding_period_days}</td>
              </tr>)}</tbody>
            </table>
          </div>
          <p>Total realised P&amp;L from FIFO matches: {formatPaise(state.data.total_realized_pnl_paise)}</p>
          <details>
            <summary>Individual FIFO matches ({state.data.matches.length})</summary>
            <div className="table-scroll"><table>
              <caption>Each matched buy lot and sell, with dated cost and proceeds</caption>
              <thead><tr>
                <th>Symbol</th><th>Buy lot</th><th>Sell</th><th>Acquired · IST</th>
                <th>Sold · IST</th><th>Days held</th><th>Quantity</th>
                <th>FIFO cost</th><th>Net proceeds</th><th>Realised P&amp;L</th>
              </tr></thead>
              <tbody>{state.data.matches.map((row) => <tr key={`${row.sell_transaction_id}-${row.holding_lot_id}`}>
                <th>{row.symbol}</th><td>{row.buy_transaction_id}</td>
                <td>{row.sell_transaction_id}</td>
                <td>{indiaDate(row.acquired_at_utc)}</td>
                <td>{indiaDate(row.realized_at_utc)}</td>
                <td>{row.holding_period_days}</td><td>{row.quantity}</td>
                <td>{formatPaise(row.cost_basis_paise)}</td>
                <td>{formatPaise(row.net_proceeds_paise)}</td>
                <td>{formatPaise(row.realized_pnl_paise)}</td>
              </tr>)}</tbody>
            </table></div>
          </details>
        </>
      ))}
    </article>
  );
}

function indiaDate(timestamp: string): string {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(new Date(timestamp));
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}
