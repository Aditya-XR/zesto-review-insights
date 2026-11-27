"""Parse an uploaded reviews CSV and store it."""
import csv
import io
from datetime import date

from django.db import transaction

from .models import AnalysisRun, Review

REQUIRED_COLUMNS = {"review_id", "date", "city", "platform", "rating", "review_text"}
MAX_ROWS = 1000


class CSVImportError(Exception):
    """The file can't be imported. The message is shown to the user as is."""


def parse_reviews_csv(raw: bytes) -> tuple[list[dict], list[str]]:
    """Return (rows, warnings). Only problems with the whole file raise CSVImportError."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise CSVImportError("The file is not UTF-8 text. Save it as a UTF-8 CSV and try again.")

    reader = csv.DictReader(io.StringIO(text))
    columns = {(c or "").strip().lower() for c in (reader.fieldnames or [])}
    missing = REQUIRED_COLUMNS - columns
    if missing:
        raise CSVImportError(f"Missing column(s): {', '.join(sorted(missing))}.")

    rows, seen, warnings = [], set(), []
    for line_no, raw_row in enumerate(reader, start=2):  # line 1 is the header
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw_row.items() if k}
        rid = row["review_id"]
        if not rid:
            warnings.append(f"line {line_no}: skipped, empty review_id")
            continue
        if rid in seen:
            warnings.append(f"line {line_no}: skipped, duplicate review_id {rid}")
            continue
        # A bad date or rating shouldn't lose the review text, so keep the row with a blank value.
        try:
            row_date = date.fromisoformat(row["date"][:10])
        except ValueError:
            row_date = None
            warnings.append(f"line {line_no}: unreadable date {row['date']!r}")
        try:
            rating = max(1, min(5, int(float(row["rating"]))))
        except ValueError:
            rating = None
            warnings.append(f"line {line_no}: unreadable rating {row['rating']!r}")
        seen.add(rid)
        rows.append(
            {
                "review_id": rid,
                "date": row_date,
                "city": row["city"] or "Unknown",
                "platform": (row["platform"] or "unknown").lower(),
                "rating": rating,
                "text": row["review_text"],
            }
        )
        if len(rows) > MAX_ROWS:
            raise CSVImportError(f"Too many rows: the limit is {MAX_ROWS} reviews per upload.")

    if not rows:
        raise CSVImportError("The file has a header but no reviews.")
    return rows, warnings


@transaction.atomic
def import_reviews(raw: bytes, filename: str, llm_configured: bool) -> tuple[AnalysisRun, list[str]]:
    rows, warnings = parse_reviews_csv(raw)
    existing = set(Review.objects.filter(review_id__in=[r["review_id"] for r in rows]).values_list("review_id", flat=True))
    if existing:
        sample = ", ".join(sorted(existing)[:3])
        raise CSVImportError(f"{len(existing)} review(s) are already imported, e.g. {sample}.")

    run = AnalysisRun.objects.create(
        filename=filename[:255],
        total_reviews=len(rows),
        status=AnalysisRun.Status.QUEUED if llm_configured else AnalysisRun.Status.NEEDS_KEY,
        error="" if llm_configured else "No LLM API key is configured, so themes can't be found yet.",
    )
    Review.objects.bulk_create([Review(run=run, **r) for r in rows])
    return run, warnings
