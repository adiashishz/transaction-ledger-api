import json


class ErrorAssertions:
    """Mixin for TestCases: asserts the shared `{error: {code, message, details}}` body."""

    def assert_error(self, response, status_code, code):
        self.assertEqual(response.status_code, status_code, response.content)
        self.assertEqual(response["Content-Type"], "application/json")
        body = json.loads(response.content)
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], code)
        return body["error"]
