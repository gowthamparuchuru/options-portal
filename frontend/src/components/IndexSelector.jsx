export default function IndexSelector({ value, onChange }) {
  return (
    <select
      className="select"
      value={value || ""}
      onChange={(e) => onChange(e.target.value || null)}
    >
      <option value="">Select Index...</option>
      <option value="NIFTY">NIFTY 50</option>
      <option value="SENSEX">SENSEX</option>
    </select>
  );
}
