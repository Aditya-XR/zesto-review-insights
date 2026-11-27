"""POST /api/ask: answer a question about the reviews with numbers computed from the database.

The LLM only translates the question into a structured query (which theme, which filters,
how to group). The query runs in Django and the answer sentence is built from its results,
so the LLM never produces a number.
"""
from django.db.models import Count, F, Q
from django.db.models.functions import TruncMonth

from . import llm
from .models import Mention, Review, SubTheme, Theme
from .queries import filter_options, filter_reviews, parse_filters

GROUPS = {
    "theme": "theme__name",
    "sub_theme": "sub_theme__name",
    "city": "review__city",
    "platform": "review__platform",
    "rating": "review__rating",
}

PROMPT = """You turn a product manager's question about app-store reviews into a database query.

Data available: reviews (date, city, platform, star rating 1-5, text), each tagged with zero or
more themes / sub-themes, each with a sentiment (positive, negative, neutral).
Themes and sub-themes:
{taxonomy}
Cities: {cities}. Platforms: {platforms}. Months: {months}.

Question: {question}

Return JSON with these keys (use null when not needed; names must be copied exactly from the lists):
{{"answerable": true or false,
  "reason": "if not answerable: why the review data can't answer it",
  "theme": null or a theme name, "sub_theme": null or a sub-theme name,
  "sentiment": null or "positive" or "negative" or "neutral",
  "city": null or a city, "platform": null or a platform, "month": null or "YYYY-MM",
  "rating": null or a list of star ratings,
  "group_by": null or one of "theme", "sub_theme", "city", "platform", "month", "rating"}}

"complaints", "problems", "issues" mean sentiment negative; "praise", "likes" mean positive.
A question like "what are the top complaints" is group_by "theme" (or "sub_theme" inside a theme)
with sentiment negative. "Which city ..." is group_by "city". "Trend" / "over time" is group_by "month".
Questions about things not in this data (revenue, orders, delivery times in minutes, restaurants by
name, anything before/after the months listed) are not answerable."""


class AskError(Exception):
    pass


def _pick(value, options):
    """Case-insensitive exact match of an LLM-chosen name against what's in the database."""
    if value in (None, "", "null"):
        return None
    for opt in options:
        if str(opt).lower() == str(value).strip().lower():
            return opt
    raise AskError(f"'{value}' isn't in the data")


def _pct(part, whole):
    return f"{round(100 * part / whole)}%" if whole else "0%"


def answer(question: str) -> dict:
    question = (question or "").strip()
    if not question:
        raise AskError("Ask a question first.")
    opts = filter_options()
    themes = {t.name: t for t in Theme.objects.all()}
    taxonomy = "\n".join(
        f"- {t.name}: {', '.join(s.name for s in t.sub_themes.all())}" for t in Theme.objects.prefetch_related("sub_themes")
    )
    spec = llm.chat_json(
        "You reply with JSON only.",
        PROMPT.format(
            taxonomy=taxonomy or "(none yet)",
            cities=", ".join(opts["cities"]),
            platforms=", ".join(opts["platforms"]),
            months=", ".join(opts["months"]),
            question=question,
        ),
    )
    if not spec.get("answerable", True):
        return {
            "answer": f"I can't answer that from these reviews. {spec.get('reason') or ''}".strip(),
            "evidence_review_ids": [],
            "query": spec,
        }

    try:
        theme = themes[_pick(spec.get("theme"), themes)] if spec.get("theme") else None
        sub_names = SubTheme.objects.filter(theme=theme) if theme else SubTheme.objects.all()
        sub_name = _pick(spec.get("sub_theme"), set(sub_names.values_list("name", flat=True)))
        sentiment = _pick(spec.get("sentiment"), ["positive", "negative", "neutral"])
        f = parse_filters(
            {
                "city": _pick(spec.get("city"), opts["cities"]),
                "platform": _pick(spec.get("platform"), opts["platforms"]),
                "month": _pick(spec.get("month"), opts["months"]),
                "rating": ",".join(str(r) for r in (spec.get("rating") or [])),
            }
        )
    except AskError as exc:
        return {"answer": f"I can't answer that from these reviews: {exc}.", "evidence_review_ids": [], "query": spec}
    group_by = spec.get("group_by") if spec.get("group_by") in (*GROUPS, "month") else None

    scope = filter_reviews(Review.objects.filter(analyzed_at__isnull=False), f)
    total = scope.count()
    mentions = filter_reviews(Mention.objects.filter(review__analyzed_at__isnull=False), f, "review__")
    if theme:
        mentions = mentions.filter(theme=theme)
    if sub_name:
        mentions = mentions.filter(sub_theme__name=sub_name)
    if sentiment:
        mentions = mentions.filter(sentiment=sentiment)

    topic = " > ".join(x for x in [theme.name if theme else None, sub_name] if x)
    kind = {"negative": "Complaints", "positive": "Praise", "neutral": "Neutral mentions"}.get(sentiment, "Mentions")
    label = f"{kind} about {topic}" if topic else kind
    where = ", ".join(f"{k} {v if k != 'rating' else '/'.join(map(str, v))}" for k, v in f.items())
    where = f" in {where}" if where else ""

    if group_by:
        if group_by == "month":
            rows = mentions.annotate(g=TruncMonth("review__date")).values("g").annotate(n=Count("review", distinct=True)).order_by("g")
            parts = [f"{r['g']:%Y-%m}: {r['n']}" for r in rows if r["g"]]
        else:
            rows = (
                mentions.values(g=F(GROUPS[group_by]))
                .annotate(n=Count("review", distinct=True), neg=Count("id", filter=Q(sentiment="negative")))
                .order_by("-n")
            )
            parts = [f"{r['g']}: {r['n']}" + ("" if sentiment else f" ({r['neg']} negative)") for r in rows[:8]]
        if not parts:
            text = f"No reviews match: {label.lower()}{where}."
        else:
            text = f"{label}{where}, in number of reviews by {group_by.replace('_', '-')}: " + "; ".join(parts) + "."
    else:
        n = mentions.values("review").distinct().count()
        about = topic or "any theme"
        feeling = f" {sentiment}ly" if sentiment else ""
        text = f"{n} of {total} reviews{where} ({_pct(n, total)}) talk about {about}{feeling}."
        if not sentiment and n:
            neg = mentions.filter(sentiment="negative").values("review").distinct().count()
            pos = mentions.filter(sentiment="positive").values("review").distinct().count()
            text += f" {neg} negative, {pos} positive."

    evidence = list(mentions.order_by("-review__date").values_list("review__review_id", flat=True).distinct()[:5])
    return {"answer": text, "evidence_review_ids": evidence, "query": spec}
