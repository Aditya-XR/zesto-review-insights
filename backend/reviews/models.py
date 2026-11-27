from django.db import models


class Sentiment(models.TextChoices):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class AnalysisRun(models.Model):
    """One CSV import and the theme analysis that follows it.

    The worker process picks up QUEUED runs, so this table doubles as a tiny job queue.
    """

    class Status(models.TextChoices):
        QUEUED = "queued"
        DISCOVERING = "discovering"  # LLM is proposing the theme list
        CLASSIFYING = "classifying"  # LLM is tagging reviews in batches
        DONE = "done"
        FAILED = "failed"
        NEEDS_KEY = "needs_key"  # imported, but no LLM key is configured

    filename = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    total_reviews = models.PositiveIntegerField(default=0)
    processed_reviews = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)
    llm_model = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    @property
    def is_finished(self):
        return self.status in (self.Status.DONE, self.Status.FAILED, self.Status.NEEDS_KEY)


class Review(models.Model):
    review_id = models.CharField(max_length=64, unique=True)  # id from the CSV, e.g. TST-00001
    run = models.ForeignKey(AnalysisRun, on_delete=models.CASCADE, related_name="reviews")
    date = models.DateField(null=True, blank=True, db_index=True)
    city = models.CharField(max_length=100, db_index=True)
    platform = models.CharField(max_length=50, db_index=True)
    rating = models.PositiveSmallIntegerField(null=True, blank=True, db_index=True)
    text = models.TextField()
    # Set once the LLM has looked at the review. Analyzed + no mentions = theme NONE.
    analyzed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "review_id"]

    def __str__(self):
        return self.review_id


class Theme(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    # True when the classifier had to invent it because nothing in the discovered list fitted.
    added_during_classification = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class SubTheme(models.Model):
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="sub_themes")
    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["theme__name", "name"]
        constraints = [models.UniqueConstraint(fields=["theme", "name"], name="uniq_subtheme_per_theme")]

    def __str__(self):
        return f"{self.theme.name} > {self.name}"


class Mention(models.Model):
    """A review talks about one theme, via one sub-theme, with one sentiment."""

    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name="mentions")
    theme = models.ForeignKey(Theme, on_delete=models.CASCADE, related_name="mentions")
    sub_theme = models.ForeignKey(SubTheme, on_delete=models.CASCADE, related_name="mentions")
    sentiment = models.CharField(max_length=10, choices=Sentiment.choices)
    quote = models.TextField(blank=True)  # the part of the review that supports this label

    class Meta:
        constraints = [models.UniqueConstraint(fields=["review", "theme"], name="uniq_theme_per_review")]
        indexes = [models.Index(fields=["theme", "sentiment"])]
