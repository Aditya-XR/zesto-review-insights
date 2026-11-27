import { useState } from "react";
import { api } from "../api";

const STEP = {
  queued: "Waiting to start…",
  discovering: "Reading the reviews to work out the themes…",
  classifying: "Tagging each review with its themes and sentiment…",
};

// Tells Priya what state the analysis is in: missing key, running, failed.
export default function StatusBanner({ status, onChanged }) {
  const [retryError, setRetryError] = useState(null);
  const run = status.latest_run;

  const retry = () => {
    setRetryError(null);
    api.retry(run.id).then(onChanged).catch((e) => setRetryError(e.message));
  };

  if (!status.llm_configured) {
    return (
      <div className="banner warn">
        <strong>No LLM key configured.</strong> Reviews can be imported and browsed, but themes can't be found until
        a key is set. Put <code>GEMINI_API_KEY</code> (or <code>OPENAI_API_KEY</code> / <code>GROQ_API_KEY</code>)
        in <code>.env</code> and run <code>docker compose up</code> again; waiting imports are then analysed automatically.
      </div>
    );
  }
  if (!run) return null;
  if (STEP[run.status]) {
    const pct = run.total_reviews ? Math.round((100 * run.processed_reviews) / run.total_reviews) : 0;
    return (
      <div className="banner info">
        <div>
          <strong>Finding themes in {run.total_reviews} reviews.</strong> {STEP[run.status]}{" "}
          {run.status === "classifying" && `${run.processed_reviews} of ${run.total_reviews} done.`} The numbers below
          fill in as it goes.
        </div>
        <div className="progress"><div style={{ width: `${Math.max(pct, 3)}%` }} /></div>
      </div>
    );
  }
  if (run.status === "failed") {
    return (
      <div className="banner error">
        <strong>Theme-finding stopped before it finished.</strong> {run.processed_reviews} of {run.total_reviews} reviews
        were analysed. <span className="muted">{run.error}</span>{" "}
        <button onClick={retry}>Retry the rest</button>
        {retryError && <div>{retryError}</div>}
      </div>
    );
  }
  if (run.status === "needs_key") {
    return (
      <div className="banner warn">
        <strong>{run.total_reviews} reviews are waiting for analysis.</strong> A key is configured now.{" "}
        <button onClick={retry}>Analyse them</button>
        {retryError && <div>{retryError}</div>}
      </div>
    );
  }
  return null;
}
