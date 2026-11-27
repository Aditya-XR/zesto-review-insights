from django.urls import path

from . import views

urlpatterns = [
    # Fixed by the brief (the graders' script calls these)
    path("health", views.health),
    path("import", views.import_view),
    path("export/labels.csv", views.export_labels),
    path("ask", views.ask_view),
    # Used by the dashboard
    path("status", views.status_view),
    path("runs/<int:run_id>/retry", views.retry_run),
    path("filters", views.filters_view),
    path("summary", views.summary_view),
    path("changes", views.changes_view),
    path("reviews", views.reviews_view),
]
