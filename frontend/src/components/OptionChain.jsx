import { useState, useEffect, useRef } from "react";

export default function OptionChain({ indexId, onAdd, onSpotUpdate, onCompanionUpdate }) {
  const [data, setData] = useState(null);
  const [prices, setPrices] = useState({});
  const [spot, setSpot] = useState(0);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState(null);
  const wsRef = useRef(null);

  useEffect(() => {
    setData(null);
    setPrices({});
    setError(null);
    setConnected(false);

    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${proto}//${window.location.host}/api/options/ws/${indexId}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => setConnected(true);

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === "error") {
        setError(msg.message);
        return;
      }
      if (msg.type === "init") {
        setData(msg);
        setSpot(msg.spot_price);
        if (onSpotUpdate) onSpotUpdate(msg.spot_price);
        if (onCompanionUpdate && msg.companion) onCompanionUpdate(msg.companion);
      }
      if (msg.type === "tick") {
        setPrices(msg.prices);
        if (msg.spot) {
          setSpot(msg.spot);
          if (onSpotUpdate) onSpotUpdate(msg.spot);
        }
        if (onCompanionUpdate && msg.companion) onCompanionUpdate(msg.companion);
      }
    };

    ws.onclose = () => setConnected(false);
    ws.onerror = () => setError("WebSocket connection failed");

    return () => {
      ws.close();
    };
  }, [indexId]);

  if (error) {
    return (
      <div className="panel">
        <div className="panel-header">Option Chain</div>
        <div className="panel-body" style={{ color: "var(--red)" }}>
          {error}
        </div>
      </div>
    );
  }

  if (!data) {
    return (
      <div className="panel">
        <div className="panel-header">
          Option Chain
          <span style={{ fontWeight: 400, color: "var(--text-dim)" }}>
            Loading...
          </span>
        </div>
        <div className="panel-body">Connecting to live feed...</div>
      </div>
    );
  }

  const strikes = data.strikes;
  const atm = data.atm;

  const sortedKeys = Object.keys(strikes).sort(
    (a, b) => Number(a) - Number(b)
  );

  const ceStrikes = sortedKeys.filter((k) => Number(k) > atm).reverse();
  const peStrikes = sortedKeys.filter((k) => Number(k) < atm).reverse();

  return (
    <div className="panel">
      <div className="panel-header">
        Option Chain — {indexId}
        <span>
          <span className={`ws-dot ${connected ? "connected" : "disconnected"}`} />
          {connected ? "Live" : "Disconnected"}
        </span>
      </div>

      <div className="spot-bar">
        Spot: <strong>{Number(spot).toLocaleString("en-IN", { minimumFractionDigits: 2 })}</strong>
        &nbsp;|&nbsp; Expiry: <strong>{data.expiry}</strong>
        &nbsp;|&nbsp; ATM: <strong>{Number(atm).toLocaleString()}</strong>
      </div>

      <div className="chain-split">
        {/* CE side — strikes above spot */}
        <div className="chain-half">
          <table className="chain-table">
            <thead>
              <tr>
                <th>Strike</th>
                <th>LTP</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {ceStrikes.map((key) => {
                const s = strikes[key];
                if (!s) return null;
                const strikeNum = Number(key);
                const ltp = s.ce_token ? prices[s.ce_token] || 0 : 0;
                const dist = spot
                  ? (((strikeNum - spot) / spot) * 100).toFixed(2)
                  : "0.00";

                return (
                  <tr key={key}>
                    <td className="strike-cell">
                      {strikeNum.toLocaleString()}
                      <span className="dist-tag">+{dist}%</span>
                    </td>
                    <td className="price-cell ce-price">
                      {ltp ? `₹${Number(ltp).toFixed(2)}` : "—"}
                    </td>
                    <td>
                      {s.ce_symbol && (
                        <button
                          className="btn btn-sm btn-ce"
                          onClick={() =>
                            onAdd({
                              ...s,
                              strike: strikeNum,
                              side: "CE",
                              exchange: data.exchange,
                              expiry: data.expiry,
                              indexId,
                            })
                          }
                        >
                          + Add
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Spot divider */}
        <div className="spot-divider">
          <span className="spot-price-tag">
            {Number(spot).toLocaleString("en-IN", { minimumFractionDigits: 2 })}
          </span>
        </div>

        {/* PE side — strikes below spot */}
        <div className="chain-half">
          <table className="chain-table">
            <tbody>
              {peStrikes.map((key) => {
                const s = strikes[key];
                if (!s) return null;
                const strikeNum = Number(key);
                const ltp = s.pe_token ? prices[s.pe_token] || 0 : 0;
                const dist = spot
                  ? (((strikeNum - spot) / spot) * 100).toFixed(2)
                  : "0.00";

                return (
                  <tr key={key}>
                    <td className="strike-cell">
                      {strikeNum.toLocaleString()}
                      <span className="dist-tag">{dist}%</span>
                    </td>
                    <td className="price-cell pe-price">
                      {ltp ? `₹${Number(ltp).toFixed(2)}` : "—"}
                    </td>
                    <td>
                      {s.pe_symbol && (
                        <button
                          className="btn btn-sm btn-pe"
                          onClick={() =>
                            onAdd({
                              ...s,
                              strike: strikeNum,
                              side: "PE",
                              exchange: data.exchange,
                              expiry: data.expiry,
                              indexId,
                            })
                          }
                        >
                          + Add
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
