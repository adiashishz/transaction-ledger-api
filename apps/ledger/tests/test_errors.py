import json

from django.test import RequestFactory, SimpleTestCase

from apps.ledger.exceptions import server_error_handler


class ErrorShapeTests(SimpleTestCase):
    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code)
        self.assertEqual(response["Content-Type"], "application/json")
        body = json.loads(response.content)
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], code)

    def test_unknown_route_returns_json_404(self):
        self.assert_error(self.client.get("/api/does-not-exist"), 404, "NOT_FOUND")

    def test_trailing_slash_is_not_redirected(self):
        self.assert_error(self.client.get("/api/docs/"), 404, "NOT_FOUND")

    def test_server_error_handler_returns_json_500(self):
        response = server_error_handler(RequestFactory().get("/"))
        self.assert_error(response, 500, "INTERNAL_ERROR")


class ApiDocsTests(SimpleTestCase):
    def test_schema_is_served(self):
        self.assertEqual(self.client.get("/api/schema").status_code, 200)

    def test_swagger_ui_is_served(self):
        response = self.client.get("/api/docs")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "swagger")
