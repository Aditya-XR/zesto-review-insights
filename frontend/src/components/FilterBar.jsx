const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function monthLabel(m) {
  if (!m) return "";
  const [y, mm] = m.split("-");
  return `${MONTH_NAMES[Number(mm) - 1]} ${y}`;
}

export default function FilterBar({ filters, options, onChange }) {
  const set = (key) => (e) => onChange({ ...filters, [key]: e.target.value });
  const active = Object.values(filters).some(Boolean);
  return (
    <div className="filters">
      <span className="filters-label">Show reviews from</span>
      <select value={filters.city} onChange={set("city")} aria-label="City">
        <option value="">All cities</option>
        {options?.cities.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <select value={filters.platform} onChange={set("platform")} aria-label="Platform">
        <option value="">All platforms</option>
        {options?.platforms.map((p) => <option key={p} value={p}>{p === "ios" ? "iOS" : p[0].toUpperCase() + p.slice(1)}</option>)}
      </select>
      <select value={filters.month} onChange={set("month")} aria-label="Month">
        <option value="">All months</option>
        {options?.months.map((m) => <option key={m} value={m}>{monthLabel(m)}</option>)}
      </select>
      <select value={filters.rating} onChange={set("rating")} aria-label="Rating">
        <option value="">All ratings</option>
        <option value="1,2">1–2 ★ (unhappy)</option>
        <option value="3">3 ★</option>
        <option value="4,5">4–5 ★ (happy)</option>
        {[1, 2, 4, 5].map((r) => <option key={r} value={r}>{r} ★ only</option>)}
      </select>
      {active && <button className="link" onClick={() => onChange({ city: "", platform: "", month: "", rating: "" })}>Clear filters</button>}
    </div>
  );
}
