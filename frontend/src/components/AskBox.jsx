import { useState } from "react";
import { api } from "../api";

const EXAMPLES = ["What are the top complaints in Pune?", "How many reviews complain about refunds?", "Which city has the most delivery complaints?"];

// Ask a question in plain English. The server computes the numbers from the database;
// the answer links to the reviews it is based on.
export default function AskBox({ onShowReviews }) {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  const ask = async (q) => {
    const text = (q ?? question).trim();
    if (!text) return;
    setQuestion(text);
    setBusy(true);
    setError(null);
    try {
      setResult(await api.ask(text));
    } catch (e) {
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card ask">
      <form onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about the reviews, e.g. “What are the top complaints on iOS?”"
          aria-label="Ask a question about the reviews"
        />
        <button className="primary" disabled={busy || !question.trim()}>{busy ? "Thinking…" : "Ask"}</button>
      </form>
      {!result && !error && (
        <div className="examples">
          Try:{" "}
          {EXAMPLES.map((q) => (
            <button key={q} className="link" onClick={() => ask(q)}>{q}</button>
          ))}
        </div>
      )}
      {error && <div className="state error">{error}</div>}
      {result && (
        <div className="answer">
          <p>{result.answer}</p>
          {result.evidence_review_ids.length > 0 && (
            <button
              className="link"
              onClick={() => onShowReviews({ ids: result.evidence_review_ids, title: `Example reviews for “${question}”` })}
            >
              Read {result.evidence_review_ids.length} example review{result.evidence_review_ids.length > 1 ? "s" : ""} →
            </button>
          )}
          <span className="muted small"> Numbers are counted from the database, not written by the AI.</span>
        </div>
      )}
    </div>
  );
}
