import { useEffect, useState } from "react";

import { getHealth, type HealthResponse } from "./api/client";
import "./App.css";

type BackendStatus =
  | { state: "loading" }
  | { state: "connected"; health: HealthResponse }
  | { state: "unreachable" };

const dashboardSections = [
  ["Market overview", "Unavailable"],
  ["Portfolio summary", "Unavailable"],
  ["Intraday engine", "Not implemented"],
  ["Swing engine", "Not implemented"],
  ["Long-term engine", "Not implemented"],
  ["Risk manager", "Not implemented"],
  ["Recent activity", "Unavailable"],
] as const;

function App() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>({ state: "loading" });

  useEffect(() => {
    let isMounted = true;

    async function loadHealth() {
      try {
        const health = await getHealth();
        if (isMounted) {
          setBackendStatus({ state: "connected", health });
        }
      } catch {
        if (isMounted) {
          setBackendStatus({ state: "unreachable" });
        }
      }
    }

    void loadHealth();
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
        {dashboardSections.map(([title, status]) => (
          <article className="dashboard-card" key={title}>
            <h2>{title}</h2>
            <p>{status}</p>
          </article>
        ))}
      </section>
    </main>
  );
}

function BackendStatusIndicator({ status }: { status: BackendStatus }) {
  if (status.state === "loading") {
    return <p className="connection-status pending">Backend: Loading</p>;
  }

  if (status.state === "connected") {
    return <p className="connection-status connected">Backend: Connected</p>;
  }

  return <p className="connection-status unreachable">Backend: Unreachable</p>;
}

export default App;
