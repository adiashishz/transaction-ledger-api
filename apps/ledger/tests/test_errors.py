from unittest import mock

from django.test import RequestFactory, SimpleTestCase, override_settings
from rest_framework.test import APITestCase

from apps.ledger import services
from apps.ledger.exceptions import server_error_handler
from apps.ledger.models import Account
from apps.ledger.tests.helpers import ErrorAssertions


class ErrorShapeTests(ErrorAssertions, SimpleTestCase):
    """Errors raised outside the API views use the same JSON body."""

    def test_unknown_route_returns_json_404(self):
        self.assert_error(self.client.get("/api/does-not-exist"), 404, "NOT_FOUND")

    def test_route_outside_api_returns_json_404(self):
        self.assert_error(self.client.get("/"), 404, "NOT_FOUND")

    def test_trailing_slash_is_not_redirected(self):
        self.assert_error(self.client.get("/api/docs/"), 404, "NOT_FOUND")

    def test_disallowed_host_returns_json_400(self):
        with self.assertLogs("django.security", "ERROR"):
            response = self.client.get("/api/accounts", HTTP_HOST="evil.example")
        self.assert_error(response, 400, "BAD_REQUEST")

    def test_server_error_handler_returns_json_500(self):
        response = server_error_handler(RequestFactory().get("/"))
        self.assert_error(response, 500, "INTERNAL_ERROR")


class ApiErrorTests(ErrorAssertions, APITestCase):
    def setUp(self):
        self.account = Account.objects.create(name="Aarav Sharma")

    def test_method_not_allowed_keeps_allow_header(self):
        response = self.client.delete(f"/api/accounts/{self.account.id}/transactions")
        self.assert_error(response, 405, "METHOD_NOT_ALLOWED")
        self.assertIn("POST", response["Allow"])

    @override_settings(DATA_UPLOAD_MAX_MEMORY_SIZE=1_000)
    def test_oversized_body_returns_json_413(self):
        url = f"/api/accounts/{self.account.id}/transactions"
        payload = {"type": "credit", "amount": "1.00", "reference_id": "BIG", "description": "x" * 2_000}
        self.assert_error(self.client.post(url, payload), 413, "PAYLOAD_TOO_LARGE")
        self.assertFalse(self.account.transactions.exists())

    def test_html_only_client_gets_json_406(self):
        response = self.client.get(f"/api/accounts/{self.account.id}", HTTP_ACCEPT="text/html")
        self.assert_error(response, 406, "NOT_ACCEPTABLE")

    def test_unexpected_error_returns_generic_500_without_leaking_details(self):
        with mock.patch.object(services, "get_account", side_effect=ValueError("password=hunter2")):
            with self.assertLogs("apps.ledger.exceptions", "ERROR") as logs:
                response = self.client.get(f"/api/accounts/{self.account.id}/balance")

        error = self.assert_error(response, 500, "INTERNAL_ERROR")
        self.assertEqual(error, {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred.", "details": {}})
        self.assertNotIn(b"hunter2", response.content)
        self.assertIn("hunter2", "\n".join(logs.output))


class ApiDocsTests(SimpleTestCase):
    def test_schema_is_served(self):
        self.assertEqual(self.client.get("/api/schema").status_code, 200)

    def test_swagger_ui_is_served(self):
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "swagger")
