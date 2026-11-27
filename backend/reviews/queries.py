"""Read-side helpers shared by the dashboard endpoints and /api/ask. All numbers come from here."""
from collections import defaultdict
from datetime import date

from django.db.models import Count, Q
from django.db.models.functions import TruncMonth

from .models import Mention, Review, Theme

REVIEW_FILTERS = ("city", "platform", "month", "rating")


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-09' -> (2026-09-01, 2026-10-01)."""
    y, m = (int(x) for x in month.split("-"))
    return date(y, m, 1), date(y + (m == 12), m % 12 + 1, 1)


def previous_month(month: str) -> str:
    y, m = (int(x) for x in month.split("-"))
    return f"{y - (m == 1)}-{(m - 2) % 12 + 1:02d}"


def parse_filters(params) -> dict:
    """Pull the review filters out of query params (or a dict). Unknown/blank values are dropped."""
    f = {}
    for key in REVIEW_FILTERS:
        value = str(params.get(key) or "").strip()
        if not value:
            continue
        if key == "rating":
            ratings = [int(v) for v in value.split(",") if v.strip().isdigit()]
            if ratings:
                f[key] = ratings
        elif key == "month":
            try:
                month_bounds(value)
                f[key] = value
            except ValueError:
                pass
        else:
            f[key] = value
    return f


def filter_reviews(qs, f: dict, prefix: str = ""):
    """Apply review filters to a Review queryset (prefix='') or Mention queryset (prefix='review__')."""
    if "city" in f:
        qs = qs.filter(**{f"{prefix}city__iexact": f["city"]})
    if "platform" in f:
        qs = qs.filter(**{f"{prefix}platform__iexact": f["platform"]})
    if "rating" in f:
        qs = qs.filter(**{f"{prefix}rating__in": f["rating"]})
    if "month" in f:
        start, end = month_bounds(f["month"])
        qs = qs.filter(**{f"{prefix}date__gte": start, f"{prefix}date__lt": end})
    return qs


def _counts(qs, *group):
    """Mention counts by sentiment, grouped by the given fields."""
    return qs.values(*group).annotate(
        count=Count("id"),
        positive=Count("id", filter=Q(sentiment="positive")),
        negative=Count("id", filter=Q(sentiment="negative")),
        neutral=Count("id", filter=Q(sentiment="neutral")),
    )


def summary(f: dict) -> dict:
    reviews = filter_reviews(Review.objects.all(), f)
    analyzed = reviews.filter(analyzed_at__isnull=False)
    mentions = filter_reviews(Mention.objects.all(), f, "review__")

    by_theme = {r["theme_id"]: r for r in _counts(mentions, "theme_id")}
    by_sub = defaultdict(list)
    for r in _counts(mentions, "theme_id", "sub_theme_id", "sub_theme__name").order_by("-count"):
        by_sub[r["theme_id"]].append(
            {k: r[k] for k in ("count", "positive", "negative", "neutral")} | {"id": r["sub_theme_id"], "name": r["sub_theme__name"]}
        )
    monthly = defaultdict(dict)
    for r in _counts(mentions.annotate(month=TruncMonth("review__date")), "theme_id", "month"):
        if r["month"]:
            monthly[r["theme_id"]][r["month"].strftime("%Y-%m")] = {"count": r["count"], "negative": r["negative"]}
    months = sorted({m for d in monthly.values() for m in d})

    themes = []
    for t in Theme.objects.filter(id__in=by_theme):
        c = by_theme[t.id]
        themes.append(
            {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "added_during_classification": t.added_during_classification,
                **{k: c[k] for k in ("count", "positive", "negative", "neutral")},
                "sub_themes": by_sub[t.id],
                "monthly": [{"month": m, **monthly[t.id].get(m, {"count": 0, "negative": 0})} for m in months],
            }
        )
    themes.sort(key=lambda t: -t["count"])

    analyzed_count = analyzed.count()
    with_mentions = analyzed.filter(mentions__isnull=False).distinct().count()
    return {
        "total_reviews": reviews.count(),
        "analyzed_reviews": analyzed_count,
        "no_theme_reviews": analyzed_count - with_mentions,
        "reviews_with_negative": analyzed.filter(mentions__sentiment="negative").distinct().count(),
        "months": months,
        "themes": themes,
    }


def changes(f: dict) -> dict:
    """What changed: each theme's share of reviews that complain about it, this month vs last.

    Uses shares rather than raw counts so a busier month doesn't look like a worse one.
    """
    f = dict(f)
    month = f.pop("month", None)
    if month is None:
        latest = filter_reviews(Review.objects.exclude(date=None), f).order_by("-date").values_list("date", flat=True).first()
        if latest is None:
            return {"month": None, "previous_month": None, "themes": []}
        month = latest.strftime("%Y-%m")
    prev = previous_month(month)

    def period(m):
        pf = f | {"month": m}
        total = filter_reviews(Review.objects.all(), pf).count()
        rows = {r["theme_id"]: r for r in _counts(filter_reviews(Mention.objects.all(), pf, "review__"), "theme_id")}
        return total, rows

    cur_total, cur = period(month)
    prev_total, prv = period(prev)
    out = []
    for t in Theme.objects.filter(id__in=set(cur) | set(prv)):
        c, p = cur.get(t.id, {}), prv.get(t.id, {})
        cur_share = c.get("negative", 0) / cur_total if cur_total else 0
        prev_share = p.get("negative", 0) / prev_total if prev_total else 0
        out.append(
            {
                "id": t.id,
                "name": t.name,
                "current": {"reviews": c.get("count", 0), "negative": c.get("negative", 0), "negative_share": round(cur_share, 4)},
                "previous": {"reviews": p.get("count", 0), "negative": p.get("negative", 0), "negative_share": round(prev_share, 4)},
                "delta": round(cur_share - prev_share, 4),
            }
        )
    out.sort(key=lambda r: -abs(r["delta"]))
    return {
        "month": month,
        "previous_month": prev,
        "reviews_this_month": cur_total,
        "reviews_previous_month": prev_total,
        "themes": out,
    }


def filter_options() -> dict:
    qs = Review.objects.all()
    months = sorted({d.strftime("%Y-%m") for d in qs.exclude(date=None).dates("date", "month")})
    return {
        "cities": sorted(set(qs.values_list("city", flat=True))),
        "platforms": sorted(set(qs.values_list("platform", flat=True))),
        "months": months,
        "ratings": [1, 2, 3, 4, 5],
    }
