import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import AskBox from "./components/AskBox";
import ChangesPanel from "./components/ChangesPanel";
import FilterBar from "./components/FilterBar";
import ImportButton from "./components/ImportButton";
import ReviewPanel from "./components/ReviewPanel";
import StatusBanner from "./components/StatusBanner";
import ThemeTable from "./components/ThemeTable";

const RUNNING = ["queued", "discovering", "classifying"];
const NO_FILTERS = { city: "", platform: "", month: "", rating: "" };

export default function App() {
  const [status, setStatus] = useState(null);
  const [statusError, setStatusError] = useState(null);
  const [filters, setFilters] = useState(NO_FILTERS);
  const [options, setOptions] = useState(null);
  const [summary, setSummary] = useState(null);
  const [summaryError, setSummaryError] = useState(null);
  const [loading, setLoading] = useState(false);
  // What the review list shows: a theme / sub-theme / sentiment, or the reviews behind an answer.
  const [selection, setSelection] = useState(null);
  const reviewsRef = useRef(null);

  const running = RUNNING.includes(status?.latest_run?.status);

  const loadStatus = useCallback(() => {
    api.status().then((s) => { setStatus(s); setStatusError(null); }).catch((e) => setStatusError(e.message));
  }, []);

  // Poll the analysis status: every 2s while themes are being found, every 15s otherwise.
  useEffect(() => {
    loadStatus();
    const id = setInterval(loadStatus, running ? 2000 : 15000);
    return () => clearInterval(id);
  }, [loadStatus, running]);

  const reviewCount = status?.review_count ?? 0;
  const processed = status?.latest_run?.processed_reviews ?? 0;

  useEffect(() => {
    if (reviewCount) api.filters().then(setOptions).catch(() => {});
  }, [reviewCount]);

  // Reload the numbers when filters change, and as batches finish while a run is in progress.
  useEffect(() => {
    if (!reviewCount) return;
    setLoading(true);
    api.summary(filters)
      .then((s) => { setSummary(s); setSummaryError(null); })
      .catch((e) => setSummaryError(e.message))
      .finally(() => setLoading(false));
  }, [filters, reviewCount, processed]);

  const select = (sel) => {
    setSelection(sel);
    setTimeout(() => reviewsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
  };

  const onImported = () => { setSelection(null); setFilters(NO_FILTERS); loadStatus(); };

  if (statusError && !status) {
    return (
      <Shell>
        <div className="card state error">
          <h2>The dashboard can't reach its server</h2>
          <p>{statusError}</p>
          <button onClick={loadStatus}>Try again</button>
        </div>
      </Shell>
    );
  }
  if (!status) return <Shell><div className="card state">Loading…</div></Shell>;

  return (
    <Shell actions={reviewCount > 0 && <HeaderActions running={running} onImported={onImported} />}>
      <StatusBanner status={status} onChanged={loadStatus} />

      {reviewCount === 0 ? (
        <div className="card state empty">
          <h2>No reviews yet</h2>
          <p>
            Import a CSV of app-store reviews with the columns{" "}
            <code>review_id, date, city, platform, rating, review_text</code> (up to 1,000 rows). Themes are
            found automatically; this takes a minute or two.
          </p>
          <ImportButton onImported={onImported} primary />
        </div>
      ) : (
        <>
          <FilterBar filters={filters} options={options} onChange={(f) => { setFilters(f); }} />
          {summaryError && <div className="card state error">Couldn't load the numbers: {summaryError}</div>}
          {summary && <Kpis summary={summary} onSelect={select} />}
          {status.llm_configured && summary?.themes.length > 0 && <AskBox onShowReviews={select} />}
          <div className="grid">
            <ThemeTable
              summary={summary}
              loading={loading}
              running={running}
              selection={selection}
              onSelect={select}
            />
            <ChangesPanel filters={filters} refreshKey={processed} onSelect={select} />
          </div>
          <div ref={reviewsRef}>
            <ReviewPanel selection={selection} filters={filters} onClear={() => setSelection(null)} />
          </div>
        </>
      )}
    </Shell>
  );
}

function Shell({ children, actions }) {
  return (
    <div className="app">
      <header>
        <div>
          <h1>Zesto Review Insights</h1>
          <p className="subtitle">What customers are saying in app-store reviews, by theme</p>
        </div>
        <div className="header-actions">{actions}</div>
      </header>
      <main>{children}</main>
    </div>
  );
}

function HeaderActions({ running, onImported }) {
  return (
    <>
      <ImportButton onImported={onImported} />
      <a className={`button ${running ? "disabled" : ""}`} href="/api/export/labels.csv" download>
        Export labels CSV
      </a>
    </>
  );
}

function Kpis({ summary, onSelect }) {
  const { total_reviews: total, analyzed_reviews: analyzed, reviews_with_negative: neg, themes } = summary;
  const topNeg = [...themes].sort((a, b) => b.negative - a.negative)[0];
  const topPos = [...themes].sort((a, b) => b.positive - a.positive)[0];
  const pct = (n, d) => (d ? Math.round((100 * n) / d) : 0);
  return (
    <div className="kpis">
      <div className="kpi">
        <span className="kpi-label">Reviews</span>
        <span className="kpi-value">{total}</span>
        {analyzed < total && <span className="kpi-note">{analyzed} analysed so far</span>}
      </div>
      <div className="kpi">
        <span className="kpi-label">Have at least one complaint</span>
        <span className="kpi-value neg">{pct(neg, analyzed)}%</span>
        <span className="kpi-note">{neg} of {analyzed} reviews</span>
      </div>
      <button className="kpi clickable" disabled={!topNeg} onClick={() => onSelect({ theme: topNeg, sentiment: "negative" })}>
        <span className="kpi-label">Most complained about</span>
        <span className="kpi-value small">{topNeg?.name ?? "—"}</span>
        {topNeg && <span className="kpi-note">{topNeg.negative} negative reviews →</span>}
      </button>
      <button className="kpi clickable" disabled={!topPos?.positive} onClick={() => onSelect({ theme: topPos, sentiment: "positive" })}>
        <span className="kpi-label">Most praised</span>
        <span className="kpi-value small">{topPos?.positive ? topPos.name : "—"}</span>
        {topPos?.positive > 0 && <span className="kpi-note">{topPos.positive} positive reviews →</span>}
      </button>
    </div>
  );
}
