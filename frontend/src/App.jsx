import { useState, useEffect, useCallback } from "react";
import LoginStatus from "./components/LoginStatus";
import IndexSelector from "./components/IndexSelector";
import OptionChain from "./components/OptionChain";
import Basket from "./components/Basket";
import LotModal from "./components/LotModal";
import OrderTracker from "./components/OrderTracker";

export default function App() {
  const [auth, setAuth] = useState({ checked: false, ok: false, error: null });
  const [selectedIndex, setSelectedIndex] = useState(null);
  const [chainActive, setChainActive] = useState(false);
  const [basket, setBasket] = useState([]);
  const [modal, setModal] = useState(null);
  const [execId, setExecId] = useState(null);

  useEffect(() => {
    fetch("/api/auth/status")
      .then((r) => r.json())
      .then((d) => setAuth({ checked: true, ok: d.authenticated, error: d.error }))
      .catch((e) => setAuth({ checked: true, ok: false, error: e.message }));
  }, []);

  const openModal = useCallback((strike) => setModal(strike), []);

  const addToBasket = useCallback(
    (item) => {
      setBasket((prev) => [...prev, item]);
      setModal(null);
    },
    []
  );

  const removeFromBasket = useCallback((idx) => {
    setBasket((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const executeBasket = useCallback(async () => {
    if (basket.length === 0) return;
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
        <LoginStatus auth={auth} />
      </header>

      {auth.checked && !auth.ok && (
        <div className="error-banner">
          Login failed: {auth.error || "Unknown error"}. Trading features disabled.
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
          </div>

          <div className="content-grid">
            <div className="chain-panel">
              {chainActive && selectedIndex && (
                <OptionChain indexId={selectedIndex} onAdd={openModal} />
              )}
            </div>

            <div className="side-panel">
              <Basket
                items={basket}
                onRemove={removeFromBasket}
                onExecute={executeBasket}
                disabled={!!execId}
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
    </div>
  );
}
