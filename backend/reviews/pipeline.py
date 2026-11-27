"""Theme finding, in two LLM stages:

1. discover: read (a sample of) the run's reviews and propose a theme list with sub-themes.
   Nothing is hard-coded, so new topics in new data become new themes.
2. classify: tag every review, in batches, with the theme(s)/sub-theme it talks about and the
   sentiment for each. A review that fits nothing gets no mentions (exported as NONE). The
   classifier may also add a theme when a review is clearly about something the list missed.

Runs in the worker process (see management/commands/run_worker.py), never inside a request.
"""
import json
import logging
import os
import random
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.db import transaction
from django.utils import timezone

from . import llm
from .models import AnalysisRun, Mention, Review, Sentiment, SubTheme, Theme

log = logging.getLogger(__name__)

DISCOVERY_SAMPLE = 400  # reviews shown to the LLM when proposing themes
BATCH_SIZE = int(os.environ.get("LLM_BATCH_SIZE", "25"))
CONCURRENCY = int(os.environ.get("LLM_CONCURRENCY", "3"))
CLASSIFY_PASSES = 3

SYSTEM = (
    "You are a customer-insights analyst at a food-delivery app. You read app-store reviews "
    "and organise them into themes a product manager can act on. You reply with JSON only."
)

DISCOVER_PROMPT = """Below are {n} customer reviews, one per line as `rating | text`.

Build the list of THEMES these customers talk about, each with SUB-THEMES.

Rules:
- A theme is a broad part of the service that one team owns. In a typical set of reviews each
  theme covers roughly 5-30% of the reviews. A sub-theme is the specific issue inside it.
- Only include topics these reviews actually mention. If a topic is mentioned by at least ~2% of
  reviews and does not belong under another theme, give it its own theme, even if it is unusual.
- Themes must not overlap: any single complaint or compliment should clearly belong to exactly one
  sub-theme. Prefer 1-4 sub-themes per theme.
- One theme = one topic. Never join two different topics in one theme (a name with "and", "&" or
  a comma is a warning sign): if two problems would be fixed by different teams, for example how
  the food is packed vs. whether the right items were sent, they are separate themes.
- Name themes by topic, never by feeling: no "Complaints", "Praise", "Overall Experience".
  General praise or criticism of the app itself belongs under the theme about the app.
- Short Title Case names (1-3 words), e.g. "Delivery", "Customer Support".
{existing}
Return JSON: {{"themes": [{{"name": "...", "description": "one sentence: what belongs here",
"sub_themes": [{{"name": "...", "description": "..."}}]}}]}}

Reviews:
{reviews}"""

CLASSIFY_PROMPT = """Theme list (Theme > Sub-theme: what belongs there):
{taxonomy}

Tag each review below with EVERY theme it talks about.
- A review can have zero, one or several themes. Use only the single best sub-theme per theme.
- Only tag what the review actually says. Filler like "Uninstalling", "Sigh", "Happy customer",
  "Ordered pizza on Friday" or "Will order again" is not a theme on its own.
- Sentiment is per theme and comes from what the review says about THAT theme, not the star
  rating: "food great but late" = food positive, delivery negative. Use "neutral" only for a
  theme mentioned without any opinion.
- Reviews with no real content ("ok", "hmm", "👍", "asdf", ".") get an empty topics list.
- Reviews may be in Hinglish or have typos; read them for meaning.
- Use the theme and sub-theme names exactly as written above. Only if a review is clearly about
  something none of them covers, invent a new short theme and sub-theme and set "new": true.

Return JSON: {{"results": [{{"id": "<review id>", "topics": [{{"theme": "...", "sub_theme": "...",
"sentiment": "positive|negative|neutral", "quote": "the few words of the review that show it",
"new": false}}]}}]}}

Reviews (JSON lines):
{reviews}"""


# ---------------------------------------------------------------------------------------------
# Stage 1: discovery


def _clean_name(name) -> str:
    return " ".join(str(name or "").split())[:100]


def discover_themes(reviews: list[Review]) -> list[Theme]:
    sample = reviews if len(reviews) <= DISCOVERY_SAMPLE else random.Random(0).sample(reviews, DISCOVERY_SAMPLE)
    existing = list(Theme.objects.prefetch_related("sub_themes"))
    existing_txt = ""
    if existing:
        lines = [f"- {t.name}: {', '.join(s.name for s in t.sub_themes.all())}" for t in existing]
        existing_txt = (
            "- Themes already in use from earlier uploads (reuse these exact names when they fit, "
            "add new ones only for new topics):\n" + "\n".join(lines) + "\n"
        )
    lines = [f"{r.rating or '-'} | {' '.join(r.text.split())}" for r in sample]
    data = llm.chat_json(SYSTEM, DISCOVER_PROMPT.format(n=len(sample), existing=existing_txt, reviews="\n".join(lines)))
    return save_taxonomy(data.get("themes") or [])


def save_taxonomy(themes_json: list[dict]) -> list[Theme]:
    """Create themes/sub-themes from the LLM's proposal, reusing names case-insensitively."""
    saved = []
    for t in themes_json:
        name = _clean_name(t.get("name"))
        if not name or name.upper() == "NONE":
            continue
        theme = Theme.objects.filter(name__iexact=name).first()
        if theme is None:
            theme = Theme.objects.create(name=name, description=str(t.get("description") or "")[:500])
        for s in t.get("sub_themes") or []:
            sub_name = _clean_name(s.get("name") if isinstance(s, dict) else s)
            if sub_name and not theme.sub_themes.filter(name__iexact=sub_name).exists():
                desc = s.get("description", "") if isinstance(s, dict) else ""
                SubTheme.objects.create(theme=theme, name=sub_name, description=str(desc)[:500])
        if not theme.sub_themes.exists():
            SubTheme.objects.create(theme=theme, name=theme.name, description=theme.description)
        saved.append(theme)
    return saved


# ---------------------------------------------------------------------------------------------
# Stage 2: classification


def taxonomy_text() -> str:
    lines = []
    for t in Theme.objects.prefetch_related("sub_themes"):
        for s in t.sub_themes.all():
            lines.append(f"- {t.name} > {s.name}: {s.description or t.description}")
    return "\n".join(lines)


def classify_batch(batch: list[Review], taxonomy: str) -> dict[str, list[dict]]:
    """Call the LLM for one batch. Returns {review_id: [topic, ...]}. No database access here,
    because this runs in worker threads."""
    lines = [json.dumps({"id": r.review_id, "rating": r.rating, "text": r.text}, ensure_ascii=False) for r in batch]
    data = llm.chat_json(SYSTEM, CLASSIFY_PROMPT.format(taxonomy=taxonomy, reviews="\n".join(lines)))
    out = {}
    for item in data.get("results") or []:
        if isinstance(item, dict) and item.get("id"):
            out[str(item["id"]).strip()] = [t for t in (item.get("topics") or []) if isinstance(t, dict)]
    return out


def _normalise_sentiment(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in Sentiment.values else Sentiment.NEUTRAL


class TaxonomyResolver:
    """Maps names the LLM returned to Theme/SubTheme rows, creating new ones when needed."""

    def __init__(self):
        self.themes = {t.name.lower(): t for t in Theme.objects.all()}
        self.subs = {(s.theme_id, s.name.lower()): s for s in SubTheme.objects.select_related("theme")}
        self.sub_by_name = {}
        for s in self.subs.values():
            self.sub_by_name.setdefault(s.name.lower(), s)

    def resolve(self, theme_name: str, sub_name: str) -> SubTheme | None:
        theme_name, sub_name = _clean_name(theme_name), _clean_name(sub_name)
        if not theme_name or theme_name.upper() == "NONE":
            return None
        theme = self.themes.get(theme_name.lower())
        if theme is None:
            # The LLM sometimes returns only the sub-theme in the theme slot.
            known = self.sub_by_name.get(theme_name.lower())
            if known:
                return known
            theme = Theme.objects.create(name=theme_name, added_during_classification=True)
            self.themes[theme_name.lower()] = theme
        sub_name = sub_name if sub_name and sub_name.upper() != "NONE" else theme.name
        sub = self.subs.get((theme.id, sub_name.lower()))
        if sub is None:
            sub = SubTheme.objects.create(theme=theme, name=sub_name)
            self.subs[(theme.id, sub_name.lower())] = sub
            self.sub_by_name.setdefault(sub_name.lower(), sub)
        return sub


@transaction.atomic
def save_classification(batch: list[Review], results: dict[str, list[dict]], resolver: TaxonomyResolver) -> None:
    now = timezone.now()
    mentions = []
    for review in batch:
        seen_themes = set()
        for topic in results.get(review.review_id, []):
            sub = resolver.resolve(topic.get("theme"), topic.get("sub_theme"))
            if sub is None or sub.theme_id in seen_themes:
                continue
            seen_themes.add(sub.theme_id)
            mentions.append(
                Mention(
                    review=review,
                    theme_id=sub.theme_id,
                    sub_theme=sub,
                    sentiment=_normalise_sentiment(topic.get("sentiment")),
                    quote=str(topic.get("quote") or "")[:500],
                )
            )
    Mention.objects.filter(review__in=batch).delete()  # makes a retried batch idempotent
    Mention.objects.bulk_create(mentions)
    Review.objects.filter(pk__in=[r.pk for r in batch]).update(analyzed_at=now)


# ---------------------------------------------------------------------------------------------
# Orchestration


def _set(run: AnalysisRun, **fields) -> None:
    for k, v in fields.items():
        setattr(run, k, v)
    run.save(update_fields=list(fields))


def process_run(run: AnalysisRun) -> None:
    cfg = llm.get_config()
    if cfg is None:
        _set(run, status=AnalysisRun.Status.NEEDS_KEY, error="No LLM API key is configured.", finished_at=timezone.now())
        return
    _set(run, llm_model=cfg.model, started_at=run.started_at or timezone.now(), error="")

    reviews = list(run.reviews.order_by("review_id"))
    try:
        if not run.reviews.filter(analyzed_at__isnull=False).exists():  # skip on resume
            _set(run, status=AnalysisRun.Status.DISCOVERING)
            themes = discover_themes(reviews)
            log.info("run %s: discovered %d themes", run.pk, len(themes))
            if not Theme.objects.exists():
                raise llm.LLMError("The LLM did not propose any themes.")
    except llm.LLMError as exc:
        _set(run, status=AnalysisRun.Status.FAILED, error=str(exc), finished_at=timezone.now())
        return

    _set(run, status=AnalysisRun.Status.CLASSIFYING)
    # Free tiers fail now and then even after backoff, so make a few passes over what's left.
    failures = []
    for _ in range(CLASSIFY_PASSES):
        failures = classify_pending(run)
        if not failures:
            break
    if failures:
        _set(
            run,
            status=AnalysisRun.Status.FAILED,
            error=f"{len(failures)} batch(es) still failing after {CLASSIFY_PASSES} passes. Last error: {failures[-1]}",
            finished_at=timezone.now(),
        )
    else:
        _set(run, status=AnalysisRun.Status.DONE, finished_at=timezone.now())


def classify_pending(run: AnalysisRun) -> list[str]:
    """Classify the run's not-yet-analyzed reviews. Returns the errors of batches that failed."""
    reviews = list(run.reviews.order_by("review_id"))
    todo = [r for r in reviews if r.analyzed_at is None]
    _set(run, processed_reviews=len(reviews) - len(todo))
    batches = [todo[i : i + BATCH_SIZE] for i in range(0, len(todo), BATCH_SIZE)]
    taxonomy = taxonomy_text()
    resolver = TaxonomyResolver()
    failures = []

    with ThreadPoolExecutor(max_workers=max(1, CONCURRENCY)) as pool:
        futures = {pool.submit(classify_batch, b, taxonomy): b for b in batches}
        for fut in as_completed(futures):
            batch = futures[fut]
            try:
                results = fut.result()
            except llm.LLMError as exc:
                log.error("run %s: batch failed: %s", run.pk, exc)
                failures.append(str(exc))
                continue
            if not results:
                failures.append("the LLM returned no results for a batch")
                continue
            missing = [r for r in batch if r.review_id not in results]
            if missing:
                # The model skipped some reviews: retry just those once.
                try:
                    results.update(classify_batch(missing, taxonomy))
                except llm.LLMError as exc:
                    log.warning("run %s: retry of %d skipped reviews failed: %s", run.pk, len(missing), exc)
            save_classification(batch, results, resolver)
            _set(run, processed_reviews=run.processed_reviews + len(batch))
    return failures
