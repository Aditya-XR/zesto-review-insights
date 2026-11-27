import { useEffect, useState } from "react";
import { api } from "../api";
import { monthLabel } from "./FilterBar";

// "What changed": for each theme, the share of a month's reviews that complain about it,
// compared with the month before. Shares, not counts, so a busier month doesn't look worse.
export default function ChangesPanel({ filters, refreshKey, onSelect }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api
      .changes(filters)
      .then((d) => { setData(d); setError(null); })
      .catch((e) => setError(e.message));
  }, [filters, refreshKey]);

  if (error) return <div className="card changes state error">Couldn't load changes: {error}</div>;
  if (!data) return <div className="card changes state">Loading…</div>;
  if (!data.month) return <div className="card changes state empty">No dated reviews to compare.</div>;

  const pts = (x) => `${x > 0 ? "+" : ""}${Math.round(x * 100)} pts`;
  const pct = (x) => `${Math.round(x * 100)}%`;
  const moved = data.themes.filter((t) => Math.abs(t.delta) >= 0.01);
  const worse = moved.filter((t) => t.delta > 0).slice(0, 5);
  const better = moved.filter((t) => t.delta < 0).slice(0, 5);

  const Row = ({ t }) => (
    <li>
      <button className="name" onClick={() => onSelect({ theme: t, sentiment: "negative", month: data.month })}>
        {t.name}
      </button>
      <span className={`delta ${t.delta > 0 ? "neg-text" : "pos-text"}`}>{pts(t.delta)}</span>
      <span className="muted small">
        {pct(t.previous.negative_share)} → {pct(t.current.negative_share)} of reviews complain
      </span>
    </li>
  );

  return (
    <div className="card changes">
      <div className="card-head">
        <h2>What changed in {monthLabel(data.month)}</h2>
      </div>
      <p className="muted small">
        Compared with {monthLabel(data.previous_month)} ({data.reviews_this_month} vs {data.reviews_previous_month} reviews).
        Choose a month in the filters to look at another one.
      </p>
      {data.reviews_previous_month === 0 ? (
        <p className="state empty">No reviews from {monthLabel(data.previous_month)} to compare with.</p>
      ) : (
        <>
          <h3 className="neg-text">Getting worse</h3>
          {worse.length ? (
            <ul className="movers">{worse.map((t) => <Row key={t.id} t={t} />)}</ul>
          ) : (
            <p className="muted small">Nothing got noticeably worse.</p>
          )}
          <h3 className="pos-text">Getting better</h3>
          {better.length ? (
            <ul className="movers">{better.map((t) => <Row key={t.id} t={t} />)}</ul>
          ) : (
            <p className="muted small">Nothing improved noticeably.</p>
          )}
        </>
      )}
    </div>
  );
}
