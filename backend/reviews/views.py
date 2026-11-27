import csv
import io

from django.db import connection
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from . import ask as ask_module
from . import llm, queries
from .importer import CSVImportError, import_reviews
from .models import AnalysisRun, Mention, Review

NO_KEY_MESSAGE = (
    "No LLM API key is configured, so themes can't be found. Set GEMINI_API_KEY (or OPENAI_API_KEY / "
    "GROQ_API_KEY) in .env and restart with `docker compose up`."
)


def run_json(run: AnalysisRun | None):
    if run is None:
        return None
    return {
        "id": run.id,
        "filename": run.filename,
        "status": run.status,
        "total_reviews": run.total_reviews,
        "processed_reviews": run.processed_reviews,
        "error": run.error,
        "llm_model": run.llm_model,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


@api_view(["GET"])
def health(request):
    connection.ensure_connection()
    return Response({"status": "ok", "llm_configured": llm.is_configured()})


@api_view(["GET"])
def status_view(request):
    cfg = llm.get_config()
    return Response(
        {
            "llm_configured": cfg is not None,
            "llm_provider": cfg.key_name if cfg else None,
            "llm_model": cfg.model if cfg else None,
            "llm_message": None if cfg else NO_KEY_MESSAGE,
            "review_count": Review.objects.count(),
            "latest_run": run_json(AnalysisRun.objects.first()),
        }
    )


@api_view(["POST"])
@parser_classes([MultiPartParser, FormParser])
def import_view(request):
    upload = request.FILES.get("file")
    if upload is None:
        return Response({"error": "Upload a CSV in a form field named 'file'."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        run, warnings = import_reviews(upload.read(), upload.name, llm.is_configured())
    except CSVImportError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response(
        {"run": run_json(run), "imported": run.total_reviews, "warnings": warnings, "llm_message": None if llm.is_configured() else NO_KEY_MESSAGE},
        status=status.HTTP_202_ACCEPTED,  # accepted: themes are found in the background
    )


@api_view(["POST"])
def retry_run(request, run_id):
    run = get_object_or_404(AnalysisRun, pk=run_id)
    if not llm.is_configured():
        return Response({"error": NO_KEY_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    if run.status not in (AnalysisRun.Status.FAILED, AnalysisRun.Status.NEEDS_KEY):
        return Response({"error": f"Run is {run.status}; only failed runs can be retried."}, status=status.HTTP_409_CONFLICT)
    run.status, run.error, run.finished_at = AnalysisRun.Status.QUEUED, "", None
    run.save(update_fields=["status", "error", "finished_at"])
    return Response({"run": run_json(run)}, status=status.HTTP_202_ACCEPTED)


def labels_csv() -> str:
    """One row per (review, theme). Reviews with no theme get a NONE row."""
    by_review = {}
    for m in Mention.objects.select_related("theme", "sub_theme", "review").order_by("review__review_id", "theme__name"):
        by_review.setdefault(m.review.review_id, []).append((m.theme.name, m.sub_theme.name, m.sentiment))
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["review_id", "theme", "sub_theme", "sentiment"])
    for rid in Review.objects.order_by("review_id").values_list("review_id", flat=True):
        for row in by_review.get(rid) or [("NONE", "NONE", "neutral")]:
            w.writerow([rid, *row])
    return out.getvalue()


@api_view(["GET"])
def export_labels(request):
    pending = AnalysisRun.objects.exclude(status=AnalysisRun.Status.DONE)
    if not AnalysisRun.objects.exists():
        return Response({"status": "empty", "detail": "No reviews imported yet."}, status=status.HTTP_404_NOT_FOUND)
    if pending.filter(status__in=[AnalysisRun.Status.QUEUED, AnalysisRun.Status.DISCOVERING, AnalysisRun.Status.CLASSIFYING]).exists():
        run = pending.first()
        return Response(
            {"status": run.status, "detail": f"Still finding themes: {run.processed_reviews}/{run.total_reviews} reviews done."},
            status=status.HTTP_202_ACCEPTED,
        )
    if pending.filter(status=AnalysisRun.Status.NEEDS_KEY).exists():
        return Response({"status": "needs_key", "detail": NO_KEY_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    # Done, or failed part-way: export what we have (unanalysed reviews come out as NONE).
    resp = HttpResponse(labels_csv(), content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = 'attachment; filename="labels.csv"'
    return resp


@api_view(["GET"])
def filters_view(request):
    return Response(queries.filter_options())


@api_view(["GET"])
def summary_view(request):
    return Response(queries.summary(queries.parse_filters(request.query_params)))


@api_view(["GET"])
def changes_view(request):
    return Response(queries.changes(queries.parse_filters(request.query_params)))


@api_view(["GET"])
def reviews_view(request):
    p = request.query_params
    qs = queries.filter_reviews(Review.objects.all(), queries.parse_filters(p))
    mention_filter = {}
    if p.get("theme"):
        mention_filter["theme_id"] = p["theme"]
    if p.get("sub_theme"):
        mention_filter["sub_theme_id"] = p["sub_theme"]
    if p.get("sentiment"):
        mention_filter["sentiment"] = p["sentiment"]
    if mention_filter:
        qs = qs.filter(mentions__in=Mention.objects.filter(**mention_filter))
    if p.get("no_theme") == "1":
        qs = qs.filter(analyzed_at__isnull=False, mentions__isnull=True)
    if p.get("q"):
        qs = qs.filter(text__icontains=p["q"])
    if p.get("ids"):
        qs = qs.filter(review_id__in=p["ids"].split(","))
    qs = qs.distinct().order_by("-date", "review_id")

    page_size = 25
    try:
        page = max(1, int(p.get("page", 1)))
    except ValueError:
        page = 1
    total = qs.count()
    items = list(qs.prefetch_related("mentions__theme", "mentions__sub_theme")[(page - 1) * page_size : page * page_size])
    return Response(
        {
            "count": total,
            "page": page,
            "page_size": page_size,
            "results": [
                {
                    "review_id": r.review_id,
                    "date": r.date,
                    "city": r.city,
                    "platform": r.platform,
                    "rating": r.rating,
                    "text": r.text,
                    "analyzed": r.analyzed_at is not None,
                    "mentions": [
                        {"theme_id": m.theme_id, "theme": m.theme.name, "sub_theme": m.sub_theme.name, "sentiment": m.sentiment, "quote": m.quote}
                        for m in r.mentions.all()
                    ],
                }
                for r in items
            ],
        }
    )


@api_view(["POST"])
def ask_view(request):
    if not llm.is_configured():
        return Response({"error": NO_KEY_MESSAGE}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        return Response(ask_module.answer(str(request.data.get("question", ""))))
    except ask_module.AskError as exc:
        return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except llm.LLMError as exc:
        return Response({"error": f"The LLM call failed: {exc}"}, status=status.HTTP_502_BAD_GATEWAY)
