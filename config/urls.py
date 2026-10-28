from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("api/schema", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("api/", include("apps.ledger.urls")),
]

# Errors raised outside DRF views (unknown routes, crashes) use the same JSON shape.
handler400 = "apps.ledger.exceptions.bad_request_handler"
handler404 = "apps.ledger.exceptions.not_found_handler"
handler500 = "apps.ledger.exceptions.server_error_handler"
