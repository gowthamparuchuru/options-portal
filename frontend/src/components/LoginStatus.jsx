import { useState, useEffect } from "react";

function BrokerDot({ ok, loading }) {
  if (loading) return <span className="auth-dot loading" />;
  return <span className={`auth-dot ${ok ? "ok" : "fail"}`} />;
}

export default function LoginStatus({ auth }) {
  const [brokers, setBrokers] = useState(null);

  useEffect(() => {
    if (!auth.ok) return;

    const check = () => {
      fetch("/api/auth/broker-status")
        .then((r) => r.json())
        .then(setBrokers)
        .catch(() => {});
    };

    check();
    const id = setInterval(check, 60_000);
    return () => clearInterval(id);
  }, [auth.ok]);

  if (!auth.checked) {
    return (
      <div className="broker-status-row">
        <div className="auth-badge">
          <span className="auth-dot loading" />
          Connecting...
        </div>
      </div>
    );
  }

  if (!auth.ok) {
    return (
      <div className="broker-status-row">
        <div className="auth-badge" title={auth.error || "Login failed"}>
          <span className="auth-dot fail" />
          Disconnected
        </div>
      </div>
    );
  }

  const shoonLoading = !brokers;
  const shoonOk = brokers?.shoonya?.ok ?? false;
  const upstoxOk = brokers?.upstox?.ok ?? false;

  return (
    <div className="broker-status-row">
      <div
        className="auth-badge"
        title={shoonOk ? "Shoonya connected" : brokers?.shoonya?.error || "Checking..."}
      >
        <BrokerDot ok={shoonOk} loading={shoonLoading} />
        Shoonya
      </div>
      <div
        className="auth-badge"
        title={upstoxOk ? "Upstox connected" : brokers?.upstox?.error || "Checking..."}
      >
        <BrokerDot ok={upstoxOk} loading={shoonLoading} />
        Upstox
      </div>
    </div>
  );
}
