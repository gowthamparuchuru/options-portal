import { useState, useEffect, useCallback, useRef } from "react";
import LoginStatus from "./components/LoginStatus";
import LiveClock from "./components/LiveClock";
import IndexSelector from "./components/IndexSelector";
import OptionChain from "./components/OptionChain";
import SpotChart from "./components/SpotChart";
import Basket from "./components/Basket";
import LotModal from "./components/LotModal";
import ConfirmModal from "./components/ConfirmModal";
import OrderTracker from "./components/OrderTracker";

export default function App() {
  const [auth, setAuth] = useState({ checked: false, ok: false, error: null });
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [chainActive, setChainActive] = useState(false);
  const [basket, setBasket] = useState([]);
  const [modal, setModal] = useState(null);
  const [editModal, setEditModal] = useState(null);
  const [showConfirm, setShowConfirm] = useState(false);
  const [spotPrice, setSpotPrice] = useState(null);
  const [execId, setExecId] = useState(null);
  const [funds, setFunds] = useState(null);
  const [margin, setMargin] = useState({
    total_margin: 0, span: 0, exposure: 0,
    margin_benefit: 0, option_premium: 0,
    available: null,
    loading: false, error: null,
  });
  const marginTimer = useRef(null);

  const fetchFunds = useCallback(() => {
    fetch("/api/orders/funds")
      .then((r) => r.json())
      .then((d) => { if (d.available != null) setFunds(d); })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetch("/api/auth/status")
      .then(async (r) => {
        const d = await r.json();
        if (r.ok && d.authenticated) {
          setAuth({ checked: true, ok: true, error: null });
          fetchFunds();
        } else {
          const reason = d.error || `Broker responded with HTTP ${r.status}`;
          setAuth({ checked: true, ok: false, error: reason });
        }
      })
      .catch((e) =>
        setAuth({ checked: true, ok: false, error: `Network error: ${e.message}` })
      );
  }, [fetchFunds]);

  const openModal = useCallback((strike) => setModal(strike), []);

  const addToBasket = useCallback((item) => {
    setBasket((prev) => [...prev, item]);
    setModal(null);
  }, []);

  const removeFromBasket = useCallback((idx) => {
    setBasket((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const openEditModal = useCallback((idx) => {
    setEditModal({ idx, item: basket[idx] });
  }, [basket]);

  const updateBasketItem = useCallback((idx, newLots) => {
    setBasket((prev) =>
      prev.map((item, i) => (i === idx ? { ...item, lots: newLots } : item))
    );
    setEditModal(null);
  }, []);

  useEffect(() => {
    if (marginTimer.current) clearTimeout(marginTimer.current);

    if (basket.length === 0) {
      setMargin((m) => ({
        ...m, total_margin: 0, span: 0, exposure: 0,
        margin_benefit: 0, option_premium: 0, loading: false, error: null,
      }));
      return;
    }

    setMargin((m) => ({ ...m, loading: true, error: null }));

    marginTimer.current = setTimeout(async () => {
      try {
        const marginOrders = basket.map((item) => ({
          exchange: item.exchange,
          index_id: item.index_id,
          strike: item.strike,
          option_type: item.option_type,
          lots: item.lots,
          lot_size: item.lot_size,
          expiry: item.expiry,
        }));

        const [marginRes, fundsRes] = await Promise.all([
          fetch("/api/orders/margin", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ orders: marginOrders }),
          }),
          fetch("/api/orders/funds"),
        ]);
        const data = await marginRes.json();
        const fundsData = await fundsRes.json();
        if (fundsData.available != null) setFunds(fundsData);

        if (data.error) {
          setMargin((m) => ({ ...m, loading: false, error: data.error }));
        } else {
          setMargin({
            total_margin: data.total_margin || 0,
            span: data.span || 0,
            exposure: data.exposure || 0,
            margin_benefit: data.margin_benefit || 0,
            option_premium: data.option_premium || 0,
            available: fundsData.available ?? null,
            loading: false,
            error: null,
          });
        }
      } catch (e) {
        setMargin((m) => ({ ...m, loading: false, error: e.message }));
      }
    }, 500);

    return () => {
      if (marginTimer.current) clearTimeout(marginTimer.current);
    };
  }, [basket]);

  const executeBasket = useCallback(async () => {
    if (basket.length === 0) return;
    setShowConfirm(false);
    const res = await fetch("/api/orders/execute", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ orders: basket }),
    });
    const data = await res.json();
    if (data.execution_id) {
      setExecId(data.execution_id);
    }
  }, [basket]);

  return (
    <div className="app">
      <header className="header">
        <h1>Options Portal</h1>
        <LiveClock />
        <LoginStatus auth={auth} />
      </header>

      {auth.checked && !auth.ok && (
        <div className="error-banner">
          Shoonya broker login failed: {auth.error || "Unknown error"}. Trading
          features disabled.
        </div>
      )}

      {auth.ok && (
        <main className="main">
          <div className="controls">
            <IndexSelector value={selectedIndex} onChange={setSelectedIndex} />
            <button
              className="btn btn-primary"
              disabled={!selectedIndex}
              onClick={() => setChainActive(true)}
            >
              Show Option Chain
            </button>
            {funds && (
              <div className="funds-badge">
                <span className="funds-label">Available Margin</span>
                <span className="funds-value">
                  {Number(funds.available).toLocaleString("en-IN", { maximumFractionDigits: 0 })}
                </span>
              </div>
            )}
          </div>

          <div className="content-grid">
            <div className="chart-column">
              {chainActive && selectedIndex && (
                <>
                  <div className="panel trade-note-box">
                    <div className="trade-note-title">
                      {selectedIndex === "SENSEX" ? "SENSEX" : "NIFTY"} Trade Parameters
                    </div>
                    <div className="trade-note-row">
                      <span className="trade-note-label">Safe Variance</span>
                      <span className="trade-note-value">
                        {selectedIndex === "SENSEX" ? "3%" : "2.5%"}
                      </span>
                    </div>
                    <div className="trade-note-row">
                      <span className="trade-note-label">Premium Target</span>
                      <span className="trade-note-value">
                        {selectedIndex === "SENSEX" ? "₹2.5" : "₹1.5"}
                      </span>
                    </div>
                  </div>
                  <SpotChart indexId={selectedIndex} spotPrice={spotPrice} />
                </>
              )}
            </div>

            <div className="chain-panel">
              {chainActive && selectedIndex && (
                <OptionChain
                  indexId={selectedIndex}
                  onAdd={openModal}
                  onSpotUpdate={setSpotPrice}
                />
              )}
            </div>

            <div className="side-panel">
              <Basket
                items={basket}
                onRemove={removeFromBasket}
                onEdit={openEditModal}
                onExecute={() => setShowConfirm(true)}
                disabled={!!execId}
                margin={margin}
              />
              {execId && <OrderTracker execId={execId} />}
            </div>
          </div>
        </main>
      )}

      {modal && (
        <LotModal
          strike={modal}
          onConfirm={addToBasket}
          onClose={() => setModal(null)}
        />
      )}

      {editModal && (
        <EditLotModal
          item={editModal.item}
          onConfirm={(newLots) => updateBasketItem(editModal.idx, newLots)}
          onClose={() => setEditModal(null)}
        />
      )}

      {showConfirm && (
        <ConfirmModal
          items={basket}
          margin={margin}
          onConfirm={executeBasket}
          onClose={() => setShowConfirm(false)}
        />
      )}
    </div>
  );
}

function EditLotModal({ item, onConfirm, onClose }) {
  const [lots, setLots] = useState(item.lots);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (lots < 1) return;
    onConfirm(Number(lots));
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>
          Edit {item.option_type} — Strike {item.strike.toLocaleString()}
        </h3>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label>Symbol</label>
            <input value={item.symbol} readOnly />
          </div>
          <div className="modal-field">
            <label>Lot Size</label>
            <input value={item.lot_size} readOnly />
          </div>
          <div className="modal-field">
            <label>Number of Lots</label>
            <div className="lot-stepper">
              <button
                type="button"
                className="lot-stepper-btn"
                onClick={() => setLots((l) => Math.max(1, Number(l) - 1))}
                disabled={lots <= 1}
              >
                −
              </button>
              <input
                type="number"
                min="1"
                max="100"
                value={lots}
                onChange={(e) => setLots(e.target.value)}
                className="lot-stepper-input"
                autoFocus
              />
              <button
                type="button"
                className="lot-stepper-btn"
                onClick={() => setLots((l) => Math.min(100, Number(l) + 1))}
                disabled={lots >= 100}
              >
                +
              </button>
            </div>
          </div>
          <div style={{ fontSize: 13, color: "var(--text-dim)", marginBottom: 16 }}>
            Total quantity: {lots * item.lot_size}
          </div>
          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary">
              Update
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
