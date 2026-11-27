import { useEffect, useState } from "react";
import { api } from "../api";
import { monthLabel } from "./FilterBar";

const SENTIMENTS = [
  ["", "All"],
  ["negative", "Negative"],
  ["positive", "Positive"],
  ["neutral", "Neutral"],
];

// The reviews behind a number. `selection` is {theme, sub?, sentiment?, month?}, {noTheme}, or
// {ids, title} for the evidence behind an answer from the "Ask" box.
export default function ReviewPanel({ selection, filters, onClear }) {
  const [sentiment, setSentiment] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState(null);
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setSentiment(selection?.sentiment ?? "");
    setSearch("");
    setPage(1);
  }, [selection]);

  const params = selection && {
    ...filters,
    ...(selection.month ? { month: selection.month } : {}),
    theme: selection.theme?.id,
    sub_theme: selection.sub?.id,
    sentiment: selection.theme ? sentiment : "",
    no_theme: selection.noTheme ? "1" : "",
    ids: selection.ids?.join(","),
    q: search,
    page,
  };
  const key = JSON.stringify(params);

  useEffect(() => {
    if (!params) return;
    setLoading(true);
    api
      .reviews(params)
      .then((d) => {
        setData(d);
        setItems((prev) => (page === 1 ? d.results : [...prev, ...d.results]));
        setError(null);
      })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!selection) {
    return (
      <div className="card reviews state empty">
        Click a theme, a sub-theme or a coloured bar above to read the reviews behind it.
      </div>
    );
  }

  const title = selection.ids
    ? selection.title
    : selection.noTheme
      ? "Reviews with no clear topic"
      : [selection.theme.name, selection.sub?.name].filter(Boolean).join(" › ");
  const scope = [
    selection.month || filters.month ? monthLabel(selection.month || filters.month) : null,
    filters.city,
    filters.platform,
    filters.rating ? `${filters.rating.replace(",", "–")} ★` : null,
  ].filter(Boolean);

  return (
    <div className="card reviews">
      <div className="card-head">
        <h2>
          {title} <span className="muted">· {data?.count ?? "…"} reviews</span>
        </h2>
        <button className="link" onClick={onClear}>Close</button>
      </div>
      {scope.length > 0 && <p className="muted small">Filtered to {scope.join(", ")}</p>}
      <div className="review-tools">
        {selection.theme && (
          <div className="seg">
            {SENTIMENTS.map(([v, label]) => (
              <button key={v} className={sentiment === v ? "on" : ""} onClick={() => { setSentiment(v); setPage(1); }}>
                {label}
              </button>
            ))}
          </div>
        )}
        <input
          type="search"
          placeholder="Search in these reviews…"
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(1); }}
        />
      </div>
      {error && <div className="state error">Couldn't load reviews: {error}</div>}
      {!error && !loading && items.length === 0 && <div className="state empty">No reviews match.</div>}
      <ul className="review-list">
        {items.map((r) => (
          <ReviewCard key={r.review_id} r={r} themeId={selection.theme?.id} />
        ))}
      </ul>
      {loading && <div className="state">Loading…</div>}
      {data && items.length < data.count && !loading && (
        <button className="more" onClick={() => setPage(page + 1)}>
          Show more ({data.count - items.length} left)
        </button>
      )}
    </div>
  );
}

function ReviewCard({ r, themeId }) {
  const focus = r.mentions.find((m) => m.theme_id === themeId);
  return (
    <li className="review">
      <div className="review-meta">
        <span className="stars" title={`${r.rating ?? "?"} of 5`}>
          {r.rating ? "★".repeat(r.rating) + "☆".repeat(5 - r.rating) : "no rating"}
        </span>
        <span>{r.date}</span>
        <span>{r.city}</span>
        <span>{r.platform === "ios" ? "iOS" : r.platform}</span>
        <span className="muted">{r.review_id}</span>
      </div>
      <p className="review-text">{highlight(r.text, focus?.quote)}</p>
      <div className="chips">
        {!r.analyzed && <span className="chip">not analysed yet</span>}
        {r.analyzed && r.mentions.length === 0 && <span className="chip">no clear topic</span>}
        {r.mentions.map((m) => (
          <span key={m.theme} className={`chip ${m.sentiment} ${m.theme_id === themeId ? "focus" : ""}`} title={m.quote}>
            {m.theme} › {m.sub_theme}
          </span>
        ))}
      </div>
    </li>
  );
}

// Highlight the words the model quoted as evidence for the selected theme, if they're in the text.
function highlight(text, quote) {
  if (!quote) return text;
  const i = text.toLowerCase().indexOf(quote.toLowerCase());
  if (i === -1) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark>{text.slice(i, i + quote.length)}</mark>
      {text.slice(i + quote.length)}
    </>
  );
}
