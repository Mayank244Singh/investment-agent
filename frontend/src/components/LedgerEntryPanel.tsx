import { useEffect, useRef, useState } from "react";

import {
  ApiError, getLatestEqClose, postLedgerEntry,
  type LatestEqCloseResponse,
} from "../api/client";
import {
  formatPaise, indiaToday, normalizeSymbol, parseRupeesToPaise,
  prepareEntry, priceWarning, warningThreshold,
  type Draft, type PreparedEntry,
} from "../lib/ledger";

const STORAGE_KEY = "investment-agent.pending-ledger-v1";
const WARNING_PERCENT = warningThreshold(import.meta.env.VITE_PRICE_WARNING_PERCENT);

type PendingSubmission = { key: string; entry: PreparedEntry };
type PriceLookup =
  | { state: "idle" | "loading" | "error" }
  | { state: "ready"; data: LatestEqCloseResponse };

function readPending(): PendingSubmission | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null || !("key" in parsed) ||
        !("entry" in parsed) || typeof parsed.key !== "string" ||
        typeof parsed.entry !== "object" || parsed.entry === null) return null;
    return parsed as PendingSubmission;
  } catch {
    return null;
  }
}

function initialDraft(): Draft {
  return {
    kind: "deposit", amount: "", symbol: "", quantity: "", price: "",
    fees: "0", date: indiaToday(), note: "",
  };
}

export default function LedgerEntryPanel({
  symbols, stalenessDays, onRecorded,
}: {
  symbols: string[];
  stalenessDays: number;
  onRecorded: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(initialDraft);
  const [review, setReview] = useState<PreparedEntry | null>(null);
  const [pending, setPending] = useState<PendingSubmission | null>(readPending);
  const [lookup, setLookup] = useState<PriceLookup>({ state: "idle" });
  const [errors, setErrors] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const isTrade = draft.kind === "buy" || draft.kind === "sell";
  const normalizedSymbol = isTrade ? normalizeSymbol(draft.symbol) : null;
  const visibleTrade = pending?.entry ?? review;
  const lookupSymbol = visibleTrade && "symbol" in visibleTrade.payload
    ? visibleTrade.payload.symbol : normalizedSymbol;

  useEffect(() => {
    if (lookupSymbol === null) return;
    let active = true;
    void getLatestEqClose(lookupSymbol).then(
      (data) => active && setLookup({ state: "ready", data }),
      () => active && setLookup({ state: "error" }),
    );
    return () => { active = false; };
  }, [lookupSymbol]);

  const update = (field: keyof Draft, value: string) => {
    setDraft((current) => ({ ...current, [field]: value }));
    setErrors([]);
    setMessage("");
    if (field === "symbol" || field === "kind") setLookup({ state: "loading" });
  };

  const send = async (submission: PendingSubmission) => {
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setMessage("");
    try {
      await postLedgerEntry(submission.entry.kind, submission.entry.payload, submission.key);
      localStorage.removeItem(STORAGE_KEY);
      setPending(null);
      setReview(null);
      setDraft(initialDraft());
      setMessage("Entry recorded. Ledger and valuation are refreshing.");
      onRecorded();
    } catch (error) {
      if (error instanceof ApiError && error.status >= 400 &&
          error.status < 500 && error.status !== 408) {
        localStorage.removeItem(STORAGE_KEY);
        setPending(null);
        const prefix = error.status === 409 ? "Conflict (409)" :
          error.status === 422 ? "Validation error (422)" : `Request rejected (${error.status})`;
        setMessage(`${prefix}: ${error.message}`);
      } else {
        setMessage(
          "Outcome unknown. The entry may have been recorded. Retry the same submission; do not create a new one.",
        );
      }
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  };

  const confirm = () => {
    if (review === null || submittingRef.current) return;
    const existing = readPending();
    if (existing !== null) {
      setPending(existing);
      setMessage("Another unconfirmed submission is stored. Resolve it before recording a new entry.");
      return;
    }
    const submission = { key: crypto.randomUUID(), entry: review };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(submission));
    } catch {
      setMessage("Browser storage is unavailable; the submission was not sent.");
      return;
    }
    setPending(submission);
    void send(submission);
  };

  const visible = pending?.entry ?? review;
  const reference = lookup.state === "ready" && lookup.data.symbol === lookupSymbol &&
    lookup.data.status === "available" &&
    lookup.data.close_paise !== null && lookup.data.price_date !== null
    ? { close_paise: lookup.data.close_paise, price_date: lookup.data.price_date }
    : null;
  const tradePrice = visible && "price_paise" in visible.payload
    ? visible.payload.price_paise : null;
  const effectiveStalenessDays = lookup.state === "ready"
    ? lookup.data.staleness_limit_days : stalenessDays;
  const comparison = tradePrice === null
    ? null
    : priceWarning(tradePrice, reference, indiaToday(), effectiveStalenessDays, WARNING_PERCENT);
  const suggestions = normalizedSymbol === null ? [] : symbols
    .filter((symbol) => symbol.startsWith(normalizedSymbol))
    .slice(0, 20);

  return (
    <article className="dashboard-card dashboard-card-wide">
      <h2>Record completed ledger entry</h2>
      {pending && <p role="alert">Unconfirmed submission recovered. Retry with the same idempotency key.</p>}
      {message && <p role="alert">{message}</p>}
      {visible ? (
        <div className="entry-review">
          <h3>{pending ? "Unconfirmed submission" : "Confirm entry"}</h3>
          <EntrySummary entry={visible} />
          {tradePrice !== null && (
            <div className="reference-price">
              {lookup.state === "ready" && reference ? (
                <p>Latest stored EQ close: {formatPaise(reference.close_paise)} · end-of-day price date: {reference.price_date}</p>
              ) : lookup.state === "ready" && lookup.data.symbol === lookupSymbol &&
                lookup.data.status === "no_eq_price" ? (
                <p role="alert">Warning: this symbol has no stored EQ price.</p>
              ) : lookup.state === "error" ? (
                <p role="alert">Warning: the reference price could not be checked.</p>
              ) : (
                <p>Checking the latest stored EQ close…</p>
              )}
              {comparison?.far && <p role="alert">Warning: entered price differs from the latest stored close by at least {WARNING_PERCENT}%.</p>}
              {comparison?.stale && <p role="alert">Warning: the reference close is older than the {effectiveStalenessDays}-day staleness limit.</p>}
            </div>
          )}
          <p className="correction-warning">Recorded entries cannot be undone. Check all details before submitting.</p>
          <p className="timezone-note">This records an event that already happened; proposed-trade risk validation does not apply.</p>
          {pending ? (
            <button type="button" onClick={() => void send(pending)} disabled={submitting}>
              {submitting ? "Submitting…" : "Retry same submission"}
            </button>
          ) : (
            <div className="form-actions">
              <button type="button" onClick={() => setReview(null)}>Back to edit</button>
              <button type="button" onClick={confirm} disabled={submitting ||
                (tradePrice !== null && lookup.state !== "ready" && lookup.state !== "error")}>
                Confirm and record
              </button>
            </div>
          )}
        </div>
      ) : (
        <form onSubmit={(event) => {
          event.preventDefault();
          const result = prepareEntry(draft);
          if (result.entry === null) setErrors(result.errors);
          else setReview(result.entry);
        }}>
          <div className="entry-grid">
            <label>Entry type
              <select value={draft.kind} onChange={(event) => update("kind", event.target.value)}>
                <option value="deposit">Deposit</option>
                <option value="withdrawal">Withdrawal</option>
                <option value="buy">Buy</option>
                <option value="sell">Sell</option>
              </select>
            </label>
            <label>Trade date · Asia/Kolkata
              <input type="date" value={draft.date} max={indiaToday()}
                onChange={(event) => update("date", event.target.value)} />
            </label>
            {isTrade ? (
              <>
                <label>Symbol
                  <input value={draft.symbol} list="stored-equity-symbols"
                    onChange={(event) => update("symbol", event.target.value)} />
                </label>
                <datalist id="stored-equity-symbols">
                  {suggestions.map((symbol) => <option key={symbol} value={symbol} />)}
                </datalist>
                <label>Quantity
                  <input inputMode="numeric" value={draft.quantity}
                    onChange={(event) => update("quantity", event.target.value)} />
                </label>
                <label>Price per share · ₹
                  <input inputMode="decimal" value={draft.price}
                    onChange={(event) => update("price", event.target.value)} />
                </label>
                <label>Fees · ₹
                  <input inputMode="decimal" value={draft.fees}
                    onChange={(event) => update("fees", event.target.value)} />
                </label>
              </>
            ) : (
              <label>Amount · ₹
                <input inputMode="decimal" value={draft.amount}
                  onChange={(event) => update("amount", event.target.value)} />
              </label>
            )}
            <label>Note (optional)
              <textarea value={draft.note} maxLength={2000}
                onChange={(event) => update("note", event.target.value)} />
            </label>
          </div>
          {errors.length > 0 && <ul role="alert">{errors.map((error) => <li key={error}>{error}</li>)}</ul>}
          {isTrade && draft.price && parseRupeesToPaise(draft.price) !== null &&
            lookup.state === "ready" && reference && (
              <p>Latest stored EQ close: {formatPaise(reference.close_paise)} · end-of-day price date: {reference.price_date}</p>
            )}
          <button type="submit">Review entry</button>
        </form>
      )}
    </article>
  );
}

function EntrySummary({ entry }: { entry: PreparedEntry }) {
  const { payload } = entry;
  const trade = "symbol" in payload;
  return (
    <dl className="entry-summary">
      <div><dt>Entry</dt><dd>{entry.kind}</dd></div>
      <div><dt>Date · Asia/Kolkata</dt><dd>{(trade ? payload.transacted_at_utc : payload.occurred_at_utc).slice(0, 10)}</dd></div>
      {trade && <>
        <div><dt>Symbol</dt><dd>{payload.symbol}</dd></div>
        <div><dt>Quantity</dt><dd>{payload.quantity}</dd></div>
        <div><dt>Price per share</dt><dd>{formatPaise(payload.price_paise)}</dd></div>
        <div><dt>Gross trade value</dt><dd>{formatPaise(entry.gross_paise)}</dd></div>
        <div><dt>Fees</dt><dd>{formatPaise(entry.fees_paise)}</dd></div>
      </>}
      <div><dt>{entry.kind === "buy" ? "Total debit including fees" :
        entry.kind === "sell" ? "Net sale proceeds after fees" : "Amount"}</dt>
        <dd>{formatPaise(entry.total_paise)}</dd></div>
      {payload.note && <div><dt>Note</dt><dd>{payload.note}</dd></div>}
    </dl>
  );
}
