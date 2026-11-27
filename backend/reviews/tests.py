"""Tests for the parts the graders' script depends on: import, the pipeline's bookkeeping, and the
label export. The LLM is replaced by a fake, so these run offline and without a key:

    docker compose exec backend python manage.py test
"""
import csv
import io
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from . import ask, llm, pipeline
from .models import AnalysisRun, Mention, Review, Theme

CSV_HEADER = "review_id,date,city,platform,rating,review_text\n"
SAMPLE_CSV = CSV_HEADER + (
    'T-1,2026-09-03,Pune,android,1,"Food was cold and the rider was rude."\n'
    "T-2,2026-09-10,Delhi,ios,5,Super quick delivery!\n"
    "T-3,2026-10-01,Pune,android,3,ok\n"
)

TAXONOMY = {
    "themes": [
        {"name": "Delivery", "description": "getting the order", "sub_themes": [{"name": "Delivery Time"}, {"name": "Rider Behaviour"}]},
        {"name": "Food Quality", "description": "the food", "sub_themes": [{"name": "Temperature"}]},
    ]
}
CLASSIFICATION = {
    "results": [
        {
            "id": "T-1",
            "topics": [
                {"theme": "food quality", "sub_theme": "temperature", "sentiment": "negative", "quote": "Food was cold"},
                {"theme": "Delivery", "sub_theme": "Rider Behaviour", "sentiment": "NEGATIVE"},
                {"theme": "Delivery", "sub_theme": "Delivery Time", "sentiment": "negative"},  # same theme twice
            ],
        },
        {"id": "T-2", "topics": [{"theme": "Delivery", "sub_theme": "Delivery Time", "sentiment": "positive"}]},
        {"id": "T-3", "topics": []},
    ]
}


def fake_llm(system, user, **kwargs):
    """Answers the discovery prompt with TAXONOMY and the classification prompt with CLASSIFICATION."""
    return TAXONOMY if "Build the list of THEMES" in user else CLASSIFICATION


def upload(client, text, name="reviews.csv"):
    return client.post("/api/import", {"file": SimpleUploadedFile(name, text.encode(), content_type="text/csv")})


class WithKey(TestCase):
    """Pretends a Gemini key is set (for setUp too, which class-level mock.patch doesn't cover)."""

    def setUp(self):
        patcher = mock.patch.dict("os.environ", {"GEMINI_API_KEY": "test-key"})
        patcher.start()
        self.addCleanup(patcher.stop)


class ImportTests(WithKey):
    def test_import_stores_reviews_and_queues_a_run(self):
        resp = upload(self.client, SAMPLE_CSV)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(Review.objects.count(), 3)
        run = AnalysisRun.objects.get()
        self.assertEqual((run.status, run.total_reviews), (AnalysisRun.Status.QUEUED, 3))
        r = Review.objects.get(review_id="T-1")
        self.assertEqual((r.city, r.platform, r.rating, str(r.date)), ("Pune", "android", 1, "2026-09-03"))

    def test_missing_column_is_rejected(self):
        resp = upload(self.client, "review_id,date,review_text\nT-1,2026-09-01,hi\n")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("city", resp.json()["error"])
        self.assertFalse(Review.objects.exists())

    def test_bad_rows_keep_the_review_or_are_reported(self):
        text = CSV_HEADER + "T-1,not-a-date,Pune,android,x,hello\nT-1,2026-09-01,Pune,android,2,dup\n"
        resp = upload(self.client, text)
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(len(resp.json()["warnings"]), 3)  # bad date, bad rating, duplicate id
        r = Review.objects.get()
        self.assertIsNone(r.date)
        self.assertIsNone(r.rating)

    def test_reimporting_the_same_ids_is_rejected(self):
        upload(self.client, SAMPLE_CSV)
        self.assertEqual(upload(self.client, SAMPLE_CSV).status_code, 400)
        self.assertEqual(Review.objects.count(), 3)

    def test_missing_file_field(self):
        self.assertEqual(self.client.post("/api/import", {}).status_code, 400)


@mock.patch("reviews.llm.chat_json", side_effect=fake_llm)
class PipelineAndExportTests(WithKey):
    def setUp(self):
        super().setUp()
        upload(self.client, SAMPLE_CSV)
        self.run = AnalysisRun.objects.get()

    def test_export_is_202_until_themes_are_done(self, _llm):
        self.assertEqual(self.client.get("/api/export/labels.csv").status_code, 202)
        pipeline.process_run(self.run)
        self.assertEqual(self.client.get("/api/export/labels.csv").status_code, 200)

    def test_export_rows(self, _llm):
        pipeline.process_run(self.run)
        resp = self.client.get("/api/export/labels.csv")
        rows = list(csv.reader(io.StringIO(resp.content.decode())))
        self.assertEqual(rows[0], ["review_id", "theme", "sub_theme", "sentiment"])
        self.assertEqual(
            rows[1:],
            [
                # one row per (review, theme): the second Delivery topic of T-1 is dropped
                ["T-1", "Delivery", "Rider Behaviour", "negative"],
                ["T-1", "Food Quality", "Temperature", "negative"],
                ["T-2", "Delivery", "Delivery Time", "positive"],
                ["T-3", "NONE", "NONE", "neutral"],
            ],
        )

    def test_names_from_the_llm_are_matched_case_insensitively(self, _llm):
        pipeline.process_run(self.run)
        self.assertEqual(sorted(Theme.objects.values_list("name", flat=True)), ["Delivery", "Food Quality"])
        self.run.refresh_from_db()
        self.assertEqual((self.run.status, self.run.processed_reviews), (AnalysisRun.Status.DONE, 3))

    def test_classifier_can_add_a_theme_the_discovery_missed(self, llm_mock):
        new_topic = {"results": [{"id": "T-3", "topics": [{"theme": "Hygiene", "sub_theme": "Dirty Kitchen", "sentiment": "negative", "new": True}]}]}
        llm_mock.side_effect = lambda s, u, **k: TAXONOMY if "Build the list" in u else new_topic
        pipeline.process_run(self.run)
        theme = Theme.objects.get(name="Hygiene")
        self.assertTrue(theme.added_during_classification)
        self.assertEqual(Mention.objects.get(review__review_id="T-3").theme, theme)

    def test_failed_batches_are_retried_then_reported(self, llm_mock):
        def rate_limited(system, user, **kwargs):
            if "Build the list" in user:
                return TAXONOMY
            raise llm.LLMError("HTTP 429")

        llm_mock.side_effect = rate_limited
        pipeline.process_run(self.run)
        self.run.refresh_from_db()
        self.assertEqual(self.run.status, AnalysisRun.Status.FAILED)
        self.assertIn("429", self.run.error)

    def test_summary_counts_and_filters(self, _llm):
        pipeline.process_run(self.run)
        data = self.client.get("/api/summary").json()
        delivery = next(t for t in data["themes"] if t["name"] == "Delivery")
        self.assertEqual((delivery["count"], delivery["positive"], delivery["negative"]), (2, 1, 1))
        self.assertEqual(data["no_theme_reviews"], 1)
        pune = self.client.get("/api/summary?city=Pune").json()
        self.assertEqual(next(t for t in pune["themes"] if t["name"] == "Delivery")["count"], 1)

    def test_reviews_drill_down(self, _llm):
        pipeline.process_run(self.run)
        theme = Theme.objects.get(name="Delivery")
        data = self.client.get(f"/api/reviews?theme={theme.id}&sentiment=negative").json()
        self.assertEqual([r["review_id"] for r in data["results"]], ["T-1"])
        self.assertEqual(len(data["results"][0]["mentions"]), 2)

    def test_ask_computes_numbers_from_the_database(self, llm_mock):
        pipeline.process_run(self.run)
        llm_mock.side_effect = lambda s, u, **k: {"answerable": True, "theme": "delivery", "sentiment": "negative", "city": "pune"}
        result = ask.answer("How many delivery complaints in Pune?")
        self.assertTrue(result["answer"].startswith("1 of 2 reviews"))
        self.assertEqual(result["evidence_review_ids"], ["T-1"])

    def test_ask_says_when_it_cannot_answer(self, llm_mock):
        pipeline.process_run(self.run)
        llm_mock.side_effect = lambda s, u, **k: {"answerable": True, "city": "Kolkata"}
        self.assertIn("can't answer", ask.answer("Kolkata?")["answer"])


class NoKeyTests(TestCase):
    @mock.patch.dict("os.environ", {"GEMINI_API_KEY": "", "OPENAI_API_KEY": "", "GROQ_API_KEY": ""})
    def test_app_works_without_a_key(self):
        self.assertFalse(self.client.get("/api/health").json()["llm_configured"])
        resp = upload(self.client, SAMPLE_CSV)
        self.assertEqual(resp.status_code, 202)
        self.assertIsNotNone(resp.json()["llm_message"])
        self.assertEqual(AnalysisRun.objects.get().status, AnalysisRun.Status.NEEDS_KEY)
        self.assertEqual(self.client.get("/api/export/labels.csv").status_code, 503)
        self.assertEqual(self.client.post("/api/ask", {"question": "hi"}, content_type="application/json").status_code, 503)
