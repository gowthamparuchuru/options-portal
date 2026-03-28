import { useState } from "react";

export default function LotModal({ strike, onConfirm, onClose }) {
  const [lots, setLots] = useState(1);
  const side = strike.side;
  const symbol = side === "CE" ? strike.ce_symbol : strike.pe_symbol;
  const token = side === "CE" ? strike.ce_token : strike.pe_token;
  const lotSize = side === "CE" ? strike.ce_lotsize : strike.pe_lotsize;

  const handleSubmit = (e) => {
    e.preventDefault();
    if (lots < 1) return;
    onConfirm({
      symbol,
      token,
      exchange: strike.exchange,
      strike: strike.strike,
      option_type: side,
      lots: Number(lots),
      lot_size: lotSize,
      index_id: strike.indexId,
      expiry: strike.expiry,
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>
          Add {side} — Strike {strike.strike.toLocaleString()}
        </h3>
        <form onSubmit={handleSubmit}>
          <div className="modal-field">
            <label>Symbol</label>
            <input value={symbol} readOnly />
          </div>
          <div className="modal-field">
            <label>Lot Size</label>
            <input value={lotSize} readOnly />
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
            Total quantity: {lots * lotSize}
          </div>
          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn btn-primary">
              Add to Basket
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
