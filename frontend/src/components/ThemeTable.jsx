import { useState } from "react";
import { monthLabel } from "./FilterBar";

// The main view: every theme with how many reviews mention it and how they feel.
// Click a theme, a sub-theme, or a coloured part of a bar to read the reviews behind it.
export default function ThemeTable({ summary, loading, running, selection, onSelect }) {
  const [sort, setSort] = useState("count");
  const [open, setOpen] = useState({});

  if (!summary) return <div className="card themes state">{loading ? "Loading themes…" : ""}</div>;
  const { themes, analyzed_reviews: analyzed, no_theme_reviews: noTheme, months } = summary;
  if (!themes.length) {
    return (
      <div className="card themes state empty">
        {running
          ? "Themes will appear here as soon as the first reviews are analysed."
          : summary.total_reviews
            ? "No themes found for the reviews matching these filters."
            : "No reviews match these filters."}
      </div>
    );
  }
  const sorted = [...themes].sort((a, b) => (sort === "negative" ? b.negative - a.negative : b.count - a.count));
  const max = Math.max(...themes.map((t) => t.count));
  const isSel = (t, sub, sentiment) =>
    selection?.theme?.id === t.id &&
    (selection?.sub?.id ?? null) === (sub?.id ?? null) &&
    (selection?.sentiment ?? null) === (sentiment ?? null);

  return (
    <div className={`card themes ${loading ? "dim" : ""}`}>
      <div className="card-head">
        <h2>Themes</h2>
        <div className="seg">
          <button className={sort === "count" ? "on" : ""} onClick={() => setSort("count")}>Most mentioned</button>
          <button className={sort === "negative" ? "on" : ""} onClick={() => setSort("negative")}>Most complaints</button>
        </div>
      </div>
      <div className="legend">
        <span><i className="dot pos" /> positive</span>
        <span><i className="dot neg" /> negative</span>
        <span><i className="dot neu" /> neutral</span>
        <span className="muted">
          % = share of {analyzed} analysed reviews · trend = complaints per month
          {months.length > 1 ? ` (${monthLabel(months[0])} – ${monthLabel(months[months.length - 1])})` : ""}
        </span>
      </div>
      <table>
        <thead>
          <tr>
            <th>Theme <span className="muted small">(▸ for sub-themes)</span></th>
            <th className="num">Reviews</th>
            <th>Positive / negative</th>
            <th>Complaints trend</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((t) => (
            <ThemeRows
              key={t.id}
              t={t}
              max={max}
              analyzed={analyzed}
              expanded={!!open[t.id]}
              toggle={() => setOpen({ ...open, [t.id]: !open[t.id] })}
              onSelect={onSelect}
              isSel={isSel}
            />
          ))}
          {noTheme > 0 && (
            <tr className={`row ${selection?.noTheme ? "selected" : ""}`}>
              <td>
                <span className="chev-btn" />
                <button className="name muted" onClick={() => onSelect({ noTheme: true })}>
                  No clear topic <span className="small">(“ok”, “👍”…)</span>
                </button>
              </td>
              <td className="num">{noTheme}</td>
              <td colSpan={2} />
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ThemeRows({ t, max, analyzed, expanded, toggle, onSelect, isSel }) {
  return (
    <>
      <tr className={`row ${isSel(t) ? "selected" : ""}`}>
        <td>
          <button className="chev-btn" onClick={toggle} aria-label={expanded ? "Hide sub-themes" : "Show sub-themes"}>
            {expanded ? "▾" : "▸"}
          </button>
          <button className="name" onClick={() => onSelect({ theme: t })} title={t.description}>
            {t.name}
          </button>
          {t.added_during_classification && <span className="badge" title="Not in the first theme list: found while tagging reviews">new topic</span>}
        </td>
        <td className="num">
          {t.count} <span className="muted small">{analyzed ? Math.round((100 * t.count) / analyzed) : 0}%</span>
        </td>
        <td>
          <SentimentBar item={t} width={t.count / max} onPick={(s) => onSelect({ theme: t, sentiment: s })} />
        </td>
        <td>
          <Sparkline
            points={t.monthly.map((m) => m.negative)}
            labels={t.monthly.map((m) => `${monthLabel(m.month)}: ${m.negative} complaints`)}
          />
        </td>
      </tr>
      {expanded &&
        t.sub_themes.map((s) => (
          <tr key={s.id} className={`row sub ${isSel(t, s) ? "selected" : ""}`}>
            <td>
              <button className="name" onClick={() => onSelect({ theme: t, sub: s })}>{s.name}</button>
            </td>
            <td className="num">{s.count}</td>
            <td>
              <SentimentBar item={s} width={s.count / max} onPick={(sent) => onSelect({ theme: t, sub: s, sentiment: sent })} />
            </td>
            <td />
          </tr>
        ))}
    </>
  );
}

function SentimentBar({ item, width, onPick }) {
  const parts = [
    ["positive", item.positive, "pos"],
    ["negative", item.negative, "neg"],
    ["neutral", item.neutral, "neu"],
  ].filter(([, n]) => n > 0);
  return (
    <div className="sbar-wrap">
      <div className="sbar" style={{ width: `${Math.max(width * 100, 6)}%` }}>
        {parts.map(([name, n, cls]) => (
          <button
            key={name}
            className={`seg-${cls}`}
            style={{ flexGrow: n }}
            title={`${n} ${name}: click to read them`}
            onClick={() => onPick(name)}
          />
        ))}
      </div>
      <span className="sbar-label">
        <span className="pos-text">{item.positive}</span> / <span className="neg-text">{item.negative}</span>
      </span>
    </div>
  );
}

function Sparkline({ points, labels }) {
  if (points.length < 2) return <span className="muted">–</span>;
  const w = 90;
  const h = 24;
  const max = Math.max(...points, 1);
  const xy = points.map((p, i) => [(i / (points.length - 1)) * (w - 6) + 3, h - 3 - (p / max) * (h - 6)]);
  return (
    <svg width={w} height={h} className="spark" role="img" aria-label={labels.join(", ")}>
      <title>{labels.join("\n")}</title>
      <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" strokeWidth="1.5" />
      {xy.map(([x, y], i) => (
        <circle key={i} cx={x} cy={y} r={i === xy.length - 1 ? 2.5 : 1.2} />
      ))}
    </svg>
  );
}
