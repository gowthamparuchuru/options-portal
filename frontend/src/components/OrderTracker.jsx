import { useState, useEffect, useRef } from "react";

export default function OrderTracker({ execId }) {
  const [orders, setOrders] = useState([]);
  const [done, setDone] = useState(false);
  const wsRef = useRef(null);

  useEffect(() => {
    if (!execId) return;

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/orders/ws/${execId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.orders) setOrders(msg.orders);
      if (msg.type === "done" || msg.done) setDone(true);
    };

    ws.onerror = () => {};
    ws.onclose = () => {};

    return () => ws.close();
  }, [execId]);

  if (orders.length === 0) {
    return (
      <div className="panel" style={{ marginTop: 12 }}>
        <div className="panel-header">Order Execution</div>
        <div className="panel-body" style={{ color: "var(--text-dim)" }}>
          Waiting for order updates...
        </div>
      </div>
    );
  }

  return (
    <div className="panel" style={{ marginTop: 12 }}>
      <div className="panel-header">
        Order Execution
        <span style={{ fontWeight: 400, color: done ? "var(--green)" : "var(--yellow)" }}>
          {done ? "Complete" : "In Progress"}
        </span>
      </div>
      <div className="panel-body">
        {orders.map((o, i) => (
          <div className="order-row" key={i}>
            <div>
              <div style={{ fontWeight: 500 }}>
                {o.option_type} — {o.strike?.toLocaleString()}
              </div>
              <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
                {o.symbol}
                {o.phase && ` · ${o.phase}`}
                {o.attempt > 0 && ` · Try #${o.attempt}`}
              </div>
              {o.avg_price > 0 && (
                <div style={{ fontSize: 12, color: "var(--green)" }}>
                  Filled @ ₹{o.avg_price.toFixed(2)}
                </div>
              )}
              {o.error && (
                <div style={{ fontSize: 12, color: "var(--red)" }}>{o.error}</div>
              )}
            </div>
            <span className={`status-badge status-${o.status}`}>{o.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
