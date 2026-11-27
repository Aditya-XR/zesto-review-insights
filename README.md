# Zesto Review Insights

A dashboard that tells a product manager what customers are talking about in app-store reviews, how they feel about each topic, and which reviews back up each number.

## How to run it
```bash
cp .env.example .env     # fill in GEMINI_API_KEY (or OPENAI_API_KEY / GROQ_API_KEY)
docker compose up --build
```
UI: http://localhost:3000 · API: http://localhost:8000/api/health

Differences from the starter:
- **A `worker` service** (same image, runs `python manage.py run_worker`) finds themes in the background.
- **Provider:** `GEMINI_API_KEY` with `LLM_MODEL=gemini-3.5-flash-lite` (tested). OpenAI and Groq keys also work: all three use the OpenAI-compatible chat API. If several keys are set, the first one in the order Gemini, OpenAI, Groq is used. If `LLM_MODEL` names a Gemini model but only an OpenAI or Groq key is set, that provider's default model is used.
- **With no key**, the app starts, imports and browses reviews, and shows a "no LLM key" banner. `/api/export/labels.csv` then returns `503`. Imports waiting for a key are analysed automatically once a key is set and the stack restarted.
- **Tests:** `docker compose exec backend python manage.py test` runs 15 tests with a fake LLM, so it needs no key.

## Product
- **Who and what decision:** Priya, the PM, deciding what to fix next and whether last month's fix worked. The table sorts themes by mentions or by complaints. Every number can be clicked to show the reviews behind it, with the supporting phrase highlighted.
- **Feature I chose: "What changed this month".** For each theme it compares the share of reviews that complain about it with the previous month. Shares are used rather than raw counts, so a busier month doesn't look worse. It also adds a complaint-trend sparkline on each theme row.
  - **Why:** the first question a PM asks every week is "what got worse?". A static breakdown can't answer that, and it's what tells her whether a fix worked.
  - Pick any month in the filter to compare that month with the one before it. Clicking a mover opens its complaints for that month.
- **Also built:** the `/api/ask` bonus as an "Ask" box. The LLM only turns the question into a structured query (theme, filters, group-by). Django runs the query and builds the sentence, so every number comes from the database. Questions the data can't answer, such as revenue, get "I can't answer that…", and the answer links to evidence reviews.
- **Left out on purpose:**
  - Login and users: it's an internal tool.
  - Editing or merging themes by hand.
  - Deleting imports.
  - A rule-based fallback when there's no LLM: it would be inaccurate, and a clear message is better.
  - Charts beyond sparklines.

## Architecture
- **Data model** (`backend/reviews/models.py`):
  - `AnalysisRun`: one per import, with status `queued → discovering → classifying → done/failed/needs_key` and progress counts. It doubles as the job queue.
  - `Review`: CSV fields plus `analyzed_at`.
  - `Theme`, and `SubTheme` (FK to `Theme`).
  - `Mention`: `review`, `theme`, `sub_theme`, `sentiment`, `quote`. There is a unique constraint on (review, theme), so the model enforces the export's "one row per (review, theme)".
  - A review that has been analysed but has no mentions is exported as `NONE`.
- **Theme finding** (`pipeline.py`) runs in two LLM stages:
  1. **Discover:** the LLM reads all reviews (up to 400 sampled) and proposes themes and sub-themes. Nothing is hard-coded, so new topics become new themes. Existing themes are passed in so later imports reuse them.
  2. **Classify:** batches of 25 reviews, 3 in parallel. Each review gets 0–n themes, each with one sub-theme, a sentiment and a supporting quote. If something fits no theme, the classifier may create one (shown with a "new topic" badge).
- **Where it runs:** in the worker, never in the request.
  - `POST /api/import` only validates and stores the CSV, then returns `202`.
  - The worker claims runs with `SELECT … FOR UPDATE SKIP LOCKED`, so Postgres is the queue and there's no Redis or Celery.
  - **Why:** LLM calls take tens of seconds and hit rate limits. The worker retries `429`/`5xx` with backoff and makes up to 3 passes over failed batches. It resumes half-finished runs after a restart, and the API stays fast in the meantime.
- **Endpoints:**

  | Endpoint | What it does |
  |---|---|
  | `GET /api/health` | Health check |
  | `POST /api/import` | Upload a CSV |
  | `GET /api/export/labels.csv` | `202` while running, `200` when done |
  | `POST /api/ask` | The Ask box |
  | `GET /api/status` | Key status and progress of the latest run |
  | `GET /api/filters` | Values for the filter dropdowns |
  | `GET /api/summary?city&platform&month&rating` | Themes, sub-themes, counts, sentiment split, monthly series |
  | `GET /api/changes?…` | Data for "What changed" |
  | `GET /api/reviews?theme&sub_theme&sentiment&q&page…` | The drill-down list |
  | `POST /api/runs/<id>/retry` | Retry a failed run |

## How well it works
Import `data/reviews.csv` through Docker, then `python3 check_labels.py my_labels.csv` (gemini-3.5-flash-lite; the export was ready about 30 s after import):
```
theme precision 0.97  recall 0.98  F1 0.98
sentiment accuracy 1.00 on 434 shared (review, theme) pairs
```
- Results vary from run to run because discovery is an LLM call. On earlier runs with the final prompt, F1 was 0.92: discovery merged pricing and payments into one theme. Before I tightened the discovery prompt against compound themes ("X and Y"), F1 was 0.85.
- **Case it gets wrong (1): VAL-00068**, "The tracking map is very smooth and reliable…". This was tagged *App › Search and Navigation*; ours is *Delivery › Order Tracking*. The live map sits in the app, so the model files it as app UI.
- **Case it gets wrong (2): VAL-00102 / VAL-00163**, "Cashback promised never got credited". This was tagged *Customer Support › Refund Handling*; ours is *Pricing & Offers › Coupons & Discounts*. Money not arriving reads like a refund.
- **Related pattern:** the model sometimes adds an implied theme, e.g. "refund for a spoilt item" also gets Food Quality.

## AI tools
- **Claude Code (Claude Opus 5.5):** wrote most of the backend, frontend, tests and this README from my direction. It also iterated on the prompts against `check_labels.py` and drove the browser to check the UI.
- **Gemini (gemini-3.5-flash-lite):** the LLM the app uses at runtime.

## With another day
- **Make discovery more stable:** run discovery 2–3 times and merge the results, or cluster review embeddings first and have the LLM name the clusters. Also let Priya rename or merge themes in the UI.
- **Run a second check on each review** to catch dropped or extra themes.
- **Group "new topic" themes:** themes the classifier creates on its own are not de-duplicated across batches yet.
- **Score reliability per theme:** add a small labelled set per theme and show precision in the UI.
- **Production setup:** serve the frontend as a static build behind nginx with gunicorn, and add pagination and caching to `/api/summary` for large datasets.
