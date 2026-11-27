import { useRef, useState } from "react";
import { api } from "../api";

export default function ImportButton({ onImported, primary = false }) {
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);

  const onFile = async (e) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow picking the same file again
    if (!file) return;
    setBusy(true);
    setMessage(null);
    try {
      const res = await api.importCsv(file);
      const warn = res.warnings?.length ? ` (${res.warnings.length} warning(s): ${res.warnings.slice(0, 2).join("; ")})` : "";
      setMessage({ ok: true, text: `Imported ${res.imported} reviews${warn}.` });
      onImported();
    } catch (err) {
      setMessage({ ok: false, text: err.message });
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="import">
      <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={onFile} />
      <button className={primary ? "primary" : ""} disabled={busy} onClick={() => input.current.click()}>
        {busy ? "Importing…" : "Import reviews CSV"}
      </button>
      {message && <span className={message.ok ? "import-ok" : "import-err"}>{message.text}</span>}
    </span>
  );
}
