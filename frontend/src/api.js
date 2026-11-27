// Tiny fetch wrapper: JSON in/out, and errors carry the server's message.
async function request(path, options = {}) {
  let resp;
  try {
    resp = await fetch(path, options);
  } catch {
    throw new Error("Can't reach the server. Is the backend running?");
  }
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.error || data.detail || `Request failed (${resp.status})`);
  return data;
}

export function query(params) {
  const q = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") q.set(k, v);
  });
  const s = q.toString();
  return s ? `?${s}` : "";
}

export const api = {
  status: () => request("/api/status"),
  filters: () => request("/api/filters"),
  summary: (filters) => request(`/api/summary${query(filters)}`),
  changes: (filters) => request(`/api/changes${query(filters)}`),
  reviews: (params) => request(`/api/reviews${query(params)}`),
  retry: (runId) => request(`/api/runs/${runId}/retry`, { method: "POST" }),
  ask: (question) =>
    request("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    }),
  importCsv: (file) => {
    const form = new FormData();
    form.append("file", file);
    return request("/api/import", { method: "POST", body: form });
  },
};
