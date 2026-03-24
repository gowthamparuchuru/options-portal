export default function Basket({ items, onRemove, onExecute, disabled }) {
  return (
    <div className="panel">
      <div className="panel-header">
        Basket
        <span style={{ fontWeight: 400, color: "var(--text-dim)" }}>
          {items.length} order{items.length !== 1 ? "s" : ""}
        </span>
      </div>

      <div className="panel-body">
        {items.length === 0 ? (
          <div className="basket-empty">
            Click +CE or +PE on the option chain to add orders.
          </div>
        ) : (
          items.map((item, idx) => (
            <div className="basket-item" key={idx}>
              <div className="basket-item-info">
                <span className="basket-item-sym">
                  {item.option_type} — {item.strike.toLocaleString()}
                </span>
                <span className="basket-item-meta">
                  {item.symbol} &middot; {item.lots} lot{item.lots > 1 ? "s" : ""} (
                  {item.lots * item.lot_size} qty)
                </span>
              </div>
              <button
                className="btn btn-sm btn-danger"
                onClick={() => onRemove(idx)}
                disabled={disabled}
              >
                ✕
              </button>
            </div>
          ))
        )}
      </div>

      {items.length > 0 && (
        <div className="basket-footer">
          <button
            className="btn btn-primary"
            style={{ width: "100%" }}
            onClick={onExecute}
            disabled={disabled || items.length === 0}
          >
            {disabled ? "Executing..." : `Execute ${items.length} SELL Order${items.length > 1 ? "s" : ""}`}
          </button>
        </div>
      )}
    </div>
  );
}
