function Dot({ ok, loading }) {
  if (loading) return <span className="auth-dot loading" />;
  return <span className={`auth-dot ${ok ? "ok" : "fail"}`} />;
}

export default function LoginStatus({ brokerStatus }) {
  if (!brokerStatus) {
    return (
      <div className="broker-status-row">
        <div className="auth-badge"><Dot loading /> Shoonya</div>
        <div className="auth-badge"><Dot loading /> Upstox</div>
      </div>
    );
  }

  const { shoonya, upstox } = brokerStatus;

  return (
    <div className="broker-status-row">
      <div
        className="auth-badge"
        title={shoonya?.ok ? "Shoonya connected" : shoonya?.error || "Disconnected"}
      >
        <Dot ok={shoonya?.ok} />
        Shoonya
      </div>
      <div
        className="auth-badge"
        title={upstox?.ok ? "Upstox connected" : upstox?.error || "Disconnected"}
      >
        <Dot ok={upstox?.ok} />
        Upstox
      </div>
    </div>
  );
}
