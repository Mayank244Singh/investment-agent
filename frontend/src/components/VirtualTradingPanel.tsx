import { useEffect, useRef, useState } from "react";
import {
  ApiError, getVirtualAccount, getVirtualOrders, getVirtualQuotes,
  previewVirtualOrder, submitVirtualOrder,
  type VirtualAccount, type VirtualAttempt, type VirtualOrder,
  type VirtualPreview, type VirtualQuote,
} from "../api/client";
import { formatPaise, normalizeSymbol, parseRupeesToPaise } from "../lib/ledger";

const PENDING_KEY = "investment-agent.pending-virtual-v1";
type Pending = { key: string; order: VirtualOrder };

function readPending(): Pending | null {
  try {
    const raw = localStorage.getItem(PENDING_KEY);
    if (!raw) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || !("key" in parsed) ||
        !("order" in parsed) || typeof parsed.key !== "string") return null;
    return parsed as Pending;
  } catch { return null; }
}

export default function VirtualTradingPanel() {
  const [account, setAccount] = useState<VirtualAccount | null>(null);
  const [quotes, setQuotes] = useState<VirtualQuote[]>([]);
  const [history, setHistory] = useState<VirtualAttempt[]>([]);
  const [loadError, setLoadError] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [symbol, setSymbol] = useState("INFY");
  const [quantity, setQuantity] = useState("1");
  const [stop, setStop] = useState("");
  const [preview, setPreview] = useState<VirtualPreview | null>(null);
  const [order, setOrder] = useState<VirtualOrder | null>(null);
  const [pending, setPending] = useState<Pending | null>(readPending);
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);

  const refresh = () => {
    void Promise.all([getVirtualAccount(), getVirtualQuotes(), getVirtualOrders()]).then(
      ([a, q, h]) => { setAccount(a); setQuotes(q); setHistory(h); setLoadError(""); },
      () => setLoadError("Virtual account unavailable. Run the isolated seed first."),
    );
  };
  useEffect(refresh, []);

  const inspect = async () => {
    setMessage("");
    const normalized = normalizeSymbol(symbol);
    const count = Number(quantity);
    const stopPaise = side === "buy" ? parseRupeesToPaise(stop) : null;
    if (!normalized || !Number.isSafeInteger(count) || count <= 0 ||
        (side === "buy" && stopPaise === null)) {
      setMessage("Enter a listed synthetic symbol, positive whole quantity and a positive buy stop.");
      return;
    }
    const requested: VirtualOrder = { side, symbol: normalized, quantity: count,
      stop_loss_paise: stopPaise, order_type: "market" };
    setBusy(true);
    try {
      const result = await previewVirtualOrder(requested);
      setOrder(requested);
      setPreview(result);
    } catch {
      setMessage("Preview unavailable; no simulated order was submitted.");
    } finally { setBusy(false); }
  };

  const send = async (submission: Pending) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    try {
      const result = await submitVirtualOrder(submission.order, submission.key);
      localStorage.removeItem(PENDING_KEY);
      setPending(null);
      setPreview(null);
      setOrder(null);
      setAccount(result.account);
      setMessage(result.status === "filled"
        ? `Simulated ${result.side} filled once. Ledger #${result.ledger_transaction_id}.`
        : `Risk rejected: ${result.reasons.map((r) => r.message).join("; ")}`);
      refresh();
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 && error.status < 500 && error.status !== 408) {
        localStorage.removeItem(PENDING_KEY);
        setPending(null);
        setMessage(error.status === 409 ? "Idempotency conflict (409): this key belongs to another order."
          : `Virtual request rejected (${error.status}); no fill confirmation.`);
      } else {
        setMessage("Outcome unknown. Retry the saved submission with the same key; do not create another order.");
      }
    } finally { busyRef.current = false; setBusy(false); }
  };

  const confirm = () => {
    if (!order || !preview?.approved || busyRef.current || pending) return;
    const submission: Pending = { key: crypto.randomUUID(), order };
    try { localStorage.setItem(PENDING_KEY, JSON.stringify(submission)); }
    catch { setMessage("Browser storage unavailable; order not sent."); return; }
    setPending(submission);
    void send(submission);
  };

  return <article className="dashboard-card dashboard-card-wide" aria-label="Virtual trading">
    <h2>Virtual trading demo</h2>
    <p role="note"><strong>SYNTHETIC DEMO — NOT MARKET DATA. No broker orders or live prices.</strong></p>
    {loadError && <p role="alert">{loadError}</p>}
    {account && <>
      <p>Cash {formatPaise(account.cash_balance_paise)} · Deployable {formatPaise(account.deployable_cash_paise)} · Realised P&amp;L {formatPaise(account.realized_pnl_paise)} · Profit Vault {formatPaise(account.profit_vault_balance_paise)}</p>
      <h3>Virtual holdings</h3>
      {account.holdings.length === 0 ? <p>No holdings</p> : <ul>{account.holdings.map((h) => <li key={h.symbol}>
        {h.symbol} · {h.quantity} shares · FIFO cost {formatPaise(h.cost_basis_paise)} · {h.status === "priced" && h.market_value_paise !== null && h.unrealized_pnl_paise !== null
          ? <>Synthetic value {formatPaise(h.market_value_paise)} · Unrealised P&amp;L {formatPaise(h.unrealized_pnl_paise)} · price date {h.price_date}</>
          : <>Unavailable · last synthetic price date {h.price_date ?? "none"}</>}
      </li>)}</ul>}
    </>}
    <h3>Simulated order</h3>
    <label>Side <select value={side} onChange={(e) => { setSide(e.target.value as "buy" | "sell"); setPreview(null); }}><option value="buy">Buy</option><option value="sell">Sell</option></select></label>
    <label>Symbol <select value={symbol} onChange={(e) => { setSymbol(e.target.value); setPreview(null); }}>{[...new Set([symbol, ...quotes.map((q) => q.symbol)])].map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
    <label>Whole shares <input value={quantity} onChange={(e) => { setQuantity(e.target.value); setPreview(null); }} inputMode="numeric" /></label>
    {side === "buy" && <label>Planned stop · ₹ <input value={stop} onChange={(e) => { setStop(e.target.value); setPreview(null); }} inputMode="decimal" /></label>}
    {quotes.find((q) => q.symbol === symbol) && <p>Synthetic reference {formatPaise(quotes.find((q) => q.symbol === symbol)!.price_paise)} · price date {quotes.find((q) => q.symbol === symbol)!.price_date}</p>}
    <button type="button" onClick={() => void inspect()} disabled={busy || !!pending || !!loadError}>Preview simulated order</button>
    {preview && <div className="entry-review">
      <h3>Review simulated order</h3>
      <p>{preview.source_label} · price date {preview.quote_date ?? "unavailable"}</p>
      {preview.fill_paise !== null && <p>Estimated fill {formatPaise(preview.fill_paise)} · fees {formatPaise(preview.fees_paise)} · cash impact {preview.cash_impact_paise === null ? "Unavailable" : formatPaise(preview.cash_impact_paise)}</p>}
      {preview.reference_paise !== null && <p>Synthetic reference {formatPaise(preview.reference_paise)} · adverse slippage included in estimated fill.</p>}
      {preview.charges && <details><summary>Illustrative charge breakdown · unverified broker rates</summary><ul>{Object.entries(preview.charges).map(([name, paise]) => <li key={name}>{name.replaceAll("_", " ")}: {formatPaise(paise)}</li>)}</ul></details>}
      {preview.planned_loss_paise !== null && <p>Planned risk {formatPaise(preview.planned_loss_paise)}</p>}
      <p>{preview.stop_warning}</p>
      {preview.reasons.length > 0 && <ul role="alert">{preview.reasons.map((r) => <li key={r.code}>{r.code}: {r.message}</li>)}</ul>}
      {preview.approved && <button type="button" onClick={confirm} disabled={busy || !!pending}>Confirm simulated fill</button>}
    </div>}
    {pending && <p role="alert">Unconfirmed virtual submission recovered. <button type="button" disabled={busy} onClick={() => void send(pending)}>Retry same key</button></p>}
    {message && <p role="status">{message}</p>}
    <h3>Virtual order history</h3>
    {history.length === 0 ? <p>No simulated orders</p> : <ul>{history.map((item) => <li key={item.id}>{item.status} · {item.side} {item.quantity} {item.symbol} · {item.quote_date ?? "no price date"} · {item.source_label}{item.reasons.length ? ` · ${item.reasons.map((r) => r.code).join(", ")}` : ""}</li>)}</ul>}
  </article>;
}
