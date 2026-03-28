export default function Basket({ items, onRemove, onEdit, onExecute, disabled, margin }) {
  const fmt = (v) =>
    Number(v).toLocaleString("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    });

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
              <div className="basket-item-actions">
                <button
                  className="btn btn-sm btn-edit"
                  onClick={() => onEdit(idx)}
                  disabled={disabled}
                  title="Edit lots"
                >
                  ✎
                </button>
                <button
                  className="btn btn-sm btn-danger"
                  onClick={() => onRemove(idx)}
                  disabled={disabled}
                  title="Remove"
                >
                  ✕
                </button>
              </div>
            </div>
          ))
        )}
      </div>

      {items.length > 0 && margin && (
        <div className="margin-section">
          {margin.loading ? (
            <div className="margin-loading">Calculating margin...</div>
          ) : margin.error ? (
            <div className="margin-error">{margin.error}</div>
          ) : margin.total_margin > 0 ? (
            <>
              <div className="margin-row margin-total">
                <span>Required Margin</span>
                <span>{fmt(margin.total_margin)}</span>
              </div>
              <div className="margin-row">
                <span>SPAN</span>
                <span>{fmt(margin.span)}</span>
              </div>
              <div className="margin-row">
                <span>Exposure</span>
                <span>{fmt(margin.exposure)}</span>
              </div>
              {margin.margin_benefit > 0 && (
                <div className="margin-row margin-benefit">
                  <span>Hedge Benefit</span>
                  <span>-{fmt(margin.margin_benefit)}</span>
                </div>
              )}
              {margin.option_premium < 0 && (
                <div className="margin-row margin-premium">
                  <span>Premium Received</span>
                  <span>{fmt(Math.abs(margin.option_premium))}</span>
                </div>
              )}
            </>
          ) : null}
        </div>
      )}

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
