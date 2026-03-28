export default function ConfirmModal({ items, margin, onConfirm, onClose }) {
  const fmt = (v) =>
    Number(v).toLocaleString("en-IN", {
      style: "currency",
      currency: "INR",
      maximumFractionDigits: 0,
    });

  const totalQty = items.reduce((sum, i) => sum + i.lots * i.lot_size, 0);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal confirm-modal" onClick={(e) => e.stopPropagation()}>
        <h3 className="confirm-title">Confirm Execution</h3>

        <p className="confirm-subtitle">
          You are about to place <strong>{items.length} SELL</strong> order{items.length > 1 ? "s" : ""} with
          total quantity <strong>{totalQty}</strong>.
        </p>

        <div className="confirm-orders">
          {items.map((item, idx) => (
            <div className="confirm-order-row" key={idx}>
              <span className="confirm-order-sym">
                {item.option_type} {item.strike.toLocaleString()}
              </span>
              <span className="confirm-order-detail">
                {item.lots} lot{item.lots > 1 ? "s" : ""} ({item.lots * item.lot_size} qty)
              </span>
            </div>
          ))}
        </div>

        {margin && margin.total_margin > 0 && (
          <div className="confirm-margin">
            Required Margin: <strong>{fmt(margin.total_margin)}</strong>
          </div>
        )}

        <div className="confirm-warning">
          This action cannot be undone. Orders will be placed immediately.
        </div>

        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn btn-danger confirm-execute-btn"
            onClick={onConfirm}
          >
            Confirm & Execute
          </button>
        </div>
      </div>
    </div>
  );
}
