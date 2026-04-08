export default function IndexQuotes({ items }) {
  if (!items || items.length === 0) return null;

  return (
    <div className="panel index-quotes">
      <div className="panel-header">Market Indices</div>
      <div className="index-quotes-body">
        {items.map((item) => (
          <div key={item.key} className="index-quote-row">
            <span className="index-quote-label">{item.label}</span>
            <span className="index-quote-price">
              {item.price != null
                ? Number(item.price).toLocaleString("en-IN", {
                    minimumFractionDigits: 2,
                    maximumFractionDigits: 2,
                  })
                : "—"}
            </span>
            <span
              className={`index-quote-change ${
                item.change > 0 ? "up" : item.change < 0 ? "down" : ""
              }`}
            >
              {item.change != null
                ? `${item.change > 0 ? "+" : ""}${item.change.toFixed(2)}%`
                : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
